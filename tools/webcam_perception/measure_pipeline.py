#!/usr/bin/env python3
"""
Passive observer for the Tello webcam pipeline; never publishes or calls services.

WSL, sourced ROS Jazzy and workspace:
  python3 tools/webcam_perception/measure_pipeline.py --self-test
  python3 tools/webcam_perception/measure_pipeline.py --duration 60 \
      --output /tmp/webcam_timing_baseline.json

Observed rates include discovery/startup gaps and missed monitor deliveries.
Advancing source stamps are counted separately from repeated approved heartbeats.
Source age is measured at the monitor callback, not at hardware exposure or at
the production node. Transition intervals include monitor delivery/scheduling;
they are NOT exact fault-onset, physical stopping, or flight latency measurements.
Monitor subscriptions can affect load. KEEP_LAST depth 1 favours current data;
it does not guarantee an exhaustive event trace. ROS system time must agree with
Windows capture time; the ROS/monotonic comparison cannot check Windows offset.
Only the first 100,000 values per distribution and first 1,000 state transitions
are retained. Any truncation is explicitly reported. Use separate baseline and
controlled-fault runs; no pass/fail timing thresholds are inferred here.
"""

import argparse
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import unittest

SAMPLE_LIMIT = 100000
EVENT_LIMIT = 1000
TOPICS = ('hand_target', 'candidate_command', 'approved_command')


def stamp_ns(stamp):
    """Return a nonzero well-formed stamp, or None for missing/malformed input."""
    sec, nsec = getattr(stamp, 'sec', None), getattr(stamp, 'nanosec', None)
    if type(sec) is not int or type(nsec) is not int:
        return None
    if not 0 <= sec <= 2147483647 or not 0 <= nsec < 1000000000:
        return None
    value = sec * 1000000000 + nsec
    return value if value > 0 else None


def message_fields(topic, message):
    if topic == 'hand_target':
        return (stamp_ns(message.header.stamp), None, message.detected,
                (message.detected, message.tracked_hands))
    if topic == 'candidate_command':
        return (stamp_ns(message.header.stamp), None, message.target_valid,
                (message.target_valid,))
    return (stamp_ns(message.source_header.stamp), stamp_ns(message.header.stamp),
            message.tracking_allowed,
            (message.autonomy_enabled, message.tracking_allowed, message.request_land))


def distribution(values, total):
    if not values:
        return {'sampled': 0, 'total': total, 'omitted': total}
    ordered = sorted(values)

    def percentile(fraction):
        index = fraction * (len(ordered) - 1)
        lower, upper = math.floor(index), math.ceil(index)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)

    return {
        'sampled': len(values), 'total': total, 'omitted': total - len(values),
        'min': ordered[0], 'p50': percentile(0.5), 'p95': percentile(0.95),
        'p99': percentile(0.99), 'max': ordered[-1],
    }


