"""
One supervised lateral candidate pulse through the ROS mission manager.

This process never opens a Tello socket. The Windows gateway owns the aircraft
socket, and the independent mission manager must approve every candidate.
"""

import argparse
import json
from pathlib import Path
import select
import sys
import time

from tello_bridge.forward_approved import valid_session_id


def trial_frame_id(session_id):
    if not valid_session_id(session_id):
        raise ValueError('Invalid flight session ID')
    return 'supervised_lateral_trial_' + session_id


class PulseEnvelope:
    """Keep the single nonzero interval bounded by local monotonic time."""

    def __init__(self, lateral, duration_ms):
        if (type(lateral) is not int or lateral == 0 or abs(lateral) > 5 or
                type(duration_ms) is not int or not 50 <= duration_ms <= 300):
            raise ValueError('Trial requires lateral +/-1..5 and 50..300 ms')
        self.lateral = lateral
        self.duration_ns = duration_ms * 1_000_000
        self.phase = 'idle'
        self.started_ns = None
        self.ended_ns = None

    def acquire(self):
        if self.phase != 'idle':
            raise RuntimeError('Trial already started')
        self.phase = 'neutral'

    def pulse(self, mono_ns):
        if self.phase != 'neutral':
            raise RuntimeError('Trial is not neutral')
        self.phase = 'pulse'
        self.started_ns = mono_ns

    def value(self, mono_ns):
        if self.phase == 'pulse' and mono_ns - self.started_ns >= self.duration_ns:
            self.phase = 'settle'
            self.ended_ns = mono_ns
        return self.lateral if self.phase == 'pulse' else 0

    def neutral(self):
        self.phase = 'settle'


def wait_for(node, predicate, seconds, fault=None):
    import rclpy

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.02)
        if fault and fault():
            raise RuntimeError(fault())
        if predicate():
            return
    raise TimeoutError('Trial stage timed out')


