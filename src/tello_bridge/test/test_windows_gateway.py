"""Offline Windows gateway behavior at the final FAKE transport boundary."""

import json
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from tello_bridge.forward_approved import encode_approved
from tello_bridge.policy import FRESH_NS
from tello_bridge.policy import Telemetry
from tello_bridge.transport import FakeTransport
from tello_bridge.windows_gateway import (decode_packet, FakeGateway, RealGateway,
                                          main)

EPOCH = 1_800_000_000_000_000_000


def packet(decision=EPOCH, source=EPOCH, enabled=True, tracking=True,
           land=False, lateral=5, vertical=-4):
    """Build one version-one wire decision."""
    return json.dumps({'version': 1, 'decision_ns': decision,
                       'source_ns': source, 'enabled': enabled,
                       'tracking': tracking, 'land': land,
                       'lateral': lateral, 'vertical': vertical}).encode()


def ready():
    """Prepare a simulated active session with one non-neutral FAKE send."""
    gateway = FakeGateway()
    assert gateway.receive(packet(enabled=False, tracking=False,
                                  lateral=0, vertical=0), EPOCH + 1, 1)
    assert gateway.prepare_fake_hover(mono_ns=1)
    assert gateway.arm_fake(EPOCH + 2, 2)
    assert gateway.receive(packet(decision=EPOCH + 3, source=EPOCH + 3),
                           EPOCH + 3, 3)
    gateway.tick(EPOCH + 3, 3)
    return gateway


def attempts(gateway):
    """Return final boundary attempts in order."""
    return [event['attempt'] for event in gateway.arbiter.transport.events
            if 'attempt' in event]


def test_space_aborts_before_any_later_motion_and_lands_without_ros():
    """Space immediately latches and sends neutral then land."""
    gateway = ready()
    assert attempts(gateway)[-1] == 'rc 5 0 -4 0'
    start = time.perf_counter_ns()
    gateway.abort(ros_ns=EPOCH + 4, mono_ns=4)
    elapsed = time.perf_counter_ns() - start
    assert elapsed < 100_000_000
    assert attempts(gateway)[-2:] == ['rc 0 0 0 0', 'land']
    assert gateway.guard.reason == 'OPERATOR_ABORT'
    assert [event['event'] for event in list(gateway.events)[-4:]] == [
        'operator_input', 'abort_latch', 'land_attempt', 'abort_dispatch']
    assert list(gateway.events)[-2]['retry_number'] == 1
    assert all('attempt_observed_ns' in event
               for event in gateway.arbiter.transport.events
               if 'attempt' in event)
    assert gateway.guard.status()['landing_delivery'] == (
        'REQUEST_PENDING_UNCONFIRMED')
    count = len(attempts(gateway))
    assert not gateway.receive(packet(decision=EPOCH + 5, source=EPOCH + 5),
                               EPOCH + 5, 5)
    gateway.tick(EPOCH + 5, 5)
    assert not any(event.startswith('rc 5 ')
                   for event in attempts(gateway)[count:])


def test_space_dispatch_uses_local_monotonic_time_when_wall_clock_changes():
    """A wall-clock jump cannot prevent the first local land attempt."""
    gateway = ready()
    gateway.abort(ros_ns=EPOCH - 10_000_000_000, mono_ns=4)
    assert attempts(gateway)[-2:] == ['rc 0 0 0 0', 'land']
    assert gateway.guard.reason == 'OPERATOR_ABORT'


def test_space_records_operator_cause_after_supervisor_land():
    """A later Space retains the retry budget and records local intent."""
    gateway = ready()
    gateway.guard.request_land('SUPERVISOR_LAND')
    gateway.tick(EPOCH + 4, 4)
    assert attempts(gateway).count('land') == 1
    gateway.abort(ros_ns=EPOCH + 5, mono_ns=5)
    assert gateway.guard.reason == 'OPERATOR_ABORT'
    assert gateway.guard.land_attempts == 1
    assert attempts(gateway).count('land') == 1


