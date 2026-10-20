"""Sequence evidence must reject brief TRACK, truncation, restart and bad provenance."""

from copy import deepcopy

from analyze_target_loss import analyze, main
import pytest


def fixture():
    revision = 'a' * 40
    epoch = 1_800_000_000_000_000_000
    info = {'label': 'run-1', 'commit': revision, 'working_tree_dirty': False}
    records = []
    for tick in range(151):
        elapsed = tick / 10
        tracking = 1 <= elapsed < 6.2
        landed = elapsed >= 7.7
        absent = 6.2 <= elapsed < 9.5
        stamp = epoch + tick * 100_000_000
        records.extend([
            {'topic': 'hand_target', 'elapsed_s': elapsed,
             'source_stamp_ns': stamp,
             'flags': [False, 0] if absent else [True, 1]},
            {'topic': 'approved_command', 'elapsed_s': elapsed,
             'source_stamp_ns': stamp, 'decision_stamp_ns': stamp + 1,
             'flags': [True, tracking, landed],
             'values': {'lateral': 1 if tracking else 0, 'vertical': 0}}])
    monitor = {'metadata': info, 'namespace': '/test', 'message_records': records}
    trace_info = {'label': 'run-1', 'revision': revision, 'fake': True, 'namespace': '/test'}
    bridge = {'metadata': dict(trace_info, role='bridge'), 'completion': 'shutdown',
              'events': [{'after': {'reason': 'TRACKING'}}],
              'first_fault': {'after': {'reason': 'SUPERVISOR_LAND'}}}
    operator = {'metadata': dict(trace_info, role='operator'), 'completion': 'operator_exit',
                'events': [{'kind': 'service_call'}]}
    sender = {'metadata': dict(trace_info, model_sha256='fixture', working_tree_dirty=False),
              'completion': 'completed', 'frames': [{'capture_time_ns': epoch}]}
    clock = {'schema': 'windows_wsl_clock_bounds', 'label': 'run-1',
             'lower_ms': -0.1, 'upper_ms': 0.1, 'checked_at_ns': epoch - 1_000_000_000}
    return {'monitor': monitor, 'bridge': bridge, 'operator': operator,
            'sender': sender, 'clock': clock}


def test_complete_synthetic_sequence_passes():
    result = analyze(**fixture())
    assert result['passed'], result['errors']
    assert result['maximum_completed_track_s'] == pytest.approx(5.2)
    assert result['observed_loss_to_land_s'] == pytest.approx(1.5)


@pytest.mark.parametrize('defect', ['brief', 'truncated', 'restart', 'gap', 'clock',
                                    'revision', 'missing', 'stale', 'no_return', 'nonneutral'])
def test_invalid_or_incomplete_evidence_cannot_pass(defect):
    reports = deepcopy(fixture())
    records = reports['monitor']['message_records']
    if defect == 'brief':
        for record in records:
            if record['topic'] == 'approved_command' and record['elapsed_s'] < 6:
                record['flags'][1] = False
                record['values']['lateral'] = 0
    elif defect == 'truncated':
        reports['bridge']['events_omitted'] = 1
    elif defect == 'restart':
        records[-1]['flags'] = [True, True, False]
    elif defect == 'gap':
        reports['monitor']['message_records'] = [r for r in records if not 3 < r['elapsed_s'] < 4]
    elif defect == 'clock':
        reports['clock']['checked_at_ns'] += 100_000_000_000
    elif defect == 'revision':
        reports['sender']['metadata']['revision'] = 'other'
    elif defect == 'missing':
        reports['operator'] = None
    elif defect == 'stale':
        records[21]['source_stamp_ns'] -= 250_000_000
    elif defect == 'no_return':
        reports['monitor']['message_records'] = [r for r in records if r['elapsed_s'] < 12]
    else:
        records[-1]['values']['lateral'] = 1
    assert not analyze(**reports)['passed']


def test_invalid_json_reports_failure_and_preserves_existing_output(tmp_path):
    path = tmp_path / 'bad.json'
    path.write_text('{')
    assert main(['--monitor', str(path)]) == 1
    path.write_text('original')
    with pytest.raises(FileExistsError):
        main(['--monitor', str(path), '--output', str(path)])
    assert path.read_text() == 'original'


def test_hand_removal_after_landing_must_fail():
    reports = fixture()
    records = reports['monitor']['message_records']

    # Extend the recording so five seconds of returned hand are visible.
    templates = deepcopy(records[-2:])
    for tick in range(151, 201):
        for template in templates:
            record = deepcopy(template)
            record['elapsed_s'] = tick / 10
            if record['topic'] == 'approved_command':
                stamp = (
                    reports['sender']['frames'][0]['capture_time_ns']
                    + tick * 100_000_000
                )
                record['source_stamp_ns'] = stamp
                record['decision_stamp_ns'] = stamp + 1
            records.append(record)

    # Tracking still stops at 6.2 s and landing starts at 7.7 s.
    # Move hand removal to 9.0 s, with return at 12.1 s.
    for record in records:
        if record['topic'] == 'hand_target':
            absent = 9.0 <= record['elapsed_s'] < 12.1
            record['flags'] = [False, 0] if absent else [True, 1]

    result = analyze(**reports)

    assert not result['passed'], (
        'Analyzer accepted hand removal after landing: '
        + str(result)
    )