class TopicStats:
    def __init__(self, sample_limit=SAMPLE_LIMIT):
        self.sample_limit = sample_limit
        self.messages = self.advancing = self.duplicates = self.older = 0
        self.missing_stamps = self.flagged_usable = self.future_ages = 0
        self.flagged_usable_age_ge_250ms = 0
        self.watermark = 0
        self.last_receipt = self.last_flagged_usable_receipt = None
        self.source_ages = []
        self.source_age_total = 0
        self.usable_ages = []
        self.usable_age_total = 0
        self.decision_ages = []
        self.decision_age_total = 0
        self.gaps = []
        self.gap_total = 0
        self.maximum_gap_ms = None

    def add_sample(self, kind, value):
        count_name = kind + '_total'
        setattr(self, count_name, getattr(self, count_name) + 1)
        values = getattr(self, kind + 's')
        if len(values) < self.sample_limit:
            values.append(value)

    def observe(self, source_ns, decision_ns, ros_ns, mono_ns, usable):
        self.messages += 1
        if self.last_receipt is not None:
            gap = (mono_ns - self.last_receipt) / 1000000
            self.add_sample('gap', gap)
            self.maximum_gap_ms = max(self.maximum_gap_ms or 0.0, gap)
        self.last_receipt = mono_ns
        if usable:
            self.flagged_usable += 1
            self.last_flagged_usable_receipt = mono_ns
        if decision_ns is not None:
            self.add_sample('decision_age', (ros_ns - decision_ns) / 1000000)
        if source_ns is None:
            self.missing_stamps += 1
            return
        age_ms = (ros_ns - source_ns) / 1000000
        if age_ms < 0:
            self.future_ages += 1
        if usable and age_ms >= 250:
            self.flagged_usable_age_ge_250ms += 1
        if source_ns == self.watermark:
            self.duplicates += 1
        elif source_ns < self.watermark:
            self.older += 1
        else:
            self.watermark = source_ns
            self.advancing += 1
            self.add_sample('source_age', age_ms)
            if usable:
                self.add_sample('usable_age', age_ms)

    def report(self, elapsed_ns, end_ns):
        elapsed = elapsed_ns / 1000000000
        tail_gap = None if self.last_receipt is None else (
            end_ns - self.last_receipt) / 1000000
        return {
            'observed_messages': self.messages,
            'observed_messages_per_second_over_full_window': self.messages / elapsed,
            'advancing_source_stamps': self.advancing,
            'advancing_source_stamps_per_second_over_full_window': self.advancing / elapsed,
            'duplicate_source_stamps': self.duplicates,
            'older_source_stamps': self.older,
            'missing_or_malformed_source_stamps': self.missing_stamps,
            'flagged_usable_messages': self.flagged_usable,
            'messages_with_negative_source_age': self.future_ages,
            'flagged_usable_messages_with_source_age_ge_250ms_at_monitor':
                self.flagged_usable_age_ge_250ms,
            'advancing_source_age_ms': distribution(self.source_ages, self.source_age_total),
            'flagged_usable_advancing_source_age_ms':
                distribution(self.usable_ages, self.usable_age_total),
            'decision_age_ms': distribution(self.decision_ages, self.decision_age_total),
            'receipt_gap_ms': distribution(self.gaps, self.gap_total),
            'maximum_receipt_gap_ms_all_messages': self.maximum_gap_ms,
            'silence_since_last_receipt_at_finish_ms': tail_gap,
        }