def test_held_space_reply_delay_replay_and_reconnect_cannot_rearm():
    """Repeated input and reconnect preserve the latch and retry budget."""
    gateway = ready()
    gateway.arbiter.transport.never_reply = True
    gateway.abort(ros_ns=EPOCH + 4, mono_ns=4)
    for now in range(5, 5_000_000_000, 20_000_000):
        gateway.abort(ros_ns=EPOCH + now, mono_ns=now)
        gateway.tick(EPOCH + now, now)
    assert attempts(gateway).count('land') == 3
    gateway.guard.link(False)
    gateway.arbiter.start()
    assert not gateway.receive(packet(decision=EPOCH + 5_000_000_001),
                               EPOCH + 5_000_000_001, 5_000_000_001)
    assert not gateway.arm_fake(EPOCH + 5_000_000_002, 5_000_000_002)
    assert gateway.guard.landing_latched
    assert gateway.guard.reason == 'OPERATOR_ABORT'


def test_link_loss_and_failed_neutral_leave_unconfirmed_abort():
    """A failed neutral send remains visibly unconfirmed."""
    gateway = ready()
    gateway.arbiter.transport.fail_next = True
    gateway.abort(ros_ns=EPOCH + 4, mono_ns=4)
    assert gateway.guard.landing_latched
    assert gateway.guard.reason == 'OPERATOR_ABORT'
    assert gateway.guard.status()['landing_delivery'] == 'LINK_UNAVAILABLE'
    assert 'land' not in attempts(gateway)


def test_silent_upstream_lands_without_ros_acknowledgement():
    """No new decisions eventually trigger local input-loss landing."""
    gateway = ready()
    for now in range(20_000_003, 2_000_000_003, 20_000_000):
        gateway.tick(EPOCH + now, now)
    assert gateway.guard.landing_latched
    assert gateway.guard.reason == 'APPROVED_INPUT_LOSS'
    assert 'land' in attempts(gateway)


def test_focus_loss_and_close_abort_active_session():
    """Focus loss and ordinary close use the same local abort."""
    gateway = ready()
    with patch('tello_bridge.windows_gateway.time.time_ns',
               return_value=EPOCH + 4), \
            patch('tello_bridge.windows_gateway.time.monotonic_ns',
                  return_value=4):
        gateway.focus_lost()
    assert gateway.guard.landing_latched
    assert attempts(gateway)[-2:] == ['rc 0 0 0 0', 'land']
    with patch('tello_bridge.windows_gateway.time.monotonic_ns', return_value=5):
        gateway.close()
    assert attempts(gateway).count('land') == 1
    other = ready()
    with patch('tello_bridge.windows_gateway.time.time_ns',
               return_value=EPOCH + 4), \
            patch('tello_bridge.windows_gateway.time.monotonic_ns',
                  return_value=4):
        other.close()
    assert other.guard.landing_latched
    assert 'land' in attempts(other)


def test_fake_reset_requires_local_operation_and_new_disabled_decision():
    """Upstream flags cannot reset; local reset requires disabled input."""
    gateway = ready()
    gateway.abort(ros_ns=EPOCH + 4, mono_ns=4)
    assert not gateway.receive(packet(decision=EPOCH + 5, enabled=False,
                                      tracking=False, lateral=0, vertical=0),
                               EPOCH + 5, 5)
    gateway.reset_fake()
    assert not gateway.arm_fake(EPOCH + 6, 6)
    assert gateway.receive(packet(decision=EPOCH + 7, enabled=False,
                                  tracking=False, lateral=0, vertical=0),
                           EPOCH + 7, 7)
    assert gateway.prepare_fake_hover(mono_ns=7)
    assert gateway.arm_fake(EPOCH + 8, 8)


@pytest.mark.parametrize('age,accepted', [(FRESH_NS - 1, True),
                                          (FRESH_NS, False), (-1, False)])
def test_original_source_freshness_is_strict(age, accepted):
    """Source age must remain strictly below 250 ms."""
    gateway = ready()
    now = EPOCH + FRESH_NS + 20
    assert gateway.receive(packet(decision=now, source=now - age),
                           now, FRESH_NS + 20) == accepted


@pytest.mark.parametrize('change', [lambda x: x.update(lateral=float('nan')),
                                    lambda x: x.update(decision_ns=True),
                                    lambda x: x.update(source_ns='0'),
                                    lambda x: x.update(enabled=1),
                                    lambda x: x.update(extra=1)])
def test_malformed_packet_never_authorizes_motion(change):
    """Malformed wire data neutralizes cached motion."""
    item = json.loads(packet())
    change(item)
    gateway = ready()
    assert not gateway.receive(json.dumps(item).encode(), EPOCH + 4, 4)
    gateway.tick(EPOCH + 4, 4)
    assert attempts(gateway)[-1] == 'rc 0 0 0 0'


