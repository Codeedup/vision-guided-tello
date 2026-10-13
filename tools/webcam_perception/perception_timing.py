"""Bounded sender timing evidence; no camera, detector or network dependencies."""

from collections import deque
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import platform
import subprocess
import time

from model_asset import MODEL_SHA256

MARKS = ('read_start_ns', 'read_done_ns', 'copy_done_ns', 'preprocess_done_ns',
         'inference_done_ns', 'annotation_done_ns', 'mapping_done_ns', 'encode_done_ns',
         'send_done_ns', 'console_done_ns', 'preview_done_ns')
PHASES = {
    'camera_read_ns': ('read_start_ns', 'capture_mono_ns'),
    'frame_pack_ns': ('capture_mono_ns', 'read_done_ns'),
    'frame_copy_ns': ('read_done_ns', 'copy_done_ns'),
    'preprocess_ns': ('copy_done_ns', 'preprocess_done_ns'),
    'inference_ns': ('preprocess_done_ns', 'inference_done_ns'),
    'annotation_ns': ('inference_done_ns', 'annotation_done_ns'),
    'mapping_ns': ('annotation_done_ns', 'mapping_done_ns'),
    'encode_ns': ('mapping_done_ns', 'encode_done_ns'),
    'udp_send_ns': ('encode_done_ns', 'send_done_ns'),
    'console_ns': ('send_done_ns', 'console_done_ns'),
    'preview_ns': ('console_done_ns', 'preview_done_ns'),
    'capture_to_send_ns': ('capture_mono_ns', 'send_done_ns'),
    'loop_total_ns': ('read_start_ns', 'preview_done_ns'),
}


def software_identity():
    """Collect revision/configuration provenance once, outside timed callbacks."""
    root = Path(__file__).resolve().parents[2]

    def git(*arguments):
        commands = [['git', '-C', str(root), *arguments]]
        path = PureWindowsPath(str(root))
        wsl_share = path.drive.lower().startswith(('\\\\wsl.localhost\\', '\\\\wsl$\\'))
        if os.name == 'nt' and wsl_share:
            distro = path.drive.split('\\')[-1]
            linux_root = '/' + '/'.join(path.parts[1:])
            commands.append(['wsl.exe', '-d', distro, '--', 'git', '-C', linux_root, *arguments])
        for command in commands:
            try:
                result = subprocess.run(command, capture_output=True, text=True,
                                        timeout=2, check=True)
                return result.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                continue
        return None

    dirty = git('status', '--porcelain', '--untracked-files=no')
    return {'revision': os.environ.get('TELLO_REVISION') or git('rev-parse', 'HEAD'),
            'working_tree_dirty': None if dirty is None else bool(dirty),
            'model_sha256': MODEL_SHA256}


def distribution(values):
    """Linear-interpolated millisecond percentiles over retained samples only."""
    data = sorted(value / 1_000_000 for value in values if value is not None)
    if not data:
        return {'samples': 0}

    def percentile(fraction):
        position = fraction * (len(data) - 1)
        lower = int(position)
        upper = min(lower + 1, len(data) - 1)
        return data[lower] + (data[upper] - data[lower]) * (position - lower)

    return {'samples': len(data), 'min': data[0], 'p50': percentile(0.5),
            'p95': percentile(0.95), 'p99': percentile(0.99), 'max': data[-1]}


class FrameTiming:
    """Reserve a new report and write bounded frame timing records at completion."""

    def __init__(self, path, *, metadata=None, capacity=10000):
        """Validate the memory bound and exclusively reserve the output file."""
        if type(capacity) is not int or not 1 <= capacity <= 100000:
            raise ValueError('Timing capacity must be an integer in [1,100000]')
        self.frames = deque(maxlen=capacity)
        self.total = self.omitted = 0
        self.last_capture_ns = self.last_send_ns = self.last_preview_ns = None
        self.started_ns = time.monotonic_ns()
        wall_anchor_ns = time.time_ns()
        self.metadata = dict(software_identity(), **(metadata or {}),
                             python=platform.python_version(),
                             platform=platform.platform(), wall_time_ns=wall_anchor_ns,
                             mono_anchor_ns=self.started_ns,
                             timing_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        self.file = Path(path).expanduser().open('x', encoding='utf-8')

    def record_frame(self, frame, observation, marks):
        """Retain source identity and elapsed stages without changing the packet."""
        values = dict(marks, capture_mono_ns=frame.monotonic_ns)
        ordered = [values[MARKS[0]], frame.monotonic_ns] + [values[key] for key in MARKS[1:]]
        if any(type(value) is not int or value < 0 for value in ordered):
            raise ValueError('Frame timing marks must be nonnegative integers')
        if any(b < a for a, b in zip(ordered, ordered[1:])):
            raise ValueError('Frame timing marks must advance monotonically')
        if observation['capture_time_ns'] != frame.receipt_ns:
            raise ValueError('Timing must retain the packet source timestamp')
        durations = {phase: values[end] - values[start]
                     for phase, (start, end) in PHASES.items()}
        durations['receipt_gap_ns'] = (
            None if self.last_capture_ns is None else frame.monotonic_ns - self.last_capture_ns)
        durations['send_gap_ns'] = (
            None if self.last_send_ns is None else marks['send_done_ns'] - self.last_send_ns)
        durations['between_frames_ns'] = (
            None if self.last_preview_ns is None else
            marks['read_start_ns'] - self.last_preview_ns)
        self.total += 1
        if len(self.frames) == self.frames.maxlen:
            self.omitted += 1
        self.frames.append({'record_sequence': self.total, 'frame_sequence': frame.sequence,
                            'capture_time_ns': frame.receipt_ns, 'width': frame.width,
                            'height': frame.height, 'detected': observation['detected'],
                            'tracked_hands': observation['tracked_hands'], 'marks': values,
                            'durations': durations})
        self.last_capture_ns = frame.monotonic_ns
        self.last_send_ns = marks['send_done_ns']
        self.last_preview_ns = marks['preview_done_ns']

    def finish(self, completion='quit'):
        """Write a finite JSON report once, including empty or interrupted runs."""
        if self.file.closed:
            return
        keys = [*PHASES, 'receipt_gap_ns', 'send_gap_ns', 'between_frames_ns']
        report = {'schema_version': 1, 'metadata': self.metadata,
                  'started_ns': self.started_ns, 'ended_ns': time.monotonic_ns(),
                  'completion': completion, 'capacity': self.frames.maxlen,
                  'frames_total': self.total, 'frames_retained': len(self.frames),
                  'frames_omitted': self.omitted,
                  'summary_ms': {key.removesuffix('_ns'): distribution(
                      frame['durations'][key] for frame in self.frames) for key in keys},
                  'frames': list(self.frames),
                  'limitations': [
                      'Capture stamp is camera.read completion, not camera exposure.',
                      'Camera/driver buffering before read completion is unmeasured.',
                      'Durations include host scheduling; they are not CPU-only work times.',
                      'UDP send completion is a local send attempt, not receiver delivery.',
                      'Windows and WSL monotonic clocks have different origins.',
                      'Source-stamp matching to ROS requires verified wall-clock agreement.',
                      'Summaries cover retained records; omissions exclude earlier history.',
                      'Only frames completing send and preview are recorded.',
                      'Errors mark completion and may omit the unfinished frame.',
                      'Tracing adds measurement overhead; no images or landmarks are stored.',
                  ]}
        try:
            json.dump(report, self.file, allow_nan=False)
            self.file.write('\n')
            self.file.flush()
        finally:
            self.file.close()
