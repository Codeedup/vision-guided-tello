#!/usr/bin/env python3
"""Send data-only synthetic decisions to the FAKE Windows gateway."""

import argparse
import json
import socket
import time


def main(argv=None):
    """Send bounded decisions to an explicitly named FAKE listener."""
    parser = argparse.ArgumentParser(
        description='FAKE gateway decision exercise')
    parser.add_argument('--receiver', required=True,
                        help='Explicit Windows IPv4')
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--disabled-seconds', type=float, default=10)
    parser.add_argument('--moving-seconds', type=float, default=10)
    args = parser.parse_args(argv)
    socket.inet_aton(args.receiver)
    if (not 1 <= args.port <= 65535
            or not 0 < args.disabled_seconds <= 60
            or not 0 < args.moving_seconds <= 60):
        parser.error('Invalid port or duration')
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for phase, duration in (('disabled', args.disabled_seconds),
                                ('tracking', args.moving_seconds)):
            print(phase, flush=True)
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline:
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
