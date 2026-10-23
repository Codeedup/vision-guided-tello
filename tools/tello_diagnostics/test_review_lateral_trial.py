"""Reviewer must reject extra flight authority and overlong RC output."""

from copy import deepcopy

from review_lateral_trial import review


SESSION = 'c' * 32
FRAME = 'supervised_lateral_trial_' + SESSION


def fixture():
    commands = ['command', 'rc -2 0 0 0', 'rc -2 0 0 0', 'rc 0 0 0 0']
    times = [1, 100_000_000, 150_000_000, 160_000_000]
    trace = {
        'fake': False, 'mode': 'flight', 'flight_session_id': SESSION,
        'flight_source_frame_id': FRAME, 'events_omitted': 0,
        'transport_events_omitted': 0,
        'events': [
            {'event': 'pilot_hover_confirmation', 'accepted': True, 'mono_ns': 10},
            {'event': 'local_flight_arm', 'accepted': True, 'mono_ns': 20},
            {'event': 'operator_lease', 'accepted': True, 'mono_ns': 30},
            {'event': 'trial_release', 'accepted': True, 'mono_ns': 50},
            {'event': 'decision', 'accepted': True, 'tracking': True,
             'source_frame_id': FRAME, 'lateral': -2, 'vertical': 0,
             'mono_ns': 40},
        ],
        'transport_events': [
            {'attempt': command, 'mono_ns': stamp}
            for command, stamp in zip(commands, times)],
        'command_attempts': [
            {'command': command, 'observed_ns': stamp,
             'physical_outcome': 'UNKNOWN'}
            for command, stamp in zip(commands, times)],
        'os_send_results': [
            {'command': command, 'result': len(command), 'observed_ns': stamp}
            for command, stamp in zip(commands, times)],
        'sdk_replies': [],
        'physical_observations': [{'outcome': 'UNKNOWN'}],
        'final_status': {'armed': False, 'landing_latched': False},
    }
    report = {'flight_session_id': SESSION, 'source_frame_id': FRAME,
              'requested_lateral': -2, 'requested_duration_ms': 100,
              'physical_outcome': 'UNKNOWN', 'dry_run': False,
              'approved_nonzero_count': 2,
              'passed': True, 'supervisor_fault': None}
    return trace, report


def test_matching_bounded_trial_passes_without_claiming_direction():
    trace, report = fixture()
    result = review(trace, report)
    assert result['pass'], result['errors']
    assert result['physical_direction'] == 'UNCONFIRMED'


def test_matching_fake_rehearsal_passes_only_as_fake():
    trace, report = fixture()
    trace.update(fake=True, mode='flight-dry-run')
    report['dry_run'] = True
    for event in trace['transport_events']:
        event.update(fake=True, physical_outcome='UNKNOWN')
    for item in trace['os_send_results']:
        item['result'] = 'FAKE_SENT'
    result = review(trace, report)
    assert result['pass'], result['errors']
    assert result['scope'] == 'FAKE_SINGLE_AXIS_PATH_ONLY'
    report['dry_run'] = False
    assert not review(trace, report)['pass']


def test_rejects_extra_axis_landing_repeated_motion_and_long_interval():
    base, report = fixture()
    for mutation in ('axis', 'land', 'repeat', 'duration'):
        trace = deepcopy(base)
        if mutation == 'axis':
            trace['transport_events'][2]['attempt'] = 'rc -2 1 0 0'
            trace['command_attempts'][2]['command'] = 'rc -2 1 0 0'
            trace['os_send_results'][2]['command'] = 'rc -2 1 0 0'
        elif mutation == 'land':
            trace['transport_events'][2]['attempt'] = 'land'
            trace['command_attempts'][2]['command'] = 'land'
            trace['os_send_results'][2].update(command='land', result=4)
        elif mutation == 'repeat':
            trace['transport_events'].insert(2, {'attempt': 'rc 0 0 0 0',
                                                 'mono_ns': 125_000_000})
            trace['command_attempts'].insert(2, {'command': 'rc 0 0 0 0',
                                                 'observed_ns': 125_000_000})
            trace['os_send_results'].insert(2, {'command': 'rc 0 0 0 0',
                                                'result': 10,
                                                'observed_ns': 125_000_000})
        else:
            trace['transport_events'][2]['mono_ns'] = 700_000_000
        assert not review(trace, report)['pass'], mutation


def test_rejects_missing_lease_or_mismatched_run():
    trace, report = fixture()
    trace['events'] = [item for item in trace['events']
                       if item['event'] != 'operator_lease']
    assert not review(trace, report)['pass']
    trace, report = fixture()
    report['flight_session_id'] = 'd' * 32
    assert not review(trace, report)['pass']
