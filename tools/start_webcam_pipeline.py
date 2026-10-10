#!/usr/bin/env python3
"""Own launch shutdown and revoke software authority while ROS still exists."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace', default=os.environ.get('TELLO_NAMESPACE', 'webcam_test'))
    parser.add_argument('--bind', default=os.environ.get('TELLO_BIND', '127.0.0.1'))
    parser.add_argument('--port', type=int,
                        default=int(os.environ.get('TELLO_OBSERVATION_PORT', '5005')))
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('Invalid observation UDP port')
    stopping = False

    def request_stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    launch = subprocess.Popen([
        'ros2', 'launch', 'tello_bridge', 'webcam.launch.py',
        'namespace:=' + args.namespace, 'bind:=' + args.bind, 'port:=' + str(args.port)],
        start_new_session=True)
    print('Receiver/controller/supervisor/FAKE bridge; autonomy disabled. '
          'Ctrl+C requests takeover before stopping ROS.', flush=True)
    try:
        while launch.poll() is None and not stopping:
            time.sleep(0.05)
    finally:
        if launch.poll() is None:
            try:
                result = subprocess.run([
                    sys.executable, str(ROOT / 'src/tello_bridge/scripts/tello_operator'),
                    'takeover', '--namespace', args.namespace], timeout=12)
                if result.returncode:
                    print('Takeover cleanup incomplete; preserve logs.', flush=True)
            except subprocess.TimeoutExpired:
                print('Takeover cleanup timed out; stopping launch.', flush=True)
            launch.send_signal(signal.SIGINT)
            try:
                launch.wait(timeout=8)
            except subprocess.TimeoutExpired:
                launch.terminate()
                try:
                    launch.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(launch.pid, signal.SIGKILL)
                    launch.wait(timeout=2)
    return 0 if stopping else launch.returncode


if __name__ == '__main__':
    raise SystemExit(main())