@pytest.mark.parametrize(
    'offset_s, expected',
    [
        (-0.250001, False),
        (-0.25, True),
        (-0.20, True),
        (0.0, True),
        (0.20, True),
        (0.25, True),
        (0.250001, False),
    ],
)
def test_removal_loss_observation_tolerance(offset_s, expected):
    reports = fixture()
    for record in reports['monitor']['message_records']:
        # Shift hand receipt times relative to approved-command receipt times.
        record['elapsed_s'] += 1.0
        if record['topic'] == 'hand_target':
            record['elapsed_s'] += offset_s

    result = analyze(**reports)

    assert result['passed'] is expected, result


def test_interrupted_hand_absence_must_fail():
    reports = fixture()
    for record in reports['monitor']['message_records']:
        if record['topic'] == 'hand_target' and record['elapsed_s'] == 8.0:
            record['flags'] = [True, 1]

    result = analyze(**reports)

    assert not result['passed'], result


@pytest.mark.parametrize('offset_ns, expected', [
    (-250_000_000, False), (-249_999_999, True), (0, True), (1, False),
])
def test_no_hand_without_prior_removal_must_precede_loss(offset_ns, expected):
    reports = fixture()
    records = reports['monitor']['message_records']
    loss_decision = next(r['decision_stamp_ns'] for r in records
                         if r['topic'] == 'approved_command' and r['elapsed_s'] == 6.2)
    first_absence = next(r for r in records
                         if r['topic'] == 'hand_target' and r['flags'] == [False, 0])
    first_absence['source_stamp_ns'] = loss_decision + offset_ns

    result = analyze(**reports)

    assert result['passed'] is expected, result


@pytest.mark.parametrize('age_ns, expected', [
    (249_999_999, True), (250_000_000, False),
])
def test_tracking_freshness_boundary(age_ns, expected):
    reports = fixture()
    tracking = next(r for r in reports['monitor']['message_records']
                    if r['topic'] == 'approved_command' and r['flags'] == [True, True, False])
    tracking['source_stamp_ns'] = tracking['decision_stamp_ns'] - age_ns

    result = analyze(**reports)

    assert result['passed'] is expected, result


def test_partial_hand_removal_before_loss_can_precede_no_hand():
    reports = fixture()
    records = reports['monitor']['message_records']
    loss_decision = next(r['decision_stamp_ns'] for r in records
                         if r['topic'] == 'approved_command' and r['elapsed_s'] == 6.2)
    first_absence = next(i for i, r in enumerate(records)
                         if r['topic'] == 'hand_target' and r['flags'] == [False, 0])
    records.insert(first_absence, {
        'topic': 'hand_target', 'elapsed_s': 6.19,
        'source_stamp_ns': loss_decision - 1_000_000,
        'flags': [False, 1],
    })
    records[first_absence + 1]['source_stamp_ns'] = loss_decision + 500_000

    assert analyze(**reports)['passed']

    records[first_absence]['flags'] = [False, 2]
    assert not analyze(**reports)['passed']


def test_brief_return_reacquisition_flicker_then_five_seconds_passes():
    reports = fixture()
    for record in reports['monitor']['message_records']:
        if record['topic'] == 'hand_target' and record['elapsed_s'] == 9.6:
            record['flags'] = [False, 1]

    result = analyze(**reports)

    assert result['passed'], result
    assert result['hand_return_elapsed_s'] == pytest.approx(9.7)


def test_bridge_input_loss_watchdog_is_valid_but_unrelated_fault_is_not():
    reports = fixture()
    loss_decision = next(r['decision_stamp_ns']
                         for r in reports['monitor']['message_records']
                         if r['topic'] == 'approved_command' and r['elapsed_s'] == 6.2)
    reports['bridge']['metadata'].update(wall_time_ns=loss_decision,
                                         mono_anchor_ns=0)
    reports['bridge']['first_fault'] = {
        'kind': 'policy_tick',
        'start_ns': 1_500_000_000,
        'before': {'reason': 'NEUTRAL_INPUT_EXPIRED_OR_INVALID'},
        'after': {'reason': 'APPROVED_INPUT_LOSS'},
    }
    assert analyze(**reports)['passed']

    reports['bridge']['first_fault']['start_ns'] = 500_000_000
    assert not analyze(**reports)['passed']

    reports['bridge']['first_fault']['start_ns'] = 1_500_000_000
    reports['bridge']['first_fault']['after']['reason'] = 'SESSION_DEADLINE'
    assert not analyze(**reports)['passed']
