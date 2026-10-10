"""Timing bounds, stage attribution and the sender path with injected hardware."""

import json
from pathlib import Path
import runpy
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import perception_timing as pt
from test_open_palm import OPEN_HAND

EPOCH = 1_800_000_000_000_000_000


def sample(sequence=1):
    return SimpleNamespace(sequence=sequence, receipt_ns=EPOCH + sequence,
                           monotonic_ns=sequence * 100_000_000 + 10_000_000,
                           width=640, height=480)


def marks(frame):
    values = {key: frame.monotonic_ns + index * 1_000_000
              for index, key in enumerate(pt.MARKS)}
    values['read_start_ns'] = frame.monotonic_ns - 10_000_000
    return values


def observation(frame):
    return {'capture_time_ns': frame.receipt_ns, 'detected': True, 'tracked_hands': 1}


class TestFrameTiming(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'timing.json'

    def test_stage_attribution_source_identity_and_frame_gaps(self):
        trace = pt.FrameTiming(self.path)
        self.assertEqual(self.path.stat().st_size, 0)
        for sequence in (1, 2):
            frame = sample(sequence)
            packet = observation(frame)
            original = dict(packet)
            trace.record_frame(frame, packet, marks(frame))
            self.assertEqual(packet, original)
        trace.finish()
        report = json.loads(self.path.read_text())
        first, second = report['frames']
        self.assertEqual(first['capture_time_ns'], EPOCH + 1)
        self.assertEqual(first['durations']['camera_read_ns'], 10_000_000)
        self.assertEqual(first['durations']['capture_to_send_ns'], 8_000_000)
        self.assertEqual(first['durations']['inference_ns'], 1_000_000)
        self.assertIsNone(first['durations']['receipt_gap_ns'])
        self.assertEqual(second['durations']['receipt_gap_ns'], 100_000_000)
        self.assertEqual(second['durations']['send_gap_ns'], 100_000_000)
        self.assertEqual(second['durations']['between_frames_ns'], 80_000_000)

    def test_bounded_history_and_retained_percentiles(self):
        trace = pt.FrameTiming(self.path, capacity=2)
        for sequence in (1, 2, 3):
            frame = sample(sequence)
            trace.record_frame(frame, observation(frame), marks(frame))
        trace.finish()
        report = json.loads(self.path.read_text())
        self.assertEqual((report['frames_total'], report['frames_retained'],
                          report['frames_omitted']), (3, 2, 1))
        self.assertEqual([f['frame_sequence'] for f in report['frames']], [2, 3])
        self.assertEqual(report['summary_ms']['inference']['samples'], 2)
        self.assertEqual(pt.distribution([0, 10_000_000])['p50'], 5)

    def test_refuses_overwrite_and_invalid_capacity_before_open(self):
        for value in (0, -1, True, 1.5, 100001):
            with self.assertRaises(ValueError):
                pt.FrameTiming(self.path, capacity=value)
            self.assertFalse(self.path.exists())
        self.path.write_text('Existing evidence')
        with self.assertRaises(FileExistsError):
            pt.FrameTiming(self.path)
        self.assertEqual(self.path.read_text(), 'Existing evidence')

    def test_bad_marks_or_source_stamp_do_not_enter_report(self):
        trace = pt.FrameTiming(self.path)
        frame = sample()
        bad = marks(frame)
        bad['inference_done_ns'] = bad['preprocess_done_ns'] - 1
        with self.assertRaises(ValueError):
            trace.record_frame(frame, observation(frame), bad)
        wrong = observation(frame)
        wrong['capture_time_ns'] += 1
        with self.assertRaises(ValueError):
            trace.record_frame(frame, wrong, marks(frame))
        trace.finish('error')
        self.assertEqual(json.loads(self.path.read_text())['frames_total'], 0)

    def test_empty_error_and_idempotent_finish(self):
        trace = pt.FrameTiming(self.path)
        trace.finish('error')
        original = self.path.read_bytes()
        trace.finish('quit')
        self.assertEqual(original, self.path.read_bytes())
        report = json.loads(original)
        self.assertEqual(report['completion'], 'error')
        self.assertEqual(report['summary_ms']['inference'], {'samples': 0})


class TestPreviewTiming(unittest.TestCase):
    def exercise(self, path=None, failure=None):
        cv = Mock(__version__='injected')
        cv.waitKey.side_effect = [0, ord('q')]
        frame = Mock(shape=(480, 640, 3))
        frame.copy.return_value = frame
        cv.cvtColor.return_value = frame
        mp = Mock(__version__='injected')
        tasks, python, vision = Mock(), Mock(), MagicMock()
        modules = {'cv2': cv, 'mediapipe': mp, 'mediapipe.tasks': tasks,
                   'mediapipe.tasks.python': python, 'mediapipe.tasks.python.vision': vision}
        mp.tasks, tasks.python, python.vision = tasks, python, vision
        source, sender = Mock(), Mock()
        clock = [1_000_000_000]
        sequence = [0]

        def now():
            clock[0] += 100
            return clock[0]

        def read():
            sequence[0] += 1
            clock[0] += 10_000_000
            return SimpleNamespace(sequence=sequence[0], receipt_ns=EPOCH + sequence[0],
                                   monotonic_ns=now(), width=640, height=480,
                                   bgr_array=lambda: frame)

        def detect(*args):
            if failure == 'interrupt':
                raise KeyboardInterrupt()
            clock[0] += 30_000_000
            hand = [SimpleNamespace(x=x, y=y) for x, y in OPEN_HAND]
            return SimpleNamespace(hand_landmarks=[hand])

        source.read.side_effect = read
        detector = vision.HandLandmarker.create_from_options.return_value.__enter__.return_value
        detector.detect_for_video.side_effect = detect
        if failure == 'send':
            sender.sendto.side_effect = OSError('Injected send failure')
        with patch.dict('sys.modules', modules):
            namespace = runpy.run_path(str(Path(__file__).with_name('hand_preview.py')))
            globals_ = namespace['main'].__globals__
            factory = Mock(return_value=source)
            model = Mock(return_value=b'injected fixture model')
            if failure == 'model':
                model.side_effect = ValueError('Injected model failure')
            args = ['--receiver', '127.0.0.1', '--revision', 'fixture']
            if path:
                args += ['--timing-trace', str(path)]
            with patch.dict(globals_, {'WebcamSource': factory, 'verified_model': model}):
                with patch('socket.socket', return_value=sender), patch(
                        'time.monotonic_ns', side_effect=now), patch('builtins.print'):
                    if failure or (path and path.exists()):
                        with self.assertRaises((KeyboardInterrupt, OSError, ValueError)):
                            namespace['main'](args)
                    else:
                        namespace['main'](args)
        return source, sender, cv, factory, model

    def test_traced_and_untraced_sender_packets_are_identical(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'timing.json'
            untraced = self.exercise()
            traced = self.exercise(path)

            def packets(result):
                return [call.args for call in result[1].sendto.call_args_list]

            self.assertEqual(packets(untraced), packets(traced))
            report = json.loads(path.read_text())
            self.assertEqual(report['completion'], 'quit')
            self.assertEqual(report['frames_total'], 2)
            self.assertGreaterEqual(report['summary_ms']['inference']['min'], 30)
            self.assertEqual(report['metadata']['revision'], 'fixture')
            self.assertTrue(all(f['detected'] for f in report['frames']))
            for result in (untraced, traced):
                result[0].close.assert_called_once()
                result[1].close.assert_called_once()
                result[2].destroyAllWindows.assert_called_once()

    def test_existing_report_stops_before_model_camera_or_send(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'timing.json'
            path.write_text('Existing evidence')
            result = self.exercise(path)
            result[3].assert_not_called()
            result[4].assert_not_called()
            result[1].sendto.assert_not_called()
            self.assertEqual(path.read_text(), 'Existing evidence')

    def test_failure_or_interrupt_flushes_valid_completion_report(self):
        with tempfile.TemporaryDirectory() as directory:
            for failure in ('model', 'send', 'interrupt'):
                path = Path(directory) / (failure + '.json')
                result = self.exercise(path, failure)
                report = json.loads(path.read_text())
                expected = 'interrupt' if failure == 'interrupt' else 'error'
                self.assertEqual(report['completion'], expected)
                self.assertEqual(report['frames_total'], 0)
                if failure != 'model':
                    result[0].close.assert_called_once()
                    result[1].close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
