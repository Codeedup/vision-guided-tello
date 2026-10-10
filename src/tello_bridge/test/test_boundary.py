"""Hardware-free failure contracts for authority, transport and telemetry."""

from dataclasses import replace
import itertools
import socket
from unittest.mock import Mock, patch

import pytest
from tello_bridge.arbiter import Arbiter
from tello_bridge.policy import (
    Authority,
    Command,
    FRESH_NS,
    Limits,
    sdk_integer,
    Telemetry,
)
from tello_bridge.telemetry import parse_state
from tello_bridge.transport import (
    DiagnosticChannel,
    FakeTransport,
    OwnerLock,
    UdpSdkTransport,
)

EPOCH = 1_800_000_000_000_000_000


def prepared():
    guard = Authority()
    guard.link(True)
    guard.flight_state = 'HOVER_CONFIRMED'
    guard.telemetry = Telemetry(0, 80, 70)
    assert guard.accept(Command(EPOCH, None, False, False, False, 0, 0), EPOCH, 0)
    assert guard.arm(EPOCH, 0)
    return guard


def moving(guard, offset=1, source=None, **changes):
    command = Command(EPOCH + offset, source or EPOCH + offset, True, True, False, 4.5, -3.5)
    command = replace(command, **changes)
    return guard.accept(command, EPOCH + offset, offset)


def test_restart_enabled_data_and_reconnect_cannot_arm():
    guard = Authority()
    guard.link(True)
    assert moving(guard)
    assert not guard.armed
    assert guard.action(EPOCH + 1, 1)[0] == 'yield'
    guard = prepared()
    moving(guard)
    guard.link(False)
    guard.link(True)
    moving(guard, 2)
    assert not guard.armed
    assert guard.flight_state == 'UNKNOWN'
    assert not guard.arm(EPOCH + 2, 2)


@pytest.mark.parametrize('enabled,tracking,land', itertools.product((False, True), repeat=3))
def test_every_flag_combination(enabled, tracking, land):
    guard = Authority()
    guard.link(True)
    expected = not (tracking and (not enabled or land)) and not (land and not enabled)
    cmd = Command(EPOCH, EPOCH, enabled, tracking, land, 0, 0)
    assert guard.accept(cmd, EPOCH, 0) == expected
    if land and expected:
        assert guard.landing_latched


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf'), 10.01, -10.01,
                                   True, '1', None])
def test_invalid_scalar_neutralizes_cached_motion(value):
    guard = prepared()
    assert moving(guard)
    assert not moving(guard, 2, lateral=value)
    assert guard.action(EPOCH + 2, 2) == ('rc', (0, 0, 0, 0))


@pytest.mark.parametrize('age,valid', [(0, True), (FRESH_NS - 1, True),
                                       (FRESH_NS, False), (-1, False)])
def test_source_freshness_boundary(age, valid):
    guard = Authority()
    command = Command(EPOCH, EPOCH - age, True, True, False, 1, 1)
    assert guard.accept(command, EPOCH, 0) == valid


@pytest.mark.parametrize('age,valid', [(0, True), (FRESH_NS - 1, True),
                                       (FRESH_NS, False), (-1, False)])
def test_decision_freshness_boundary(age, valid):
    guard = Authority()
    command = Command(EPOCH - age, None, False, False, False, 0, 0)
    assert guard.accept(command, EPOCH, 0) == valid


def test_heartbeat_never_renews_old_source():
    guard = prepared()
    assert moving(guard, source=EPOCH)
    for offset in (100_000_000, 200_000_000):
        assert guard.heartbeat(offset)
        assert moving(guard, offset, source=EPOCH)
        assert guard.action(EPOCH + offset, offset)[1] == (5, 0, -4, 0)
    assert guard.heartbeat(FRESH_NS)
    assert not moving(guard, FRESH_NS, source=EPOCH)
    assert guard.action(EPOCH + FRESH_NS, FRESH_NS)[1] == (0, 0, 0, 0)


def test_final_transmission_checks_decision_source_and_monotonic_receipt():
    guard = prepared()
    moving(guard)
    guard.heartbeat(249_999_999)
    assert guard.action(EPOCH + 249_999_999, 249_999_999)[1] == (5, 0, -4, 0)
    guard.heartbeat(250_000_001)
    assert guard.action(EPOCH + 250_000_001, 250_000_001)[1] == (0, 0, 0, 0)