class Measurements:
    def __init__(self, ros_ns, mono_ns, sample_limit=SAMPLE_LIMIT, event_limit=EVENT_LIMIT):
        self.start_ros = ros_ns
        self.start_mono = mono_ns
        self.clock_mismatch_max_ms = 0.0
        self.sample_limit, self.event_limit = sample_limit, event_limit
        self.topics = {name: TopicStats(sample_limit) for name in TOPICS}
        self.states = {}
        self.events = []
        self.events_omitted = 0
        self.last_tracking_loss = None
        self.records = []
        self.records_omitted = 0
        self.nonfinite_values = 0
        self.nonneutral_without_tracking = 0

    def record_message(self, topic, message, mono_ns):
        source, decision, usable, state = message_fields(topic, message)
        values = {}
        for name in ('error_x', 'error_y', 'lateral', 'vertical'):
            if hasattr(message, name):
                value = float(getattr(message, name))
                if not math.isfinite(value):
                    self.nonfinite_values += 1
                    value = None
                values[name] = value
        if topic in ('candidate_command', 'approved_command') and not usable:
            if any(value != 0.0 for value in values.values()):
                self.nonneutral_without_tracking += 1
        record = {'topic': topic, 'elapsed_s': (mono_ns - self.start_mono) / 1e9,
                  'source_stamp_ns': source, 'decision_stamp_ns': decision,
                  'flags': state, 'values': values}
        if len(self.records) < self.sample_limit:
            self.records.append(record)
        else:
            self.records_omitted += 1

    def observe(self, topic, source_ns, decision_ns, ros_ns, mono_ns, usable, state):
        mismatch_ms = abs((ros_ns - self.start_ros) -
                          (mono_ns - self.start_mono)) / 1000000
        self.clock_mismatch_max_ms = max(self.clock_mismatch_max_ms, mismatch_ms)
        self.topics[topic].observe(source_ns, decision_ns, ros_ns, mono_ns, usable)
        previous = self.states.get(topic)
        self.states[topic] = state
        if topic == 'approved_command':
            if not state[0]:
                self.last_tracking_loss = None
            elif previous is not None and previous[1] and not state[1]:
                self.last_tracking_loss = mono_ns
            elif state[1]:
                self.last_tracking_loss = None
        if previous is None or previous == state:
            return None
        event = {
            'topic': topic, 'elapsed_s': (mono_ns - self.start_mono) / 1000000000,
            'previous': previous, 'current': state,
            'source_stamp_ns': source_ns,
            'source_age_ms_at_transition': None if source_ns is None else (
                ros_ns - source_ns) / 1000000,
            'since_observed_tracking_loss_ms': None if self.last_tracking_loss is None
            else (mono_ns - self.last_tracking_loss) / 1000000,
        }
        for name in ('hand_target', 'candidate_command'):
            anchor = self.topics[name].last_flagged_usable_receipt
            event['since_last_flagged_usable_' + name + '_receipt_ms'] = (
                None if anchor is None else (mono_ns - anchor) / 1000000)
        if len(self.events) < self.event_limit:
            self.events.append(event)
        else:
            self.events_omitted += 1
        return event

    def report(self, end_ns, namespace, requested_seconds, interrupted, metadata=None):
        elapsed_ns = max(1, end_ns - self.start_mono)
        return {
            'schema_version': 2, 'namespace': namespace,
            'metadata': metadata or {},
            'requested_duration_s': requested_seconds,
            'observed_duration_s': elapsed_ns / 1000000000,
            'interrupted': interrupted,
            'qos': {'history': 'keep_last', 'depth': 1,
                    'reliability': 'reliable', 'durability': 'volatile'},
            'limits': {'distribution_sample_limit': self.sample_limit,
                       'state_transition_limit': self.event_limit,
                       'message_record_limit': self.sample_limit},
            'percentile_method': 'linear interpolation at fraction * (n - 1)',
            'sample_policy': 'first samples; truncated distributions do not cover the whole run',
            'publisher_rate_hz': None, 'camera_throughput_fps': None,
            'clock_ros_vs_monotonic_elapsed_mismatch_max_ms': self.clock_mismatch_max_ms,
            'source_age_clock_warning': self.clock_mismatch_max_ms > 10.0,
            'missing_topics': [name for name in TOPICS if not self.topics[name].messages],
            'state_order': {'hand_target': ['detected', 'tracked_hands'],
                            'candidate_command': ['target_valid'],
                            'approved_command': ['autonomy_enabled', 'tracking_allowed',
                                                 'request_land']},
            'topics': {name: stats.report(elapsed_ns, end_ns)
                       for name, stats in self.topics.items()},
            'state_transitions': self.events,
            'state_transitions_omitted': self.events_omitted,
            'message_records': self.records,
            'message_records_omitted': self.records_omitted,
            'nonfinite_command_or_error_values': self.nonfinite_values,
            'nonneutral_messages_without_tracking': self.nonneutral_without_tracking,
            'limitations': [
                'Source age is software capture-receipt to monitor callback, not exposure.',
                'Observed delivery rates are not guaranteed camera FPS or publisher rates.',
                'State timing is observed at this subscriber, not exact fault onset.',
                'QoS depth 1 and monitor scheduling can miss messages or transitions.',
                'Reliable monitoring may perturb the system being measured.',
                'ROS versus monotonic comparison cannot establish Windows clock agreement.',
                'No aircraft is commanded and no physical stopping time is measured.',
            ],
        }


