from review_grounded_movement_bench import review


def trace(attempts):
    return {
        'fake': False, 'mode': 'grounded-bench',
        'events_omitted': 0, 'transport_events_omitted': 0,
        'transport_events': [{'attempt': attempt} for attempt in attempts],
    }


def test_reviewer_accepts_capped_single_axis_pulses_followed_by_neutral():
    result = review(trace(['command', 'rc 5 0 0 0', 'rc 0 0 0 0',
                           'rc 0 0 -5 0', 'rc 0 0 0 0']))
    assert result['pass']


def test_reviewer_rejects_mixed_or_non_neutral_terminal_commands():
    result = review(trace(['command', 'rc 5 0 5 0']))
    assert not result['pass']
    assert any('Forbidden' in error for error in result['errors'])