def wait_enter(node, prompt, seconds, fault=None):
    import rclpy

    print(prompt, flush=True)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.02)
        if fault and fault():
            raise RuntimeError(fault())
        if select.select([sys.stdin], [], [], 0)[0]:
            if not sys.stdin.readline():
                raise RuntimeError('Operator input closed')
            return
    raise TimeoutError('Operator did not confirm before trial deadline')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--flight-session-id', required=True)
    parser.add_argument('--lateral', type=int, required=True,
                        help='Signed SDK scalar; physical sign is not yet verified')
    parser.add_argument('--duration-ms', type=int, required=True)
    parser.add_argument('--report', required=True, help='New JSON report path')
    parser.add_argument('--dry-run', action='store_true',
                        help='Require a FAKE gateway release; no aircraft is involved')
    args = parser.parse_args(argv)
    try:
        frame_id = trial_frame_id(args.flight_session_id)
        envelope = PulseEnvelope(args.lateral, args.duration_ms)
    except ValueError as error:
        parser.error(str(error))

    from drone_interfaces.msg import ApprovedCommand, CandidateCommand
    import rclpy
    from rclpy.node import Node
    from rclpy.signals import SignalHandlerOptions
    from std_msgs.msg import Bool, Empty
    from std_srvs.srv import SetBool

    rclpy.init(args=[], signal_handler_options=SignalHandlerOptions.NO)
    node = Node('lateral_trial', namespace=args.namespace)
    if node.get_parameter('use_sim_time').value:
        raise ValueError('Flight trial requires use_sim_time=false')
    publisher = node.create_publisher(CandidateCommand, 'candidate_command', 1)
    lease_publisher = node.create_publisher(Empty, 'flight_operator_lease', 1)
    autonomy = node.create_client(SetBool, 'mission_manager/set_autonomy')
    release = {'received': False, 'fake': None}

    def on_release(msg):
        release['received'] = True
        release['fake'] = msg.data

    release_subscription = node.create_subscription(
        Bool, 'flight_pilot_release', on_release, 1)
    approved = {'last': None, 'receipt_ns': None, 'nonzero_count': 0, 'fault': None}
    report = {'flight_session_id': args.flight_session_id,
              'source_frame_id': frame_id, 'requested_lateral': args.lateral,
              'requested_duration_ms': args.duration_ms, 'physical_outcome': 'UNKNOWN',
              'dry_run': args.dry_run, 'approved_nonzero_count': 0, 'passed': False}
    enabled = False

    def on_approved(msg):
        approved['last'] = msg
        approved['receipt_ns'] = time.monotonic_ns()
        if msg.request_land:
            approved['fault'] = 'Mission manager requested land'
        if not msg.tracking_allowed and (msg.lateral != 0 or msg.vertical != 0):
            approved['fault'] = 'Nontracking approval contained movement'
        if msg.tracking_allowed:
            if msg.source_header.frame_id != frame_id:
                approved['fault'] = 'Approved motion came from unexpected source'
            elif msg.vertical != 0 or msg.lateral not in (0, args.lateral):
                approved['fault'] = 'Approved motion exceeded trial axis or sign'
            if msg.lateral != 0:
                if (envelope.started_ns is None or
                    (envelope.ended_ns is not None and
                     approved['receipt_ns'] - envelope.ended_ns > 250_000_000)):
                    approved['fault'] = 'Approved motion outside the pulse window'
                approved['nonzero_count'] += 1

    node.create_subscription(ApprovedCommand, 'approved_command', on_approved, 1)

    def publish_candidate():
        if envelope.phase == 'idle':
            return
        msg = CandidateCommand()
        msg.header.stamp = node.get_clock().now().to_msg()
        msg.header.frame_id = frame_id
        msg.target_valid = True
        msg.lateral = float(envelope.value(time.monotonic_ns()))
        msg.vertical = 0.0
        publisher.publish(msg)

    node.create_timer(0.02, publish_candidate)
    node.create_timer(0.08, lambda: lease_publisher.publish(Empty()))

    def fault():
        return approved['fault']

    def call_autonomy(value):
        if not autonomy.wait_for_service(timeout_sec=5.0):
            raise RuntimeError('mission_manager/set_autonomy is unavailable')
        future = autonomy.call_async(SetBool.Request(data=value))
        wait_for(node, future.done, 2.0)
        response = future.result()
        if response is None or not response.success:
            raise RuntimeError('Mission manager rejected autonomy change: ' +
                               (response.message if response else 'no response'))

    def recent_approved(predicate):
        msg = approved['last']
        return (msg is not None and approved['receipt_ns'] is not None and
                time.monotonic_ns() - approved['receipt_ns'] < 250_000_000 and
                predicate(msg))

    try:
        wait_for(node, lambda: publisher.get_subscription_count() == 1 and
                 lease_publisher.get_subscription_count() == 1 and
                 release_subscription.get_publisher_count() == 1, 5.0)
        call_autonomy(False)
        wait_for(node, lambda: recent_approved(
            lambda msg: not msg.autonomy_enabled and not msg.tracking_allowed and
            msg.lateral == 0 and msg.vertical == 0), 2.0)
        prefix = ('In the FAKE gateway, click Confirm FAKE hover. ' if args.dry_run
                  else 'Take off with the app, establish a steady hover, then '
                       'click Pilot confirms hover in the Windows gateway. ')
        wait_enter(node, prefix + 'Before arming, return here and press Enter.',
                   30.0, fault)
        print('Return focus to the Windows gateway. Click Arm flight session, '
              'then Release ONE ROS lateral pulse. Keep the gateway focused.',
              flush=True)
        wait_for(node, lambda: release['received'], 30.0, fault)
        if release['fake'] != args.dry_run:
            raise RuntimeError('Gateway FAKE/real mode did not match trial mode')
        call_autonomy(True)
        enabled = True
        envelope.acquire()
        wait_for(node, lambda: recent_approved(
            lambda msg: msg.autonomy_enabled and msg.tracking_allowed and
            msg.source_header.frame_id == frame_id and
            msg.lateral == 0 and msg.vertical == 0), 2.0, fault)
        envelope.pulse(time.monotonic_ns())
        wait_for(node, lambda: envelope.phase == 'settle', 1.0, fault)
        wait_for(node, lambda: recent_approved(
            lambda msg: msg.autonomy_enabled and msg.tracking_allowed and
            msg.lateral == 0 and msg.vertical == 0), 0.5, fault)
        report['approved_nonzero_count'] = approved['nonzero_count']
        if not approved['nonzero_count']:
            raise RuntimeError('No nonzero approved command was observed')
        report['passed'] = True
    except (KeyboardInterrupt, RuntimeError, TimeoutError) as error:
        report['error'] = str(error)
    finally:
        envelope.neutral()
        if enabled:
            try:
                call_autonomy(False)
                wait_for(node, lambda: recent_approved(
                    lambda msg: not msg.autonomy_enabled and not msg.tracking_allowed),
                    1.0)
            except (RuntimeError, TimeoutError) as error:
                report['cleanup_error'] = str(error)
                report['passed'] = False
        report['approved_nonzero_count'] = approved['nonzero_count']
        report['final_phase'] = envelope.phase
        report['pulse_started_ns'] = envelope.started_ns
        report['pulse_ended_ns'] = envelope.ended_ns
        report['supervisor_fault'] = approved['fault']
        try:
            with Path(args.report).open('x', encoding='utf-8') as output:
                json.dump(report, output, indent=2)
        finally:
            node.destroy_node()
            rclpy.shutdown()
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report['passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
