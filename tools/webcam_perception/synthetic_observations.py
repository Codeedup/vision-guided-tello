#!/usr/bin/env python3
"""Deterministic synthetic observations, loopback only by default; never opens a camera."""

import argparse
import ipaddress
import json
import math
import socket
import time


def observation(stamp, mode='valid', x=0.6, y=-0.4):
    detected = mode not in ('invalid', 'two_hands')
    return {'capture_time_ns': stamp, 'detected': detected,
            'error_x': x if detected else 0.0, 'error_y': y if detected else 0.0,
            'tracked_hands': 2 if mode == 'two_hands' else (1 if detected else 0)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receiver', default='127.0.0.1')
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--duration', type=float, default=5)
    parser.add_argument('--hz', type=float, default=20)
    scenarios = ('baseline', 'loss', 'dropout', 'stale', 'future', 'replay', 'malformed')
    parser.add_argument('--scenario', choices=scenarios, default='baseline')
    args = parser.parse_args(argv)
    try:
        loopback = ipaddress.ip_address(args.receiver).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        parser.error('Synthetic sender requires a numeric loopback destination')
    if not 1 <= args.port <= 65535:
        parser.error('Invalid UDP port')
    if not (math.isfinite(args.duration) and 0 < args.duration <= 600 and
            math.isfinite(args.hz) and 1 <= args.hz <= 120):
        parser.error('duration in (0,600], hz in [1,120] required')
    initial = time.time_ns()
    start = time.monotonic()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        tick = 0
        try:
            while time.monotonic() - start < args.duration:
                elapsed = time.monotonic() - start
                stamp = time.time_ns()
                mode = 'valid'
                if args.scenario == 'loss' and elapsed >= 2:
                    mode = 'invalid'
                if args.scenario == 'dropout' and 2 <= elapsed < 2.5:
                    mode = 'invalid'
                if args.scenario == 'stale':
                    stamp -= 300_000_000
                if args.scenario == 'future':
                    stamp += 1_000_000_000
                if args.scenario == 'replay' and elapsed >= 2:
                    stamp = initial
                packet = json.dumps(observation(stamp, mode), allow_nan=False).encode()
                if args.scenario == 'malformed' and tick % 5 == 0:
                    sender.sendto(b'not json', (args.receiver, args.port))
                sender.sendto(packet, (args.receiver, args.port))
                tick += 1
                delay = start + tick / args.hz - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