def test_forwarder_preserves_stamps_and_has_no_side_effects():
    """Forwarding keeps original stamps and CLI errors stay passive."""
    def stamp(ns):
        return SimpleNamespace(sec=ns // 1_000_000_000,
                               nanosec=ns % 1_000_000_000)

    msg = SimpleNamespace(header=SimpleNamespace(stamp=stamp(EPOCH + 3)),
                          source_header=SimpleNamespace(
                              stamp=stamp(EPOCH + 2)),
                          autonomy_enabled=True, tracking_allowed=True,
                          request_land=False, lateral=3.0, vertical=-2.0)
    decoded = decode_packet(encode_approved(msg))
    assert decoded.decision_ns == EPOCH + 3
    assert decoded.source_ns == EPOCH + 2
    with pytest.raises(SystemExit):
        main([])


def test_gateway_keyboard_interrupt_closes_and_saves_trace(tmp_path):
    """Ctrl+C during Tk mainloop exits cleanly and retains FAKE evidence."""
    root = Mock()
    root.mainloop.side_effect = KeyboardInterrupt
    tkinter = SimpleNamespace(Tk=lambda: root, StringVar=Mock(),
                              Label=Mock(return_value=Mock()),
                              Button=Mock(return_value=Mock()))
    trace = tmp_path / 'trace.json'
    marker = tmp_path / 'armed'
    with patch.dict(sys.modules, {'tkinter': tkinter}), \
            patch('tello_bridge.windows_gateway.socket.socket'), \
            patch('tello_bridge.windows_gateway.OwnerLock'), \
            patch('tello_bridge.windows_gateway.fake_arm_marker',
                  return_value=marker):
        main(['--bind', '127.0.0.1', '--port', '5010', '--trace', str(trace)])
    result = json.loads(trace.read_text())
    assert result['fake'] is True
    assert result['mode'] == 'fake'
    assert result['events_omitted'] == result['transport_events_omitted'] == 0
    assert result['events'][-1]['event'] == 'operator_interrupt'
    assert not marker.exists()


class StateFeed:
    def __init__(self, telemetry=None):
        self.telemetry = telemetry
        self.started = self.closed = False

    def start(self):
        self.started = True

    def read(self, _mono_ns):
        return self.telemetry

    def close(self):
        self.closed = True


def test_grounded_bench_rejects_upstream_and_only_emits_single_axis_pulses():
    transport = FakeTransport()
    gateway = RealGateway('grounded-bench', transport, StateFeed())
    assert attempts(gateway) == ['command']
    assert not gateway.receive(packet())
    for axis, values in RealGateway.BENCH_AXES.items():
        assert gateway.bench_pulse(axis)
        assert attempts(gateway)[-2:] == [
            f'rc {values[0]} 0 {values[1]} 0', 'rc 0 0 0 0']
    assert not gateway.bench_pulse('mixed-axis')
    gateway.close()


def test_flight_requires_current_state_and_local_hover_confirmation():
    state = StateFeed(Telemetry(1, 80, 70))
    gateway = RealGateway('flight', FakeTransport(), state)
    assert gateway.receive(packet(enabled=False, tracking=False,
                                  lateral=0, vertical=0), EPOCH + 1, 1)
    gateway.tick(EPOCH + 1, 1)
    assert gateway.confirm_hover(1)
    assert gateway.arm_flight(EPOCH + 2, 2)
    assert gateway.receive(packet(EPOCH + 3, EPOCH + 3), EPOCH + 3, 3)
    gateway.tick(EPOCH + 3, 3)
    assert attempts(gateway)[-1] == 'rc 5 0 -4 0'
    stale = RealGateway('flight', FakeTransport(), StateFeed(Telemetry(-1, 80, 70)))
    assert not stale.confirm_hover(1)


@pytest.mark.parametrize('args', [
    ['--mode', 'grounded-bench', '--bind', '127.0.0.1', '--port', '5010',
     '--trace', 'new.json'],
    ['--mode', 'flight', '--hardware', '--tello-bind', '192.168.10.2',
     '--pilot-present', '--propellers-removed', '--bind', '127.0.0.1',
     '--port', '5010', '--trace', 'new.json'],
])
def test_real_mode_assertions_fail_closed(args):
    with pytest.raises(SystemExit):
        main(args)