def test_replay_and_reorder_do_not_refresh_receipt():
    guard = prepared()
    moving(guard, 10)
    receipt = guard.receipt_ns
    assert not moving(guard, 10)
    assert guard.receipt_ns == receipt
    assert not moving(guard, 9)
    assert not moving(guard, 11, source=EPOCH + 8)
    assert guard.command is None


def test_land_ignores_observation_freshness_but_validates_decision():
    for source in (None, 1):
        guard = prepared()
        assert guard.accept(Command(EPOCH + 1, source, True, False, True, 0, 0), EPOCH + 1, 1)
        assert guard.action(EPOCH + 1, 1)[0] == 'land'
        guard.disarm('MANUAL_TAKEOVER')
        assert guard.landing_latched
        assert not guard.arm(EPOCH + 2, 2)
        assert guard.flight_state == 'LANDING_REQUESTED'


def test_nontracking_must_be_neutral():
    guard = prepared()
    assert not moving(guard, tracking=False)
    assert guard.command is None


@pytest.mark.parametrize('battery,height', [(None, 70), (80, None), (24.9, 70),
                                            (101, 70), (80, 29), (80, 151),
                                            (float('nan'), 70)])
def test_telemetry_interlocks(battery, height):
    guard = prepared()
    guard.telemetry = Telemetry(0, battery, height)
    assert guard.action(EPOCH + 1, 1)[0] == 'land'
    assert not guard.armed


def test_stale_telemetry_and_missing_hover_prevent_arm():
    for mutate in ('stale', 'missing', 'unknown'):
        guard = prepared()
        guard.disarm()
        if mutate == 'stale':
            guard.telemetry = Telemetry(-500_000_000, 80, 70)
        elif mutate == 'missing':
            guard.telemetry = None
        else:
            guard.flight_state = 'UNKNOWN'
        assert not guard.arm(EPOCH, 0)


@pytest.mark.parametrize('ros_delta,mono_delta', [(-1, 1), (1, -1), (100_000_000, 1)])
def test_clock_fault_latches_land(ros_delta, mono_delta):
    guard = prepared()
    assert not guard.clock_ok(EPOCH + ros_delta, mono_delta)
    assert not guard.armed
    assert guard.landing_latched


def test_operator_lease_loss_cannot_be_revived_by_late_heartbeat():
    guard = prepared()
    assert not guard.heartbeat(300_000_000)
    assert guard.landing_latched
    assert not guard.armed


def test_approved_input_loss_independent_deadline():
    guard = prepared()
    moving(guard)
    for ns in range(100_000_000, 2_000_000_001, 100_000_000):
        guard.telemetry = Telemetry(ns, 80, 70)
        guard.heartbeat(ns)
        guard.action(EPOCH + ns, ns)
    assert guard.landing_latched
    assert guard.reason == 'APPROVED_INPUT_LOSS'


def test_landing_retries_bounded_even_when_sdk_stuck():
    guard = prepared()
    transport = FakeTransport(never_reply=True)
    transport.start()
    arbiter = Arbiter(transport, guard)
    guard.request_land()
    for ns in range(0, 6_000_000_000, 20_000_000):
        arbiter.tick(EPOCH + ns, ns)
    assert sum(event.get('attempt') == 'land' for event in transport.events) == 3
    arbiter.takeover(6_000_000_000)
    assert guard.landing_latched
    arbiter.shutdown(6_000_000_001)
    assert sum(event.get('attempt') == 'land' for event in transport.events) == 3


def test_delayed_reply_cannot_block_takeover_or_prove_landing():
    transport = FakeTransport(reply_delay_ns=5_000_000_000)
    transport.start()
    guard = prepared()
    arbiter = Arbiter(transport, guard)
    guard.request_land()
    arbiter.tick(EPOCH, 0)
    arbiter.takeover(1)
    assert not guard.armed
    assert guard.landing_latched
    transport.poll(5_000_000_000)
    assert guard.flight_state == 'LANDING_REQUESTED'
    assert transport.events[-1]['acknowledgement'] == 'SIMULATED_ONLY'


def test_fault_reconnect_has_no_cached_motion():
    transport = FakeTransport()
    transport.start()
    guard = prepared()
    moving(guard)
    arbiter = Arbiter(transport, guard)
    transport.fail_next = True
    arbiter.tick(EPOCH + 1, 1)
    assert guard.landing_latched
    assert not guard.connected
    arbiter.start()
    arbiter.tick(EPOCH + 2, 2)
    assert not guard.armed
    assert not any(event.get('attempt') == 'rc 5 0 -4 0' and event['os_send_result'] == 'FAKE_SENT'
                   for event in transport.events)


