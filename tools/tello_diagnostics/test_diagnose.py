"""Exercise the complete diagnostic/cleanup paths with traps for flight commands."""
from unittest.mock import Mock, patch
from queue import Queue
import socket
from types import SimpleNamespace

from diagnose import main, run_diagnostic, VideoPacketProbe
from frame_sources import decoder_worker
import pytest
from tello_bridge.policy import Telemetry
from tello_bridge.transport import DIAGNOSTIC_COMMANDS, FakeTransport


def test_complete_fake_diagnostic_has_no_actuating_paths():
    transport = FakeTransport()
    transport.send_rc = Mock(side_effect=AssertionError('RC forbidden'))
    transport.send_land = Mock(side_effect=AssertionError('Land forbidden'))
    with patch('diagnose.time.sleep'):
        result = run_diagnostic(transport, duration=0, video=True)
    attempts = [e['attempt'] for e in result['events'] if 'attempt' in e]
    assert set(attempts) <= DIAGNOSTIC_COMMANDS
    assert attempts[-1] == 'streamoff'
    assert not transport.connected
    assert result['physical_state'] == 'UNKNOWN'


def test_decoder_cleanup_failure_still_disables_video_and_closes_socket():
    transport = FakeTransport()
    decoder = Mock()
    decoder.close.side_effect = RuntimeError('Injected decoder cleanup failure')
    with patch('diagnose.time.sleep'), pytest.raises(RuntimeError):
        run_diagnostic(transport, duration=0, video=True, decoder_factory=lambda: decoder)
    assert any(event.get('attempt') == 'streamoff' for event in transport.events)
    assert not transport.connected


def test_transport_failure_and_state_cleanup_do_not_issue_flight_commands():
    transport = FakeTransport()
    transport.fail_next = True
    state = Mock()
    with pytest.raises(OSError):
        run_diagnostic(transport, duration=0, state_source=state)
    state.close.assert_called_once()
    assert [e.get('attempt') for e in transport.events] == ['command']
    assert not transport.connected


def test_decoder_failure_is_reported_without_actuating_or_skipping_cleanup():
    def failed_decoder(uri):
        raise TimeoutError('Injected UDP video open timeout')

    output = Queue()
    decoder_worker('synthetic.fixture', output, Mock(), failed_decoder)
    decoder = SimpleNamespace(last_error=output.get_nowait(), process=None,
                              start=Mock(), read=lambda: None, close=Mock())
    transport = FakeTransport()
    transport.send_rc = Mock(side_effect=AssertionError('RC forbidden'))
    transport.send_land = Mock(side_effect=AssertionError('Land forbidden'))
    with patch('diagnose.time.sleep'):
        result = run_diagnostic(transport, duration=0, video=True,
                                decoder_factory=lambda: decoder)
    assert result['frames'] == 0
    assert result['decoder_error'] == (
        'decoder_error', 'TimeoutError', 'Injected UDP video open timeout')
    assert result['events'][-1]['attempt'] == 'streamoff'
    decoder.close.assert_called_once()
    assert not transport.connected


def test_slow_cleanup_does_not_change_observation_end_telemetry_age():
    clock = SimpleNamespace(now_ns=1_000_000_000)

    def monotonic():
        clock.now_ns += 1_000_000
        return clock.now_ns / 1e9

    def slow_close():
        clock.now_ns += 2_000_000_000

    decoder = SimpleNamespace(last_error=None, process=None, start=Mock(),
                              read=lambda: None, close=slow_close)
    state = SimpleNamespace(start=Mock(), close=Mock(),
                            read=lambda now: Telemetry(now, 77.0, 0.0))
    with patch('diagnose.time.monotonic', side_effect=monotonic), \
            patch('diagnose.time.monotonic_ns', side_effect=lambda: clock.now_ns), \
            patch('diagnose.time.sleep'):
        result = run_diagnostic(FakeTransport(), duration=0.01, video=True,
                                decoder_factory=lambda: decoder, state_source=state)
    assert result['telemetry_age_ms_at_observation_end'] < 100
    assert result['cleanup_duration_ms'] == 2000
    assert result['telemetry_age_ms_at_finish'] == (
        result['telemetry_age_ms_at_observation_end'] + 2000)


def test_video_packet_probe_counts_loopback_datagrams_without_frames():
    probe = VideoPacketProbe('127.0.0.1', '127.0.0.1', port=0)
    try:
        probe.start()
        assert probe.read() is None  # Empty socket returns immediately.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            for payload in (b'synthetic', b'video'):
                sender.sendto(payload, probe.bound_address)
        assert probe.read() is None
        report = probe.report()
        assert report['packets'] == 2
        assert report['bytes'] == 14
        assert report['maximum_datagram_bytes'] == 9
        assert report['unexpected_source_packets'] == 0
        assert report['mode'] == 'packets_only'
    finally:
        probe.close()
    assert probe.socket is None


def test_video_packet_probe_does_not_count_other_hosts_as_aircraft_video():
    probe = VideoPacketProbe('127.0.0.2', '127.0.0.1', port=0)
    try:
        probe.start()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.bind(('127.0.0.1', 0))
            sender.sendto(b'other sender', probe.bound_address)
        probe.read()
        assert probe.report()['unexpected_source_packets'] == 1
        assert probe.report()['packets'] == 0
        assert probe.report()['bytes'] == 0
    finally:
        probe.close()


def test_packet_probe_bind_failure_closes_resources_before_streamon():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as occupied:
        occupied.bind(('127.0.0.1', 0))
        probe = VideoPacketProbe('127.0.0.1', *occupied.getsockname())
        transport = FakeTransport()
        with patch('diagnose.time.sleep'), pytest.raises(OSError):
            run_diagnostic(transport, duration=0, video=True, decoder_factory=lambda: probe)
    assert probe.socket is None
    assert not transport.connected
    assert not any(e.get('attempt') == 'streamon' for e in transport.events)


def test_packet_diagnostic_binds_before_streamon_and_reports_before_cleanup():
    probe = VideoPacketProbe('127.0.0.1', '127.0.0.1', port=0)

    class SyntheticTransport(FakeTransport):
        def _send(self, text, mono_ns):
            super()._send(text, mono_ns)
            if text == 'streamon':
                assert probe.bound_address is not None
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                    sender.sendto(b'synthetic', probe.bound_address)

    transport = SyntheticTransport()
    transport.send_rc = Mock(side_effect=AssertionError('RC forbidden'))
    transport.send_land = Mock(side_effect=AssertionError('Land forbidden'))
    with patch('diagnose.time.sleep'):
        result = run_diagnostic(transport, duration=0.01, video=True,
                                decoder_factory=lambda: probe)
    assert result['frames'] == 0
    assert result['video_packet_probe']['packets'] == 1
    assert result['video_packet_probe']['bytes'] == 9
    assert result['decoder_error'] is None
    assert set(e['attempt'] for e in result['events'] if 'attempt' in e) <= DIAGNOSTIC_COMMANDS
    assert result['events'][-1]['attempt'] == 'streamoff'
    assert probe.socket is None
    assert not transport.connected


@pytest.mark.parametrize('args', [('--video-packets',), ('--video', '--video-packets'),
                                  ('--hardware', '--video-packets')])
def test_packet_probe_requires_explicit_hardware_video_mode(args):
    with patch('diagnose.UdpSdkTransport') as transport, pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2
    transport.assert_not_called()
