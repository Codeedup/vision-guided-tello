#!/usr/bin/env python3
"""Audit a grounded SDK bench trace without claiming physical state."""

import argparse
import ipaddress
import json
from pathlib import Path

from grounded_sdk_bench import NEUTRAL, SAFE_QUERIES


def review(report):
    """Check the trace for allowed sends and distinct SDK receipts."""
    errors = []
    events = report.get('events', [])
    transport = report.get('transport_events', [])
    sends = [e for e in transport if 'attempt' in e]
    replies = [e for e in transport if 'reply' in e]
    attempts = [e['attempt'] for e in sends]
    if report.get('mode') != 'GROUNDED_REAL_SDK_BENCH':
        errors.append('wrong mode')
    if report.get('fake') is not False:
        errors.append('real SDK transport not evidenced')
    if report.get('sdk_peer') != ['192.168.10.1', 8889]:
        errors.append('SDK peer is not the Tello default endpoint')
    endpoint = report.get('local_endpoint')
    try:
        local_ip = ipaddress.IPv4Address(endpoint[0])
        local_port = endpoint[1]
        valid_local = (local_ip in ipaddress.IPv4Network('192.168.10.0/24')
                       and local_ip not in (
                           ipaddress.IPv4Address('192.168.10.0'),
                           ipaddress.IPv4Address('192.168.10.1'),
                           ipaddress.IPv4Address('192.168.10.255'))
                       and local_port == 8889)
    except (TypeError, ValueError, IndexError):
        valid_local = False
    if not valid_local:
        errors.append('local endpoint is not Windows Tello Wi-Fi SDK port')
    if report.get('outcome') != 'COMPLETED':
        errors.append('bench did not complete')
    if report.get('events_omitted') or report.get('transport_events_omitted'):
        errors.append('trace events omitted')
    if attempts[:3] != list(SAFE_QUERIES):
        errors.append('SDK entry/query order missing')
    if not attempts or attempts[-1] != NEUTRAL:
        errors.append('final neutral send missing')
    if any(command not in SAFE_QUERIES and command != NEUTRAL
           for command in attempts):
        errors.append('command outside grounded allowlist')
    if any(e.get('os_send_result') != len(e['attempt']) for e in sends):
        errors.append('OS send failure or incomplete send')
    if len(replies) < 3 or any(e.get('acknowledgement') !=
                               'UNCORRELATED_SDK_REPLY' for e in replies):
        errors.append('SDK replies missing or mislabeled')
    if not any(e.get('state') == 'STALE_OR_LOST_INPUT' for e in events):
        errors.append('stale/lost input state not observed')
    if not events or events[-1].get('kind') != 'shutdown':
        errors.append('shutdown event missing')
    if report.get('physical_outcome') != 'UNKNOWN':
        errors.append('physical outcome overstated')
    return {'pass': not errors, 'errors': errors, 'commands_sent': len(sends),
            'sdk_replies_received': len(replies),
            'physical_outcome': report.get('physical_outcome')}


def main(argv=None):
    """Review one saved trace from the CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace')
    args = parser.parse_args(argv)
    result = review(json.loads(Path(args.trace).read_text(encoding='utf-8')))
    print(json.dumps(result, indent=2))
    return 0 if result['pass'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
