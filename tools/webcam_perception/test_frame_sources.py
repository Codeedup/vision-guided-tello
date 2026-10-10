"""Synthetic pixels/landmarks: identity, signs, colour, dimensions and dropout."""
from dataclasses import replace
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from frame_sources import (
    DecoderSource,
    Frame,
    LatestFrames,
    RecordedFrames,
    WebcamSource,
)
from perception import target_from_landmarks
from test_open_palm import OPEN_HAND


def fake_decoder(uri):
    yield Frame(1, time.time_ns(), time.monotonic_ns(), 2, 1, b'\xff\0\0\0\0\xff')


def stuck_decoder(uri):
    time.sleep(30)
    yield from ()


class TestFrames(unittest.TestCase):
    def frame(self, sequence=1, mono=10):
        return Frame(sequence, 100, mono, 2, 1, b'\xff\0\0\0\0\xff')

    def test_colour_dimensions_and_no_mirror(self):
        frame = self.frame()
        self.assertEqual(frame.rgb_bytes(), b'\0\0\xff\xff\0\0')
        self.assertEqual((frame.width, frame.height), (2, 1))
        self.assertFalse(frame.mirrored)
        with self.assertRaises(ValueError):
            replace(frame, mirrored=True)
        with self.assertRaises(ValueError):
            replace(frame, width=3)

    def test_cached_frame_is_not_fresh_and_latest_only(self):
        slot = LatestFrames()
        self.assertTrue(slot.offer(self.frame()))
        self.assertTrue(slot.offer(self.frame(2)))
        self.assertEqual(slot.replaced, 1)
        self.assertEqual(slot.read(10).sequence, 2)
        self.assertIsNone(slot.read(11))
        self.assertFalse(slot.offer(self.frame(1)))
        self.assertIsNone(slot.read(12))

    def test_timeout_and_future_receipt(self):
        slot = LatestFrames()
        slot.offer(self.frame())
        self.assertIsNone(slot.read(250_000_010))
        self.assertIsNone(slot.read(9))

    def test_recorded_identity_and_stamp_are_not_rewritten(self):
        source = RecordedFrames([self.frame(), self.frame(), self.frame(2)])
        source.start()
        self.assertEqual(source.read(10).receipt_ns, 100)
        self.assertIsNone(source.read(10))
        self.assertEqual(source.read(10).sequence, 2)
        self.assertIsNone(source.read(10))
        source.close()

    def test_webcam_lazy_and_injected_read_failure(self):
        factory = Mock()
        with patch('socket.socket', side_effect=AssertionError('Network access')):
            source = WebcamSource(factory=factory)
            factory.assert_not_called()
            source.start()
            factory.return_value.read.return_value = (False, None)
            self.assertIsNone(source.read())
            source.close()
        factory.return_value.release.assert_called_once()

    def test_shared_perception_uses_open_gate_centre_and_signs(self):
        hand = [SimpleNamespace(x=x, y=y) for x, y in OPEN_HAND]
        result = target_from_landmarks([hand], 640, 480, 123)
        self.assertTrue(result['detected'])
        self.assertGreater(result['error_x'], 0)
        self.assertGreater(result['error_y'], 0)
        self.assertEqual(result['capture_time_ns'], 123)
        for hands in ([], [hand, hand], [hand[:-1]]):
            invalid = target_from_landmarks(hands, 640, 480, 123)
            self.assertFalse(invalid['detected'])
            self.assertEqual((invalid['error_x'], invalid['error_y']), (0, 0))
            self.assertEqual(invalid['tracked_hands'], len(hands))

    def test_decoder_worker_plumbing_and_bounded_stuck_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.fixture'
            path.write_text('Author-created fake decoder fixture; no human image')
            for decoder in (fake_decoder, stuck_decoder):
                source = DecoderSource(str(path), decoder=decoder, timeout_ns=5_000_000_000)
                self.assertIsNone(source.process)
                source.start()
                if decoder is fake_decoder:
                    deadline = time.monotonic() + 5
                    frame = None
                    while frame is None and time.monotonic() < deadline:
                        frame = source.read()
                        time.sleep(0.01)
                    self.assertIsNotNone(frame)
                    self.assertEqual(frame.rgb_bytes(), b'\0\0\xff\xff\0\0')
                    self.assertIsNone(source.read())
                start = time.monotonic()
                source.close()
                self.assertLess(time.monotonic() - start, 2)

    def test_hardware_video_requires_explicit_opt_in(self):
        with self.assertRaises(ValueError):
            DecoderSource('udp://0.0.0.0:11111')
        source = DecoderSource('udp://0.0.0.0:11111', hardware=True)
        self.assertIsNone(source.process)


if __name__ == '__main__':
    unittest.main()
