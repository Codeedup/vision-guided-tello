"""Exercise the complete diagnostic/cleanup paths with traps for flight commands."""
from unittest.mock import Mock, patch
from queue import Queue
from types import SimpleNamespace

from diagnose import run_diagnostic
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
