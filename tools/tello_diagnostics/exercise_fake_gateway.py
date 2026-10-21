#!/usr/bin/env python3
"""Send data-only synthetic decisions to the FAKE Windows gateway."""

import argparse
import json
from pathlib import Path
import socket
import tempfile
import time


def main(argv=None):
    """Send bounded decisions to an explicitly named FAKE listener."""
    parser = argparse.ArgumentParser(
        description='FAKE gateway decision exercise')
    parser.add_argument('--receiver', required=True,
                        help='Explicit Windows IPv4')
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--arm-timeout', type=float, default=60)
    parser.add_argument('--moving-seconds', type=float, default=10)
    args = parser.parse_args(argv)
    socket.inet_aton(args.receiver)
    if (not 1 <= args.port <= 65535
            or not 0 < args.arm_timeout <= 300
            or not 0 < args.moving_seconds <= 60):
        parser.error('Invalid port or duration')
    marker = Path(tempfile.gettempdir()) / (
        f'tello-fake-gateway-{args.port}.armed')
    started_wall_ns = time.time_ns()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        print('Sending disabled decisions; prepare and arm FAKE in the gateway',
              flush=True)
        arm_deadline = time.monotonic() + args.arm_timeout
        phase = 'disabled'
        moving_deadline = None
        while True:
            if phase == 'disabled':
                try:
                    armed = marker.stat().st_mtime_ns >= started_wall_ns
                except FileNotFoundError:
                    armed = False
                if armed:
                    phase = 'tracking'
                    moving_deadline = time.monotonic() + args.moving_seconds
                    print('FAKE arm observed; sending tracking decisions',
                          flush=True)
                elif time.monotonic() >= arm_deadline:
                    raise TimeoutError('No local FAKE arm within timeout')
            if phase == 'tracking' and time.monotonic() >= moving_deadline:
                break
            else:
                stamp = time.time_ns()
                tracking = phase == 'tracking'
                data = {'version': 1, 'decision_ns': stamp,
                        'source_ns': stamp, 'enabled': tracking,
                        'tracking': tracking, 'land': False,
                        'lateral': 5 if tracking else 0,
                        'vertical': -4 if tracking else 0}
                sock.sendto(json.dumps(data).encode(),
                            (args.receiver, args.port))
                time.sleep(0.05)
    print('done', flush=True)


if __name__ == '__main__':
    main()