class MeasurementTests(unittest.TestCase):
    def test_interface_header_selection(self):
        from types import SimpleNamespace as NS
        original = NS(stamp=NS(sec=10, nanosec=5))
        decision = NS(stamp=NS(sec=11, nanosec=7))
        hand = NS(header=original, detected=True, tracked_hands=1)
        candidate = NS(header=original, target_valid=True)
        approved = NS(header=decision, source_header=original, autonomy_enabled=True,
                      tracking_allowed=True, request_land=False)
        self.assertEqual(message_fields('hand_target', hand),
                         (10000000005, None, True, (True, 1)))
        self.assertEqual(message_fields('candidate_command', candidate),
                         (10000000005, None, True, (True,)))
        self.assertEqual(message_fields('approved_command', approved),
                         (10000000005, 11000000007, True, (True, True, False)))

    def test_stamp_validation(self):
        from types import SimpleNamespace
        for sec, nsec in ((0, 0), (-1, 0), (1, 1000000000)):
            self.assertIsNone(stamp_ns(SimpleNamespace(sec=sec, nanosec=nsec)))
        self.assertEqual(stamp_ns(SimpleNamespace(sec=1, nanosec=7)), 1000000007)

    def test_heartbeat_does_not_inflate_source_rate(self):
        stats = TopicStats()
        for tick in range(10):
            stats.observe(100, 200 + tick, 300 + tick, 400 + tick, True)
        self.assertEqual((stats.messages, stats.advancing, stats.duplicates), (10, 1, 9))
        self.assertEqual(len(stats.source_ages), 1)
        self.assertEqual(len(stats.decision_ages), 10)

    def test_missing_and_older_stamps(self):
        stats = TopicStats()
        for source in (None, 100, 90, 100, 110):
            stats.observe(source, None, 200, 300, False)
        self.assertEqual((stats.missing_stamps, stats.older, stats.duplicates,
                          stats.advancing), (1, 1, 1, 2))

    def test_signed_age_and_inclusive_threshold(self):
        stats = TopicStats()
        stats.observe(1000000000, None, 1250000000, 1, True)
        stats.observe(2000000000, None, 1999000000, 2, True)
        self.assertEqual(stats.flagged_usable_age_ge_250ms, 1)
        self.assertEqual(stats.future_ages, 1)
        self.assertEqual(stats.source_ages, [250.0, -1.0])

    def test_truncation_and_percentiles(self):
        stats = TopicStats(sample_limit=2)
        for source in (100, 200, 300):
            stats.observe(source, None, 1000, source, True)
        result = distribution(stats.source_ages, stats.source_age_total)
        self.assertEqual((result['sampled'], result['omitted']), (2, 1))
        self.assertAlmostEqual(distribution([0, 10], 2)['p95'], 9.5)
        self.assertEqual(distribution([], 0)['sampled'], 0)

    def test_observed_loss_to_land_and_takeover(self):
        data = Measurements(0, 0)
        data.observe('approved_command', 1, 1, 1, 1, True, (True, True, False))
        loss = data.observe('approved_command', 1, 2, 2, 2, False, (True, False, False))
        self.assertEqual(loss['since_observed_tracking_loss_ms'], 0.0)
        land = data.observe('approved_command', 1, 3, 3, 1500000002,
                            False, (True, False, True))
        self.assertEqual(land['since_observed_tracking_loss_ms'], 1500.0)
        takeover = data.observe('approved_command', None, 4, 4, 1600000002,
                                False, (False, False, False))
        self.assertIsNone(takeover['since_observed_tracking_loss_ms'])

    def test_no_fabricated_fault_anchor(self):
        data = Measurements(0, 0)
        data.observe('approved_command', None, 1, 1, 1, False, (True, False, False))
        event = data.observe('approved_command', None, 2, 2, 2,
                             False, (True, False, True))
        self.assertIsNone(event['since_observed_tracking_loss_ms'])
        self.assertIsNone(event['since_last_flagged_usable_hand_target_receipt_ms'])

    def test_clock_warning_and_missing_topics(self):
        data = Measurements(1000000000, 0)
        data.observe('hand_target', 1, None, 1020000000, 1, True, (True, 1))
        result = data.report(1000000000, '/webcam_test', 1, False)
        self.assertTrue(result['source_age_clock_warning'])
        self.assertEqual(result['missing_topics'], ['candidate_command', 'approved_command'])

    def test_terminal_silence_uses_monotonic_time(self):
        stats = TopicStats()
        stats.observe(1, None, 2, 1000000, True)
        self.assertEqual(stats.report(1000000000, 501000000)[
            'silence_since_last_receipt_at_finish_ms'], 500.0)


