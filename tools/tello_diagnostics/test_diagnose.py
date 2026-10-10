"""Exercise the complete diagnostic/cleanup paths with traps for flight commands."""
from unittest.mock import Mock, patch

from diagnose import run_diagnostic
import pytest
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
