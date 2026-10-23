#!/usr/bin/env python3
"""Audit a supervised one-axis flight trace and its ROS trial report offline."""

import argparse
import json
from pathlib import Path


NEUTRAL = 'rc 0 0 0 0'


def review(trace, report):
    errors = []
    warnings = []
    if type(trace) is not dict or type(report) is not dict:
        return {'pass': False, 'errors': ['Trace and report must be objects']}
    dry_run = report.get('dry_run') is True
    if (trace.get('fake') is not dry_run or
            trace.get('mode') != ('flight-dry-run' if dry_run else 'flight')):
        errors.append('Gateway mode does not match the ROS trial')
    if (trace.get('flight_session_id') != report.get('flight_session_id') or
            trace.get('flight_source_frame_id') != report.get('source_frame_id')):
        errors.append('Gateway and ROS trial identities differ')
    if trace.get('events_omitted') != 0 or trace.get('transport_events_omitted') != 0:
        errors.append('Trace omitted events')
    if (report.get('passed') is not True or type(report.get('dry_run')) is not bool or
            report.get('supervisor_fault') is not None
            or report.get('cleanup_error') or report.get('physical_outcome') != 'UNKNOWN'
            or type(report.get('approved_nonzero_count')) is not int
            or report['approved_nonzero_count'] < 1):
        errors.append('ROS trial report did not pass with complete neutral cleanup')
    lateral = report.get('requested_lateral')
    duration_ms = report.get('requested_duration_ms')
    if (type(lateral) is not int or lateral == 0 or abs(lateral) > 5 or
            type(duration_ms) is not int or not 50 <= duration_ms <= 300):
        errors.append('Trial value or duration is outside its bounded envelope')
        lateral = 0
        duration_ms = 0

    events = trace.get('events')
    if type(events) is not list:
        events = []
        errors.append('Missing gateway events')
    hover = [event for event in events if type(event) is dict and
             event.get('event') == 'pilot_hover_confirmation' and event.get('accepted')]
    arms = [event for event in events if type(event) is dict and
            event.get('event') == 'local_flight_arm' and event.get('accepted')]
    releases = [event for event in events if type(event) is dict and
                event.get('event') == 'trial_release' and event.get('accepted')]
    leases = [event for event in events if type(event) is dict and
              event.get('event') == 'operator_lease' and event.get('accepted')]
    if len(hover) != 1 or len(arms) != 1 or len(releases) != 1 or not leases:
        errors.append('Pilot hover, arm, release, or external lease is missing')
    if any(type(event) is dict and event.get('event') in (
            'operator_input', 'state_link_error', 'grounded_bench_link_error',
            'upstream_peer_rejected', 'decision_rejected', 'abort_latch',
            'land_attempt') for event in events):
        errors.append('Unexpected abort, link fault, or upstream peer')
    if arms and releases and (type(arms[0].get('mono_ns')) is not int or
                              type(releases[0].get('mono_ns')) is not int or
                              releases[0]['mono_ns'] <= arms[0]['mono_ns']):
        errors.append('Pilot release did not follow local arm')
    decisions = [event for event in events if type(event) is dict and
                 event.get('event') == 'decision' and event.get('accepted') and
                 event.get('tracking')]
    if not any(event.get('source_frame_id') == report.get('source_frame_id') and
               event.get('lateral') == lateral and event.get('vertical') == 0
               for event in decisions):
        errors.append('Expected signed trial decision was not accepted')
    if any(event.get('source_frame_id') != report.get('source_frame_id') or
           event.get('vertical') != 0 or
           (event.get('lateral') not in (0, lateral)) for event in decisions):
        errors.append('Unexpected approved movement source or axis')

    transport = trace.get('transport_events')
    if type(transport) is not list:
        transport = []
        errors.append('Missing transport events')
    attempted = [event for event in transport if type(event) is dict and
                 'attempt' in event]
    attempts = [event['attempt'] for event in attempted]
    commands = trace.get('command_attempts')
    sends = trace.get('os_send_results')
    if (type(commands) is not list or type(sends) is not list or
            any(type(item) is not dict for item in commands + sends) or
            [item.get('command') for item in commands] != attempts or
            [item.get('command') for item in sends] != attempts):
        errors.append('Command attempts or OS sends differ from transport trace')
        sends = []
    if any(type(item) is not dict or type(item.get('result')) is not int or
           type(item.get('command')) is not str or
           item['result'] != len(item['command'].encode('ascii', errors='ignore')) or
           type(item.get('observed_ns')) is not int for item in sends):
        errors.append('OS send was incomplete or missing')
    if not attempts or attempts[0] != 'command' or attempts[-1] != NEUTRAL:
        errors.append('SDK entry or final neutral is missing')
    expected = f'rc {lateral} 0 0 0'
    nonzero = [item for item in attempted if item['attempt'] == expected]
    if not nonzero:
        errors.append('No expected lateral RC packet was sent')
    if any(item not in ('command', NEUTRAL, expected) for item in attempts):
        errors.append('Forbidden, mixed-axis, or landing packet in lateral trial')
    if nonzero:
        first = attempted.index(nonzero[0])
        last = attempted.index(nonzero[-1])
        if any(item['attempt'] == NEUTRAL for item in attempted[first:last]):
            errors.append('Lateral motion restarted after a neutral packet')
        first_neutral_after = next((item for item in attempted[last + 1:]
                                    if item['attempt'] == NEUTRAL), None)
        if first_neutral_after is None:
            errors.append('No neutral packet followed lateral movement')
        elif (type(nonzero[0].get('mono_ns')) is not int or
              type(first_neutral_after.get('mono_ns')) is not int or
              first_neutral_after['mono_ns'] - nonzero[0]['mono_ns'] >
              (duration_ms + 300) * 1_000_000):
            errors.append('Lateral movement was not neutralized in time')
        times = [item.get('mono_ns') for item in nonzero]
        if (any(type(value) is not int for value in times) or
                (all(type(value) is int for value in times) and
                 times[-1] - times[0] > (duration_ms + 300) * 1_000_000)):
            errors.append('Nonzero wire interval exceeded the bounded trial')
        if arms and (type(arms[0].get('mono_ns')) is not int or
                     all(type(value) is int for value in times) and
                     (times[0] < arms[0]['mono_ns'] or
                      times[-1] - arms[0]['mono_ns'] > 60_000_000_000)):
            errors.append('Movement was outside the locally armed session')
        if releases and (type(releases[0].get('mono_ns')) is not int or
                         any(type(value) is not int for value in times) or
                         times[0] <= releases[0]['mono_ns']):
            errors.append('Movement preceded the pilot release')

    replies = trace.get('sdk_replies')
    if type(replies) is not list or any(
            type(item) is not dict or item.get('correlation') != 'UNCORRELATED_SDK_REPLY'
            for item in replies):
        errors.append('SDK replies are missing or falsely correlated')
    elif any(item.get('reply') == 'error' for item in replies):
        warnings.append('SDK error replies remain uncorrelated')
    observations = trace.get('physical_observations')
    if (type(observations) is not list or len(observations) != 1 or
            type(observations[0]) is not dict or
            observations[0].get('outcome') != 'UNKNOWN'):
        errors.append('Physical outcome must remain UNKNOWN in software trace')
    status = trace.get('final_status')
    if (type(status) is not dict or status.get('armed') is not False or
            status.get('landing_latched') is not False):
        errors.append('Gateway did not close in a disarmed, unlatched state')
    return {'pass': not errors,
            'scope': ('FAKE_SINGLE_AXIS_PATH_ONLY' if dry_run else
                      'SINGLE_AXIS_WIRE_AND_LOCAL_GATES_ONLY'),
            'physical_direction': 'UNCONFIRMED', 'errors': errors,
            'warnings': warnings, 'attempt_count': len(attempts),
            'nonzero_count': len(nonzero)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('gateway_trace', type=Path)
    parser.add_argument('ros_report', type=Path)
    args = parser.parse_args(argv)
    try:
        result = review(json.loads(args.gateway_trace.read_text(encoding='utf-8')),
                        json.loads(args.ros_report.read_text(encoding='utf-8')))
    except (OSError, json.JSONDecodeError) as error:
        result = {'pass': False, 'errors': [type(error).__name__]}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
