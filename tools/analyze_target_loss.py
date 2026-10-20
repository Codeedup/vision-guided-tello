#!/usr/bin/env python3
"""Conservative FAKE target-loss evidence audit; never infer physical outcomes."""

import argparse
import hashlib
import json
import math
from pathlib import Path


def analyze(monitor, bridge=None, operator=None, sender=None, clock=None):
    """Require a complete, paired tracking/loss/return sequence before a pass."""
    errors = []
    reports = {'monitor': monitor, 'bridge': bridge, 'operator': operator, 'sender': sender}
    metadata = monitor.get('metadata', {})
    label = metadata.get('label')
    revision = metadata.get('commit')
    if not label or not revision or metadata.get('working_tree_dirty') is not False:
        errors.append('Monitor requires a run ID and clean recorded revision')
    for name, report in reports.items():
        if report is None:
            errors.append('Missing paired ' + name + ' evidence')
            continue
        info = report.get('metadata', {})
        if info.get('label') != label or (info.get('revision') or info.get('commit')) != revision:
            errors.append('Run ID/revision mismatch: ' + name)
        omitted = sum(report.get(key, 0) for key in (
            'events_omitted', 'frames_omitted', 'state_transitions_omitted',
            'message_records_omitted'))
        if omitted:
            errors.append('Truncated evidence: ' + name)
    for name, report, completion in (('bridge', bridge, 'shutdown'),
                                     ('operator', operator, 'operator_exit')):
        if report is not None:
            info = report.get('metadata', {})
            if (report.get('completion') != completion or info.get('fake') is not True
                    or info.get('role') != name
                    or info.get('namespace') != monitor.get('namespace')):
                errors.append('Incomplete or wrong FAKE ' + name + ' trace')
            if not report.get('events'):
                errors.append('Empty ' + name + ' trace')
    if bridge is not None:
        events = bridge.get('events', [])
        if not any(e.get('after', {}).get('reason') == 'TRACKING' for e in events):
            errors.append('Bridge never evidenced actual FAKE tracking authority')
        fault = bridge.get('first_fault') or {}
        cause = fault.get('after', {}).get('reason')
        watchdog_loss = (cause == 'APPROVED_INPUT_LOSS' and
                         fault.get('kind') == 'policy_tick' and
                         fault.get('before', {}).get('reason') ==
                         'NEUTRAL_INPUT_EXPIRED_OR_INVALID')
        if cause != 'SUPERVISOR_LAND' and not watchdog_loss:
            errors.append('Bridge first landing cause was not target/input loss')
        if any(e.get('before', {}).get('landing_latched') and
               e.get('after', {}).get('landing_latched') is False for e in events):
            errors.append('Bridge landing latch was cleared during the run')
    if sender is not None:
        if sender.get('completion') not in ('completed', 'quit'):
            errors.append('Sender did not complete normally')
        if not sender.get('metadata', {}).get('model_sha256'):
            errors.append('Sender model hash missing')
        if sender.get('metadata', {}).get('working_tree_dirty') is not False:
            errors.append('Sender revision is dirty or unknown')
    if clock is None or clock.get('schema') != 'windows_wsl_clock_bounds':
        errors.append('Missing matched Windows/WSL clock evidence')
    else:
        lower, upper = clock.get('lower_ms'), clock.get('upper_ms')
        checked = clock.get('checked_at_ns')
        frames = [] if sender is None else sender.get('frames', [])
        first_stamp = None if not frames else frames[0].get('capture_time_ns')
        bounds_valid = all(type(v) in (int, float) and math.isfinite(v) for v in (lower, upper))
        if (clock.get('label') != label or not bounds_valid or
                not -10 <= lower <= upper <= 10):
            errors.append('Clock bounds missing, mismatched or wider than +/-10 ms')
        if (type(checked) is not int or type(first_stamp) is not int or
                not 0 <= first_stamp - checked <= 600_000_000_000):
            errors.append('Clock check must precede this sender by at most 10 minutes')
    if monitor.get('missing_topics') or monitor.get('source_age_clock_warning'):
        errors.append('Monitor has missing topics or a clock warning')
    if monitor.get('nonfinite_command_or_error_values') or monitor.get(
            'nonneutral_messages_without_tracking'):
        errors.append('Invalid numerical or nonneutral nontracking output')
    records = monitor.get('message_records', [])
    approved = [r for r in records if r['topic'] == 'approved_command']
    hands = [r for r in records if r['topic'] == 'hand_target']
    if not approved or not hands:
        errors.append('Missing approved/hand message records')
    for series in (approved, hands):
        if any(not 0 <= b['elapsed_s'] - a['elapsed_s'] <= 0.25
               for a, b in zip(series, series[1:])):
            errors.append('Observation gap/reorder prevents continuous sequence evidence')
    for r in approved:
        enabled, tracking, land = r['flags']
        values = list(r['values'].values())
        if (not all(type(v) in (int, float) and math.isfinite(v) and abs(v) <= 10
                    for v in values) or (not tracking and any(v != 0 for v in values))):
            errors.append('Invalid approved command values')
            break
        if tracking:
            source, decision = r.get('source_stamp_ns'), r.get('decision_stamp_ns')
            if (not enabled or land or type(source) is not int or type(decision) is not int
                    or not 0 <= decision - source < 250_000_000):
                errors.append('Tracking decision has invalid flags/source freshness')
                break
    intervals = []
    start = None
    for record in approved:
        tracking = record['flags'] == [True, True, False]
        if tracking and start is None:
            start = record['elapsed_s']
        elif not tracking and start is not None:
            intervals.append((start, record['elapsed_s']))
            start = None
    maximum_track = max((end - begin for begin, end in intervals), default=0)
    loss = next((end for begin, end in intervals if end - begin >= 5), None)
    loss_record = next((r for r in approved if r['elapsed_s'] == loss), None)
    landing = next((r['elapsed_s'] for r in approved if r['flags'] == [True, False, True]
                    and loss is not None and r['elapsed_s'] >= loss), None)
    if loss is None:
        errors.append('No sustained 5-second TRACK interval ending in loss')
    delay = None if landing is None else landing - loss
    if delay is None or not 1.45 <= delay <= 2.0:
        errors.append('No observed landing request near 1.5 seconds after loss')
    if bridge is not None and (bridge.get('first_fault') or {}).get('after', {}).get(
            'reason') == 'APPROVED_INPUT_LOSS':
        info, fault = bridge.get('metadata', {}), bridge['first_fault']
        wall, anchor, start_ns = (info.get('wall_time_ns'),
                                  info.get('mono_anchor_ns'), fault.get('start_ns'))
        loss_decision = None if loss_record is None else loss_record.get('decision_stamp_ns')
        if (not all(type(v) is int for v in (wall, anchor, start_ns, loss_decision)) or
                not 1.45 <= (wall + start_ns - anchor - loss_decision) / 1e9 <= 2.0):
            errors.append('Bridge input-loss watchdog timing not paired to loss')
    # Monitor tolerance concerns subscriber scheduling, not production deadlines.
    # Associate absence with loss using the existing 250 ms observer-gap
    # budget. This is subscriber tolerance, not a production watchdog change.
    association_tolerance_s = 0.25
    returned = None
    loss_decision = None if loss_record is None else loss_record.get('decision_stamp_ns')
    for index, record in enumerate(hands):
        if (loss is None or record['flags'] != [False, 0] or
                (index and hands[index - 1]['flags'] == [False, 0]) or
                abs(record['elapsed_s'] - loss) > association_tolerance_s):
            continue
        # A hand may first become ineligible as it leaves the frame, before the
        # detector reports zero hands. Require that one-hand transition to have
        # preceded the loss decision; multiple hands are not removal evidence.
        trigger = record
        trigger_index = index
        if (type(loss_decision) is int and type(record.get('source_stamp_ns')) is int
                and record['source_stamp_ns'] > loss_decision and index):
            trigger = hands[index - 1]
            trigger_index = index - 1
        previous = None if trigger_index == 0 else hands[trigger_index - 1]
        source = trigger.get('source_stamp_ns')
        no_hand_source = record.get('source_stamp_ns')
        if (previous is None or previous['flags'] != [True, 1] or
                trigger['elapsed_s'] - previous['elapsed_s'] > association_tolerance_s or
                trigger['flags'] not in ([False, 0], [False, 1]) or
                type(source) is not int or type(loss_decision) is not int or
                type(no_hand_source) is not int or
                not 0 <= loss_decision - source < 250_000_000 or
                not 0 <= no_hand_source - source < 250_000_000 or
                record['elapsed_s'] - trigger['elapsed_s'] > association_tolerance_s):
            continue
        following = next((i for i in range(index + 1, len(hands))
                          if hands[i]['flags'] != [False, 0]), None)
        if following is None or hands[following]['flags'] != [True, 1]:
            continue
        first_return = hands[following]['elapsed_s']
        if (first_return - record['elapsed_s'] < 3 or landing is None or
                not record['elapsed_s'] <= landing <= first_return):
            continue
        # Allow brief hand-shape flicker during reacquisition. The counted
        # return still needs five uninterrupted seconds of eligible samples.
        stable_start = None
        for hand in hands[following:]:
            if hand['flags'] != [True, 1]:
                stable_start = None
            elif stable_start is None:
                stable_start = hand['elapsed_s']
            if (stable_start is not None and
                    stable_start - first_return <= 1.0 and
                    hand['elapsed_s'] - stable_start >= 5):
                returned = stable_start
                break
        if returned is not None:
            break
    if returned is None:
        errors.append('No 3-second hand absence followed by eligible hand return')
    else:
        end = returned + 5
        tail = [r for r in approved if landing is not None and landing <= r['elapsed_s']]
        returned_hands = [r for r in hands if returned <= r['elapsed_s'] <= end]
        if (not tail or approved[-1]['elapsed_s'] < end or hands[-1]['elapsed_s'] < end
                or any(r['flags'] != [True, False, True] for r in tail)
                or any(r['flags'] != [True, 1] for r in returned_hands)):
            errors.append('Landing latch/no restart not evidenced for 5 seconds of hand return')
    return {'schema': 'fake_target_loss_audit', 'audit_policy': 'removal_transition_v2',
            'passed': not errors, 'errors': errors,
            'run_id': label, 'revision': revision,
            'maximum_completed_track_s': maximum_track, 'loss_elapsed_s': loss,
            'landing_elapsed_s': landing, 'observed_loss_to_land_s': delay,
            'hand_return_elapsed_s': returned,
            'limitations': ['Passive depth-1 monitoring can miss messages/transitions.',
                            'Observed timing is not exact fault onset or physical stopping.',
                            'A landing request and SDK reply never confirm physical landing.',
                            'Intentional removal and test setup require the operator record.']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('monitor', 'bridge', 'operator', 'sender', 'clock'):
        parser.add_argument('--' + name, type=Path, required=name == 'monitor')
    parser.add_argument('--output', type=Path, help='New JSON result; refuses overwrite')
    args = parser.parse_args(argv)
    try:
        reports = {name: None if getattr(args, name) is None else
                   json.loads(getattr(args, name).read_text())
                   for name in ('monitor', 'bridge', 'operator', 'sender', 'clock')}
        result = analyze(**reports)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        result = {'passed': False, 'errors': ['Unreadable/malformed evidence: ' + str(error)]}
    result['analyzer_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if args.output:
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
            stream.write('\n')
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
