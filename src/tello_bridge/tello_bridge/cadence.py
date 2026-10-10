"""Paced serial heartbeats without catch-up bursts or extra executor callbacks."""

import time


def run_heartbeats(heartbeat, spin_once, duration=None, *, legacy=False,
                   clock=time.monotonic):
    """
    Service executor work while waiting for an explicit 80 ms send interval.

    Each deadline starts at the actual send attempt. Late execution skips missed
    slots; only one request can be in flight. Exceptions/SIGINT propagate to the
    existing session cleanup. Legacy pacing is for explicit FAKE comparisons.
    """
    deadline = None if duration is None else clock() + duration
    next_send = clock()
    while True:
        now = clock()
        if deadline is not None and now >= deadline:
            return
        if not legacy and now < next_send:
            wait = min(0.02, next_send - now)
            if deadline is not None:
                wait = min(wait, deadline - now)
            spin_once(timeout_sec=wait)
            continue
        next_send = now + 0.08
        heartbeat()
        if legacy:
            spin_once(timeout_sec=0.08)
