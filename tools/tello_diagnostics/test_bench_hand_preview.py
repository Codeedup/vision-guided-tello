"""Synthetic preview checks: shared gate, colours, identity and SDK cleanup."""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bench_hand_preview import HandPreview, main
from diagnose import run_diagnostic
from frame_sources import Frame
import pytest
from tello_bridge.transport import DIAGNOSTIC_COMMANDS, FakeTransport
from test_open_palm import OPEN_HAND


def sample(sequence=0):
    return Frame(sequence, 123, 10_000_000, 64, 48, b'\x01\x02\x03' * (64 * 48))


def open_hand():
    return [SimpleNamespace(x=x, y=y) for x, y in OPEN_HAND]


def preview_for(hands, key=-1, **kwargs):
    cv2 = Mock()
    cv2.cvtColor.side_effect = lambda image, colour: image[..., ::-1]
    cv2.waitKey.return_value = key
    mp = SimpleNamespace(Image=Mock(side_effect=lambda **kwargs: SimpleNamespace(**kwargs)),
                         ImageFormat=SimpleNamespace(SRGB=1))
    detector = Mock()
    detector.detect_for_video.return_value = SimpleNamespace(hand_landmarks=hands)
    return HandPreview(cv2, mp, detector, **kwargs)


def test_open_palm_preview_preserves_source_stamp_colour_and_image_error_signs():
    preview = preview_for([open_hand()])
    with patch('bench_hand_preview.time.monotonic', return_value=0):
        assert preview(sample())
        assert preview(sample(1))
    observation = preview.last_observation
    assert observation['detected'] is True
    assert observation['capture_time_ns'] == 123
    assert observation['error_x'] > 0
    assert observation['error_y'] > 0  # Image coordinates: positive down.
    image = preview.detector.detect_for_video.call_args_list[0].args[0]
    assert image.data[0, 0].tolist() == [3, 2, 1]
    assert [call.args[1] for call in preview.detector.detect_for_video.call_args_list] == [10, 11]
    assert preview.summary() == {'frames': 2, 'eligible_frames': 2, 'ineligible_frames': 0}
    assert any('ELIGIBLE OPEN PALM' in call.args[1]
               for call in preview.cv2.putText.call_args_list)


@pytest.mark.parametrize('kind', ['none', 'curled', 'two'])
def test_preview_rejects_no_hand_curled_finger_and_multiple_hands(kind):
    hands = []
    if kind == 'curled':
        hand = open_hand()
        hand[8] = SimpleNamespace(x=hand[5].x, y=0.70)
        hands = [hand]
    elif kind == 'two':
        hands = [open_hand(), open_hand()]
    preview = preview_for(hands, key=ord('q'))
    with patch('bench_hand_preview.time.monotonic', return_value=0):
        assert preview(sample()) is False
    observation = preview.last_observation
    assert observation['detected'] is False
    assert observation['error_x'] == observation['error_y'] == 0
    assert preview.summary()['ineligible_frames'] == 1
    preview.cv2.line.assert_not_called()


def test_preview_quit_runs_sdk_allowlisted_cleanup():
    decoder = SimpleNamespace(start=Mock(), read=lambda: sample(), close=Mock(),
                              process=None, last_error=None)
    transport = FakeTransport()
    transport.send_rc = Mock(side_effect=AssertionError('RC forbidden'))
    transport.send_land = Mock(side_effect=AssertionError('Land forbidden'))
    callback = Mock(return_value=False)
    with patch('diagnose.time.sleep'):
        result = run_diagnostic(transport, duration=1, video=True,
                                decoder_factory=lambda: decoder, on_frame=callback)
    assert result['frames'] == 1
    callback.assert_called_once()
    decoder.close.assert_called_once()
    assert not transport.connected
    attempts = [e['attempt'] for e in result['events'] if 'attempt' in e]
    assert set(attempts) <= DIAGNOSTIC_COMMANDS
    assert attempts[-1] == 'streamoff'


def test_preview_inference_failure_still_closes_and_disables_stream():
    decoder = SimpleNamespace(start=Mock(), read=lambda: sample(), close=Mock(),
                              process=None, last_error=None)
    transport = FakeTransport()
    with patch('diagnose.time.sleep'), pytest.raises(RuntimeError, match='Synthetic inference'):
        run_diagnostic(transport, duration=1, video=True, decoder_factory=lambda: decoder,
                       on_frame=Mock(side_effect=RuntimeError('Synthetic inference failure')))
    decoder.close.assert_called_once()
    assert not transport.connected
    assert transport.events[-1]['attempt'] == 'streamoff'


def test_udp_observation_retains_decode_stamp_and_is_sent_before_display():
    sender = Mock()
    sender.sendto.side_effect = lambda packet, address: len(packet)
    timing = Mock()
    preview = preview_for([open_hand()], sender=sender, receiver=('127.0.0.1', 5005),
                          timing=timing)
    actions = Mock()
    actions.attach_mock(sender.sendto, 'send')
    actions.attach_mock(preview.cv2.imshow, 'display')
    with patch('bench_hand_preview.time.monotonic', return_value=0):
        preview(sample())
    packet, address = sender.sendto.call_args.args
    observation = json.loads(packet)
    assert observation == preview.last_observation
    assert observation['capture_time_ns'] == 123
    assert address == ('127.0.0.1', 5005)
    assert [call[0] for call in actions.mock_calls] == ['send', 'display']
    assert preview.summary()['observation_send_attempts'] == 1
    timing.record.assert_called_once()
    assert timing.record.call_args.args[0].sequence == 0


def test_incomplete_observation_send_fails_without_recording_success():
    sender = Mock()
    sender.sendto.return_value = 0
    timing = Mock()
    preview = preview_for([], sender=sender, receiver=('127.0.0.1', 5005), timing=timing)
    with pytest.raises(OSError, match='Incomplete observation'):
        preview(sample())
    assert preview.sent == 0
    timing.record.assert_not_called()
    preview.cv2.imshow.assert_not_called()


@pytest.mark.parametrize('args', [[], ['--hardware', '--duration', 'nan'],
                                  ['--hardware', '--duration', '61'],
                                  ['--hardware', '--receiver', 'invalid'],
                                  ['--hardware', '--port', '0'],
                                  ['--hardware', '--timing-trace', 'new.json']])
def test_preview_validates_explicit_hardware_and_duration_before_activation(args):
    with patch('bench_hand_preview.UdpSdkTransport') as transport, \
            pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2
    transport.assert_not_called()
