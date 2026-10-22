#!/usr/bin/env python3
"""Review a create-only grounded movement-gateway trace without aircraft access."""

import argparse
import json
from pathlib import Path


PULSES = frozenset({
    'rc 5 0 0 0', 'rc -5 0 0 0', 'rc 0 0 5 0', 'rc 0 0 -5 0',
})
NEUTRAL = 'rc 0 0 0 0'


def review(trace):
    """Return a deterministic pass/fail report for a grounded-bench trace."""
    errors = []
    if type(trace) is not dict:
        return {'pass': False, 'errors': ['Trace must be an object']}
    if trace.get('fake') is not False or trace.get('mode') != 'grounded-bench':
        errors.append('Trace is not a real grounded-bench session')
    if trace.get('events_omitted') != 0 or trace.get('transport_events_omitted') != 0:
        errors.append('Trace has omitted events')
    events = trace.get('transport_events')
    if type(events) is not list:
        errors.append('Missing transport events')
        events = []
    attempts = [event.get('attempt') for event in events if type(event) is dict
                and 'attempt' in event]
    receipts = trace.get('decision_receipts')
    if type(receipts) is not list:
        errors.append('Missing decision receipt records')
    command_records = trace.get('command_attempts')
    if type(command_records) is not list:
        errors.append('Missing distinct command attempt records')
        command_records = []
    if [record.get('command') for record in command_records
            if type(record) is dict] != attempts:
        errors.append('Command attempt records do not match transport attempts')
    if any(type(record) is not dict or type(record.get('observed_ns')) is not int
           for record in command_records):
        errors.append('Command attempt records need monotonic observation times')
    send_records = trace.get('os_send_results')
    if type(send_records) is not list:
        errors.append('Missing distinct OS send result records')
        send_records = []
    if [record.get('command') for record in send_records
            if type(record) is dict] != attempts:
        errors.append('OS send records do not match command attempts')
    else:
        for record in send_records:
            result = record.get('result')
            command = record.get('command')
            if (type(result) is not int or type(command) is not str
                    or result != len(command.encode('ascii', errors='ignore'))
                    or type(record.get('observed_ns')) is not int):
                errors.append('OS send failed or has no exact byte-count result')
                break
    replies = trace.get('sdk_replies')
    if type(replies) is not list:
        errors.append('Missing SDK reply records')
    observations = trace.get('physical_observations')
    if (type(observations) is not list or len(observations) != 1
            or type(observations[0]) is not dict
            or observations[0].get('outcome') != 'UNKNOWN'):
        errors.append('Physical outcome must be recorded as UNKNOWN')
    if any(type(record) is not dict or record.get('physical_outcome') != 'UNKNOWN'
           for record in command_records):
        errors.append('Command attempt claims or omits physical outcome')
    if not attempts or attempts[0] != 'command':
        errors.append('SDK entry command is missing')
    for index, attempt in enumerate(attempts):
        if attempt in PULSES:
            if index + 1 >= len(attempts) or attempts[index + 1] != NEUTRAL:
                errors.append('Each nonzero pulse must be immediately neutralized')
        elif attempt not in {'command', NEUTRAL, 'land'}:
            errors.append('Forbidden wire command: ' + repr(attempt))
    nonzero = [attempt for attempt in attempts if attempt in PULSES]
    if len(nonzero) != len(set(nonzero)):
        errors.append('Each signed axis may be pulsed at most once per bench')
    if set(nonzero) != PULSES:
        errors.append('All four signed single-axis checks must be present')
    if attempts and attempts[-1] != NEUTRAL:
        errors.append('Final wire command is not neutral')
    return {'pass': not errors, 'errors': errors, 'pulses': nonzero,
            'attempt_count': len(attempts)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace', type=Path)
    args = parser.parse_args(argv)
    try:
        result = review(json.loads(args.trace.read_text(encoding='utf-8')))
    except (OSError, json.JSONDecodeError) as error:
        result = {'pass': False, 'errors': [type(error).__name__]}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
