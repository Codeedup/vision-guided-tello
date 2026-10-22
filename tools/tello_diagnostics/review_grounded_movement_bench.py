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
    """Audit grounded-bench wire mapping and local abort evidence."""
    errors = []
    warnings = []
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
        replies = []
    else:
        for reply in replies:
            if (type(reply) is not dict or type(reply.get('reply')) is not str
                    or type(reply.get('observed_ns')) is not int
                    or reply.get('correlation') != 'UNCORRELATED_SDK_REPLY'):
                errors.append('SDK reply record is malformed or falsely correlated')
                break
        if any(type(reply) is dict and reply.get('reply') == 'error'
               for reply in replies):
            warnings.append('SDK error replies are uncorrelated; they cannot be '
                            'assigned to land or RC attempts')
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
    gateway_events = trace.get('events')
    if type(gateway_events) is not list:
        errors.append('Missing gateway lifecycle events')
        gateway_events = []
    operator_inputs = [event for event in gateway_events
                       if type(event) is dict and
                       event.get('event') == 'operator_input']
    if len(operator_inputs) != 1 or operator_inputs[0].get('cause') != 'space':
        errors.append('Focused Space abort event is missing or repeated')
    abort_dispatches = [event for event in gateway_events
                        if type(event) is dict and
                        event.get('event') == 'abort_dispatch']
    if (len(abort_dispatches) != 1 or
            abort_dispatches[0].get('attempts') != 1):
        errors.append('Abort dispatch must report exactly one land attempt')
    land_attempts = [event for event in gateway_events
                     if type(event) is dict and
                     event.get('event') == 'land_attempt']
    if len(land_attempts) != 1 or land_attempts[0].get('retry_number') != 1:
        errors.append('Grounded abort must make one bounded land attempt')
    if attempts.count('land') != 1:
        errors.append('Grounded abort must emit exactly one land packet')
    if len(attempts) < 3 or attempts[-3:] != [NEUTRAL, 'land', NEUTRAL]:
        errors.append('Abort must send neutral, one land packet, then final neutral')
    session = trace.get('grounded_bench_session')
    if (type(session) is not dict or type(session.get('started_ns')) is not int
            or type(session.get('maximum_duration_ns')) is not int
            or type(session.get('expired')) is not bool):
        errors.append('Missing bounded-session metadata')
    elif session['expired']:
        errors.append('Grounded-bench session expired before completion')
    elif (not events or type(events[-1]) is not dict
          or type(events[-1].get('mono_ns')) is not int
          or events[-1]['mono_ns'] - session['started_ns'] < 0
          or events[-1]['mono_ns'] - session['started_ns'] >
          session['maximum_duration_ns']):
        errors.append('Grounded-bench session exceeded its configured duration')
    if any(event.get('event') == 'bench_session_expired'
           for event in gateway_events if type(event) is dict):
        errors.append('Session expired before the full operator procedure completed')
    if attempts and attempts[-1] != NEUTRAL:
        errors.append('Final wire command is not neutral')
    return {'pass': not errors,
            'scope': 'WIRE_MAPPING_AND_LOCAL_ABORT_ONLY',
            'landing_delivery': 'UNCONFIRMED',
            'errors': errors, 'warnings': warnings, 'pulses': nonzero,
            'attempt_count': len(attempts), 'sdk_reply_count': len(replies)}


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
