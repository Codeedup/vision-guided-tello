import pytest
from review_grounded_movement_bench import review


def trace(attempts):
    transport_events = [
        {'attempt': attempt, 'mono_ns': 1, 'attempt_observed_ns': 2,
         'os_send_observed_ns': 3,
         'os_send_result': len(attempt.encode('ascii')),
         'physical_outcome': 'UNKNOWN'}
        for attempt in attempts
    ]
    return {
        'fake': False, 'mode': 'grounded-bench',
        'events_omitted': 0, 'transport_events_omitted': 0,
        'decision_receipts': [{'event': 'bench_pulse'}],
        'command_attempts': [
            {'command': event['attempt'], 'mono_ns': event['mono_ns'],
             'observed_ns': event['attempt_observed_ns'],
             'physical_outcome': event['physical_outcome']}
            for event in transport_events],
        'os_send_results': [
            {'command': event['attempt'], 'result': event['os_send_result'],
             'observed_ns': event['os_send_observed_ns']}
            for event in transport_events],
        'sdk_replies': [],
        'physical_observations': [{'outcome': 'UNKNOWN'}],
        'transport_events': transport_events,
    }


def test_reviewer_accepts_capped_single_axis_pulses_followed_by_neutral():
    result = review(trace(['command', 'rc 5 0 0 0', 'rc 0 0 0 0',
                           'rc -5 0 0 0', 'rc 0 0 0 0',
                           'rc 0 0 5 0', 'rc 0 0 0 0',
                           'rc 0 0 -5 0', 'rc 0 0 0 0']))
    assert result['pass']


def test_reviewer_rejects_mixed_or_non_neutral_terminal_commands():
    result = review(trace(['command', 'rc 5 0 5 0']))
    assert not result['pass']
    assert any('Forbidden' in error for error in result['errors'])


def test_reviewer_requires_distinct_layer_records_and_unknown_physical_outcome():
    value = trace(['command', 'rc 5 0 0 0', 'rc 0 0 0 0'])
    value.pop('os_send_results')
    value['physical_observations'][0]['outcome'] = 'LANDED'
    result = review(value)
    assert not result['pass']
    assert any('OS send' in error for error in result['errors'])
    assert any('UNKNOWN' in error for error in result['errors'])


def test_reviewer_rejects_incomplete_os_send_byte_count():
    value = trace(['command', 'rc 5 0 0 0', 'rc 0 0 0 0'])
    value['os_send_results'][1]['result'] -= 1
    result = review(value)
    assert not result['pass']
    assert any('byte-count' in error for error in result['errors'])


def test_reviewer_rejects_incomplete_axis_set():
    result = review(trace(['command', 'rc 5 0 0 0', 'rc 0 0 0 0']))
    assert not result['pass']
    assert any('All four' in error for error in result['errors'])


@pytest.mark.parametrize('attempts, expected_error', [
    (['command', 'rc 5 0 0 0', 'rc 0 0 0 0',
      'rc 5 0 0 0', 'rc 0 0 0 0'], 'at most once'),
    (['command', 'rc 5 0 0 0', 'land'], 'immediately neutralized'),
    (['command', 'rc 0 0 5 0'], 'Final wire command'),
    (['command', 'forward 10'], 'Forbidden wire command'),
    (['command', 'rc 6 0 0 0', 'rc 0 0 0 0'], 'Forbidden wire command'),
])
def test_reviewer_rejects_unsafe_pulse_sequences(attempts, expected_error):
    result = review(trace(attempts))
    assert not result['pass']
    assert any(expected_error in error for error in result['errors'])


def test_reviewer_rejects_omitted_evidence_and_missing_sdk_reply_layer():
    value = trace(['command', 'rc 0 0 0 0'])
    value['transport_events_omitted'] = 1
    value.pop('sdk_replies')
    result = review(value)
    assert not result['pass']
    assert any('omitted' in error for error in result['errors'])
    assert any('SDK reply' in error for error in result['errors'])
