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
