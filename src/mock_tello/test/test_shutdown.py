"""Signals during a mock callback must leave ROS alive until that callback returns."""

import csv
import signal
import subprocess
import sys

import pytest


@pytest.mark.parametrize('stop_signal', [signal.SIGINT, signal.SIGTERM])
def test_signal_during_callback_defers_context_shutdown(tmp_path, stop_signal):
    # A self-signal gives a deterministic callback/signal overlap. Default rclpy
    # SIGINT handling interrupts this callback; deferred teardown must complete it.
    code = """
import os
import signal
import time
import rclpy
from mock_tello.node import MockTello, main
original = MockTello.on_step
def step(self):
    original(self)
    os.kill(os.getpid(), int(os.environ['MOCK_STOP_SIGNAL']))
    time.sleep(0.1)
    assert rclpy.ok(), 'Context was shut down inside a callback'
    original(self)
    print('CALLBACK_COMPLETED_WITH_LIVE_CONTEXT', flush=True)
MockTello.on_step = step
main()
assert not rclpy.ok()
print('ORDERED_SHUTDOWN_COMPLETED', flush=True)
"""
    import os
    path = tmp_path / 'state.csv'
    result = subprocess.run(
        [sys.executable, '-c', code, '--ros-args', '-p', 'log_path:=' + str(path)],
        env=dict(os.environ, MOCK_STOP_SIGNAL=str(int(stop_signal))),
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'CALLBACK_COMPLETED_WITH_LIVE_CONTEXT' in result.stdout
    assert 'ORDERED_SHUTDOWN_COMPLETED' in result.stdout
    with path.open() as stream:
        assert 'run_index' in next(csv.reader(stream))
