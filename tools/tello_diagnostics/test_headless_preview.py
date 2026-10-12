"""Headless comparison keeps observation semantics and propeller-off cleanup."""
import contextlib
import io
import json
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bench_hand_preview import HandPreview, main
from diagnose import run_diagnostic
from frame_sources import Frame
from tello_bridge.transport import DIAGNOSTIC_COMMANDS, FakeTransport
from test_open_palm import OPEN_HAND


def sample():
    return Frame(1, 123, 10_000_000, 64, 48, b'\x01\x02\x03' * (64 * 48))


def open_hand():
    return [SimpleNamespace(x=x, y=y) for x, y in OPEN_HAND]


def preview_for(hands, **kwargs):
    cv2 = Mock()
    cv2.cvtColor.side_effect = lambda image, colour: image[..., ::-1]
    cv2.waitKey.return_value = -1
    mp = SimpleNamespace(Image=Mock(side_effect=lambda **kw: SimpleNamespace(**kw)),
                         ImageFormat=SimpleNamespace(SRGB=1))
    detector = Mock()
    detector.detect_for_video.return_value = SimpleNamespace(hand_landmarks=hands)
    return HandPreview(cv2, mp, detector, **kwargs)


class HeadlessPreviewTests(unittest.TestCase):
    def test_identical_packets_without_gui_or_source_mutation(self):
        curled = open_hand()
        curled[8] = SimpleNamespace(x=curled[5].x, y=0.70)
        cases = [([], False), ([open_hand()], True), ([curled], False),
                 ([open_hand(), open_hand()], False)]
        for hands, eligible in cases:
            with self.subTest(hands=len(hands), eligible=eligible):
                outputs = []
                for show_preview in (True, False):
                    sender, timing = Mock(), Mock()
                    sender.sendto.side_effect = lambda packet, address: len(packet)
                    preview = preview_for(hands, sender=sender, receiver=('127.0.0.1', 5005),
                                          timing=timing, show_preview=show_preview)
                    frame = sample()
                    original = frame.bgr_array().copy()
                    with patch('bench_hand_preview.time.monotonic', return_value=0):
                        self.assertTrue(preview(frame))
                    packet, address = sender.sendto.call_args.args
                    outputs.append(packet)
                    observation = json.loads(packet)
                    self.assertEqual(observation['capture_time_ns'], frame.receipt_ns)
                    self.assertEqual(observation['detected'], eligible)
                    self.assertEqual(observation['tracked_hands'], len(hands))
                    if not eligible:
                        self.assertEqual(observation['error_x'], 0)
                        self.assertEqual(observation['error_y'], 0)
                    self.assertEqual(address, ('127.0.0.1', 5005))
                    self.assertTrue((frame.bgr_array() == original).all())
                    timing.record.assert_called_once()
                    if not show_preview:
                        for method in ('circle', 'line', 'drawMarker', 'putText',
                                       'imshow', 'waitKey'):
                            getattr(preview.cv2, method).assert_not_called()
                self.assertEqual(outputs[0], outputs[1])

    def test_headless_inference_failure_closes_sdk_stream(self):
        preview = preview_for([], show_preview=False)
        preview.detector.detect_for_video.side_effect = RuntimeError('Synthetic inference')
        decoder = SimpleNamespace(start=Mock(), read=sample, close=Mock(),
                                  process=None, last_error=None)
        transport = FakeTransport()
        with patch('diagnose.time.sleep'), self.assertRaisesRegex(RuntimeError, 'Synthetic'):
            run_diagnostic(transport, duration=1, video=True,
                           decoder_factory=lambda: decoder, on_frame=preview)
        decoder.close.assert_called_once()
        self.assertFalse(transport.connected)
        attempts = [x['attempt'] for x in transport.events if 'attempt' in x]
        self.assertLessEqual(set(attempts), DIAGNOSTIC_COMMANDS)
        self.assertEqual(attempts[-1], 'streamoff')

    def test_cli_mode_metadata_detector_and_success_or_error_cleanup(self):
        frame = sample()
        frame.bgr_array()  # Load lazy NumPy once before patching the module registry.
        for headless in (False, True):
            for fail in (False, True):
                with self.subTest(headless=headless, fail=fail):
                    preview = preview_for([open_hand()])
                    cv2, mp = preview.cv2, preview.mp
                    cv2.__version__ = mp.__version__ = 'synthetic'
                    factory = Mock()
                    factory.create_from_options.return_value = contextlib.nullcontext(
                        preview.detector)
                    vision = SimpleNamespace(HandLandmarkerOptions=Mock(), HandLandmarker=factory,
                                             RunningMode=SimpleNamespace(VIDEO='VIDEO'))
                    python = SimpleNamespace(BaseOptions=Mock())
                    modules = {'cv2': cv2, 'mediapipe': mp,
                               'mediapipe.tasks': SimpleNamespace(python=python),
                               'mediapipe.tasks.python': SimpleNamespace(vision=vision)}
                    sender = Mock()
                    sender.sendto.side_effect = lambda packet, address: len(packet)

                    def diagnostic(transport, **kwargs):
                        self.assertTrue(kwargs['on_frame'](frame))
                        if fail:
                            raise RuntimeError('Synthetic failure')
                        return {'frames': 1}

                    args = ['--hardware', '--duration', '1', '--receiver', '127.0.0.1',
                            '--timing-trace', 'synthetic.json']
                    if headless:
                        args.append('--headless')
                    with patch.dict(sys.modules, modules), \
                            patch('model_asset.verified_model', return_value=b'synthetic'), \
                            patch('bench_hand_preview.UdpSdkTransport',
                                  return_value=FakeTransport()), \
                            patch('bench_hand_preview.StateReceiver'), \
                            patch('bench_hand_preview.socket.socket', return_value=sender), \
                            patch('bench_hand_preview.DecodedTiming') as timing, \
                            patch('bench_hand_preview.run_diagnostic', side_effect=diagnostic), \
                            patch('bench_hand_preview.time.monotonic', return_value=0), \
                            contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(main(args), 2 if fail else 0)
                    metadata = timing.call_args.kwargs['metadata']
                    self.assertEqual(metadata['preview_enabled'], not headless)
                    self.assertEqual(metadata['num_hands'], 2)
                    self.assertEqual(vision.HandLandmarkerOptions.call_args.kwargs['num_hands'], 2)
                    sender.close.assert_called_once()
                    timing.return_value.finish.assert_called_once_with(
                        'error' if fail else 'completed')
                    if headless:
                        cv2.imshow.assert_not_called()
                        cv2.destroyAllWindows.assert_not_called()
                    else:
                        cv2.imshow.assert_called_once()
                        cv2.destroyAllWindows.assert_called_once()

    def test_headless_still_requires_explicit_hardware(self):
        with patch('bench_hand_preview.UdpSdkTransport') as transport, \
                contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as error:
            main(['--headless'])
        self.assertEqual(error.exception.code, 2)
        transport.assert_not_called()


if __name__ == '__main__':
    unittest.main()
