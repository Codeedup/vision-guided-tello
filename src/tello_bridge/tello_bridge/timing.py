"""Opt-in FAKE timing evidence; bounded memory, no per-event output or callbacks."""

from collections import deque
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import time


def snapshot(guard, mono_ns):
    """Capture authority at a callback boundary, never infer server receipt."""
    receipt = guard.operator_receipt_ns
    return dict(guard.status(), operator_receipt_ns=receipt,
                lease_age_ns=None if receipt is None else mono_ns - receipt)


class TimingTrace:
    """Reserve a new file once; serialize retained events only at completion."""

    def __init__(self, path, *, fake, metadata=None, capacity=20000):
        if fake is not True:
            raise ValueError('Timing trace requires FAKE transport')
        if type(capacity) is not int or not 1 <= capacity <= 100000:
            raise ValueError('Trace capacity must be an integer in [1,100000]')
        self.events = deque(maxlen=capacity)
        self.omitted = self.sequence = 0
        self.first_fault = None
        self.first_expiry = None
        self.last_tick_ns = None
        self.started_ns = time.monotonic_ns()
        self.metadata = dict(metadata or {}, fake=True, pid=os.getpid(),
                             python=platform.python_version(),
                             platform=platform.platform(),
                             monotonic_clock='time.monotonic_ns; compare only on same host/boot',
                             wall_time_ns=time.time_ns(), mono_anchor_ns=self.started_ns,
                             revision=os.environ.get('TELLO_REVISION', 'UNRECORDED'))
        # Exclusive reservation refuses existing evidence before any session arm.
        self.file = Path(path).expanduser().open('x', encoding='utf-8')

    def record(self, kind, **fields):
        self.sequence += 1
        event = dict(kind=kind, sequence=self.sequence, **fields)
        if len(self.events) == self.events.maxlen:
            self.omitted += 1
        self.events.append(event)
        return event

    @contextmanager
    def measure(self, kind, guard, **fields):
        start = time.monotonic_ns()
        before = snapshot(guard, start)
        if kind == 'policy_tick':
            fields['inter_tick_gap_ns'] = (
                None if self.last_tick_ns is None else start - self.last_tick_ns)
            self.last_tick_ns = start
        try:
            yield fields
        except BaseException as error:
            fields['exception'] = type(error).__name__
            raise
        finally:
            end = time.monotonic_ns()
            after = snapshot(guard, end)
            event = self.record(kind, start_ns=start, end_ns=end,
                                duration_ns=end - start, before=before,
                                after=after, **fields)
            if not before['landing_latched'] and after['landing_latched']:
                if self.first_fault is None:
                    self.first_fault = event
                if after['reason'] == 'OPERATOR_LEASE_EXPIRED' and self.first_expiry is None:
                    self.first_expiry = event

    def finish(self, completion='shutdown'):
        if self.file.closed:
            return
        report = dict(trace_version=1, metadata=self.metadata,
                      started_ns=self.started_ns, ended_ns=time.monotonic_ns(),
                      completion=completion, capacity=self.events.maxlen,
                      events_retained=len(self.events), events_omitted=self.omitted,
                      first_fault=self.first_fault, first_expiry=self.first_expiry,
                      events=list(self.events))
        try:
            json.dump(report, self.file, allow_nan=False)
            self.file.write('\n')
            self.file.flush()
        finally:
            self.file.close()


def source_hash(path):
    """Hash installed source at startup, outside timed callbacks."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