def test_shutdown_neutral_then_land_without_motor_cut():
    transport = FakeTransport()
    transport.start()
    guard = prepared()
    moving(guard)
    arbiter = Arbiter(transport, guard)
    arbiter.shutdown(2)
    assert [e['attempt'] for e in transport.events] == ['rc 0 0 0 0', 'land']
    assert not transport.connected


@pytest.mark.parametrize('value,expected', [(0.49, 0),
                         (0.5, 1), (-0.5, -1), (9.9, 10), (-10, -10)])
def test_explicit_rounding(value, expected):
    assert sdk_integer(value) == expected


def test_real_adapter_construction_and_cleanup_cannot_contact_hardware():
    with patch('socket.socket', side_effect=AssertionError('Unexpected socket')):
        transport = UdpSdkTransport()
        transport.close()


def test_real_adapter_only_loopback_wire_and_bounded_poll():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(('127.0.0.1', 0))
        server.settimeout(1)
        transport = UdpSdkTransport('127.0.0.1', server.getsockname()[1], '127.0.0.1', 0)
        try:
            transport.start()
            transport.send_rc(5, -4, 1)
            data, peer = server.recvfrom(2048)
            assert data == b'rc 5 0 -4 0'
            server.sendto(b'ok', peer)
            transport.poll(2)
            assert transport.events[-1]['acknowledgement'] == 'UNCORRELATED_SDK_REPLY'
        finally:
            transport.close()


@pytest.mark.parametrize('command', ['takeoff', 'land', 'emergency', 'rc 0 0 0 0',
                                     'up 20', 'command\nland', 'streamoff;land', 'flip l'])
def test_diagnostic_positive_allowlist(command):
    transport = Mock()
    channel = DiagnosticChannel(transport)
    with pytest.raises(ValueError):
        channel.send(command, 0)
    transport._send.assert_not_called()
    channel.close()
    transport.send_land.assert_not_called()
    transport.send_rc.assert_not_called()


def test_diagnostic_cleanup_on_error_only_closes_socket():
    transport = Mock()
    transport._send.side_effect = OSError('Fake disconnect')
    channel = DiagnosticChannel(transport)
    with pytest.raises(OSError):
        try:
            channel.send('command', 0)
        finally:
            channel.close()
    transport.close.assert_called_once()
    transport.send_land.assert_not_called()


def test_one_local_transport_owner():
    first, second = OwnerLock('unit-test-owner'), OwnerLock('unit-test-owner')
    first.acquire()
    try:
        with pytest.raises(RuntimeError):
            second.acquire()
    finally:
        first.close()
    second.acquire()
    second.close()


@pytest.mark.parametrize('packet,expected', [
    (b'bat:80;h:70;baro:1.2;', (80, 70)), (b'bat:80;', (80, None)),
    (b'bat:nan;h:70;', (None, 70)), (b'bat:101;h:70;', (None, 70)),
    (b'bat:80;bat:10;h:70;', (None, None)), (b'garbage', (None, None)),
    (b'\xff', (None, None)), (b'bat:80;h:-1;', (80, None)),
])
def test_telemetry_units_and_missing_fields(packet, expected):
    result = parse_state(packet, 12)
    assert result.receipt_ns == 12
    assert (result.battery_percent, result.height_cm) == expected


def test_limits_reject_malformed_configuration():
    for changes in ({'maximum_height_cm': 0}, {'minimum_battery_percent': 101},
                    {'operator_timeout_ns': 0}, {'maximum_land_attempts': 1.5}):
        with pytest.raises(ValueError):
            Limits(**changes)


def test_pending_arm_is_neutral_and_active_disable_revokes_it():
    guard = prepared()
    assert guard.accept(Command(EPOCH + 1, None, False, False, False, 0, 0), EPOCH + 1, 1)
    assert guard.armed and guard.awaiting_enabled
    assert guard.action(EPOCH + 1, 1)[1] == (0, 0, 0, 0)
    assert moving(guard, 2)
    assert not guard.awaiting_enabled
    assert guard.accept(Command(EPOCH + 3, None, False, False, False, 0, 0), EPOCH + 3, 3)
    assert not guard.armed


@pytest.mark.parametrize('ros_ns,mono_ns', [(None, 0), (EPOCH, None), (EPOCH, float('nan'))])
def test_malformed_clock_does_not_poison_subsequent_safe_processing(ros_ns, mono_ns):
    guard = prepared()
    assert not guard.clock_ok(ros_ns, mono_ns)
    assert guard.landing_latched
    assert guard.action(EPOCH + 1, 1)[0] == 'land'
