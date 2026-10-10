"""Bounded timing records for asynchronous decoded-frame observation sends."""
from collections import deque
import json
from pathlib import Path
import platform
import time

from perception_timing import distribution


class DecodedTiming:
    def __init__(self, path, metadata=None, capacity=10000):
        if type(capacity) is not int or not 1 <= capacity <= 100000:
            raise ValueError('Invalid timing capacity')
        self.frames = deque(maxlen=capacity)
        self.total = 0
        self.file = Path(path).expanduser().open('x', encoding='utf-8')
        self.metadata = dict(metadata or {}, source='tello_video',
                             python=platform.python_version(), platform=platform.platform(),
                             wall_anchor_ns=time.time_ns(), mono_anchor_ns=time.monotonic_ns())

    def record(self, frame, observation, marks, send_bytes):
        keys = ('processing_start_ns', 'inference_start_ns', 'inference_done_ns',
                'mapping_done_ns', 'send_done_ns')
        ordered = [frame.monotonic_ns] + [marks[key] for key in keys]
        if any(type(v) is not int or v < 0 for v in ordered):
            raise ValueError('Timing marks must be nonnegative integers')
        if any(b < a for a, b in zip(ordered, ordered[1:])):
            raise ValueError('Decoded timing marks must advance monotonically')
        if observation['capture_time_ns'] != frame.receipt_ns:
            raise ValueError('Observation must preserve decoded-frame receipt stamp')
        self.total += 1
        self.frames.append({
            'frame_sequence': frame.sequence, 'capture_time_ns': frame.receipt_ns,
            'decode_mono_ns': frame.monotonic_ns, 'width': frame.width, 'height': frame.height,
            'detected': observation['detected'], 'tracked_hands': observation['tracked_hands'],
            'send_bytes': send_bytes, 'marks': dict(marks),
            'durations_ns': {
                'decode_to_processing': marks['processing_start_ns'] - frame.monotonic_ns,
                'inference': marks['inference_done_ns'] - marks['inference_start_ns'],
                'decode_to_send': marks['send_done_ns'] - frame.monotonic_ns}})

    def finish(self, completion='completed'):
        if self.file.closed:
            return
        report = {
            'schema': 'tello_decoded_sender_timing', 'schema_version': 1,
            'metadata': self.metadata, 'completion': completion,
            'frames_total': self.total, 'frames_retained': len(self.frames),
            'frames_omitted': self.total - len(self.frames),
            'summary_ms': {key: distribution(f['durations_ns'][key] for f in self.frames)
                           for key in ('decode_to_processing', 'inference', 'decode_to_send')},
            'frames': list(self.frames), 'limitations': [
                'Source stamps are software decode receipts, not camera exposure timestamps.',
                'Camera, transport and parser buffering before decode are unmeasured.',
                'Send completion is local OS acceptance, not WSL receipt or ROS publication.',
                'Windows/WSL wall-clock agreement must be checked independently.',
                'Windows/WSL monotonic clocks have different origins.',
                'Records contain timestamps and flags, not images or landmarks.']}
        try:
            json.dump(report, self.file, allow_nan=False)
            self.file.write('\n')
        finally:
            self.file.close()