def run_metadata(args):
    root = Path(__file__).resolve().parents[2]

    def git(*arguments):
        try:
            return subprocess.check_output(
                ['git', '-C', str(root), *arguments], stderr=subprocess.DEVNULL,
                text=True, timeout=2).strip()
        except (OSError, subprocess.SubprocessError):
            return None
    dirty = git('status', '--porcelain')
    return {
        'commit': git('rev-parse', 'HEAD'),
        'working_tree_dirty': None if dirty is None else bool(dirty),
        'run_kind': args.run_kind, 'source_classification': args.source,
        'label': args.label, 'configuration': args.configuration,
        'environment': {'platform': platform.platform(), 'python': sys.version,
                        'ros_distro': os.environ.get('ROS_DISTRO'),
                        'ros_domain_id': os.environ.get('ROS_DOMAIN_ID'),
                        'rmw_implementation': os.environ.get('RMW_IMPLEMENTATION')},
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--namespace', default='/webcam_test')
    parser.add_argument('--duration', type=float, default=60.0)
    parser.add_argument('--output', type=Path, help='New JSON file; existing files are refused')
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--source', choices=('synthetic', 'live_webcam', 'recorded',
                                             'tello_video', 'unspecified'), default='unspecified')
    parser.add_argument('--run-kind', choices=('baseline', 'fault', 'smoke'), default='baseline')
    parser.add_argument('--label', default='')
    parser.add_argument('--configuration', type=json.loads, default={})
    parser.add_argument('--sample-limit', type=int, default=SAMPLE_LIMIT)
    parser.add_argument('--event-limit', type=int, default=EVENT_LIMIT)
    args = parser.parse_args(argv)
    if not math.isfinite(args.duration) or not 1 <= args.duration <= 600:
        parser.error('--duration must be between 1 and 600 seconds')
    args.namespace = '/' + args.namespace.strip('/')
    if args.namespace != '/' and not re.fullmatch(r'(?:/[A-Za-z_][A-Za-z0-9_]*)+', args.namespace):
        parser.error('--namespace must be a valid absolute ROS namespace')
    if not 1 <= args.sample_limit <= SAMPLE_LIMIT or not 1 <= args.event_limit <= EVENT_LIMIT:
        parser.error('sample/event limits must be positive and within the documented maximum')
    if not isinstance(args.configuration, dict):
        parser.error('--configuration must be a JSON object')
    try:
        json.dumps(args.configuration, allow_nan=False)
    except ValueError:
        parser.error('--configuration cannot contain nonfinite numbers')
    if args.output is not None and args.output.exists():
        parser.error('--output already exists; choose a new filename')
    return args


def main(argv=None):
    args = parse_args(argv)
    if args.self_test:
        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(MeasurementTests))
        return 0 if result.wasSuccessful() else 1
    namespace = args.namespace
    metadata = run_metadata(args)

    from drone_interfaces.msg import ApprovedCommand, CandidateCommand, HandTarget
    import rclpy
    from rclpy.executors import ExternalShutdownException
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy

    rclpy.init(args=[])
    node = Node('pipeline_timing_monitor', namespace=namespace)
    if node.get_parameter('use_sim_time').value:
        node.destroy_node()
        rclpy.shutdown()
        raise RuntimeError('This monitor requires use_sim_time=false')
    data = Measurements(node.get_clock().now().nanoseconds, time.monotonic_ns(),
                        args.sample_limit, args.event_limit)
    qos = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=1,
                     reliability=ReliabilityPolicy.RELIABLE,
                     durability=DurabilityPolicy.VOLATILE)
    announced_ready = False

    def callback(topic, message):
        nonlocal announced_ready
        mono_ns = time.monotonic_ns()
        ros_ns = node.get_clock().now().nanoseconds
        source, decision, usable, state = message_fields(topic, message)
        data.record_message(topic, message, mono_ns)
        event = data.observe(topic, source, decision, ros_ns, mono_ns, usable, state)
        if not announced_ready and all(stats.messages for stats in data.topics.values()):
            announced_ready = True
            print('All pipeline topics observed.', flush=True)
        if event and not data.events_omitted:
            print('TRANSITION ' + json.dumps(event), flush=True)

    subscriptions = []
    for topic, message_type in zip(TOPICS, (HandTarget, CandidateCommand, ApprovedCommand)):
        subscriptions.append(node.create_subscription(
            message_type, topic, lambda msg, name=topic: callback(name, msg), qos))
    print(f'Observing {namespace} for {args.duration:g}s; no commands are sent.', flush=True)
    interrupted = False
    deadline = data.start_mono + round(args.duration * 1000000000)
    try:
        while rclpy.ok():
            remaining = (deadline - time.monotonic_ns()) / 1000000000
            if remaining <= 0:
                break
            rclpy.spin_once(node, timeout_sec=min(0.05, remaining))
        if not rclpy.ok():
            interrupted = True
    except (KeyboardInterrupt, ExternalShutdownException):
        interrupted = True
    finally:
        end_ros, end_mono = node.get_clock().now().nanoseconds, time.monotonic_ns()
        mismatch = abs((end_ros - data.start_ros) - (end_mono - data.start_mono)) / 1e6
        data.clock_mismatch_max_ms = max(data.clock_mismatch_max_ms, mismatch)
        report = data.report(end_mono, namespace, args.duration, interrupted, metadata)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write('\n')
        print(f'Report written to {args.output}')
    printable = {key: value for key, value in report.items()
                 if key not in ('state_transitions', 'message_records')}
    print(json.dumps(printable, indent=2, allow_nan=False))
    return 2 if report['missing_topics'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
