#!/usr/bin/env python3
"""Read-only, bounded graph and disabled-startup checks (no aircraft commands)."""
import argparse
from collections import Counter
import json
import math
import subprocess
import time

NODES = ('webcam_receiver', 'tracking_controller', 'mission_manager', 'tello_bridge')
TOPICS = {
    'hand_target': ('webcam_receiver', 'drone_interfaces/msg/HandTarget'),
    'candidate_command': ('tracking_controller', 'drone_interfaces/msg/CandidateCommand'),
    'approved_command': ('mission_manager', 'drone_interfaces/msg/ApprovedCommand'),
}


def graph_errors(result):
    errors = []
    namespace = result['namespace']
    for name in NODES:
        remote = namespace + '/' + name
        if result['node_counts'].get(remote, 0) != 1:
            errors.append('Expected exactly one node: ' + remote)
    for topic, (name, message_type) in TOPICS.items():
        full_name = namespace + '/' + topic
        endpoints = result['topics'][full_name]['publishers']
        if len(endpoints) != 1 or endpoints[0]['node'] != namespace + '/' + name or \
                endpoints[0]['type'] != message_type:
            errors.append('Missing, duplicate or unexpected publisher: ' + full_name)
    for name in NODES:
        remote = namespace + '/' + name
        if result['use_sim_time'].get(remote) is not False:
            errors.append('use_sim_time must be verified false: ' + remote)
    return errors


def disabled_errors(approved, bridge, now):
    errors = []
    if approved is None or now - approved['receipt'] > 0.5:
        errors.append('No recent approved output')
    elif approved['flags'] != [False, False, False] or approved['values'] != [0.0, 0.0]:
        errors.append('Approved output is not disabled/neutral')
    if not isinstance(bridge, dict) or any(
        bridge.get(key) is not expected for key, expected in (
            ('fake', True), ('armed', False), ('awaiting_enabled', False),
            ('upstream_disabled', True), ('landing_latched', False))
    ):
        errors.append('Bridge status must be FAKE, unarmed, upstream disabled and unlatched')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace', default='/webcam_test')
    parser.add_argument('--discovery-seconds', type=float, default=5.0)
    parser.add_argument('--service-timeout', type=float, default=2.0)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--expect-empty', action='store_true')
    modes.add_argument('--require-disabled', action='store_true')
    parser.add_argument('--skip-packages', action='store_true')
    args = parser.parse_args()
    if not all(math.isfinite(v) and 0 < v <= 10 for v in
               (args.discovery_seconds, args.service_timeout)):
        parser.error('Diagnostic waits must be finite and in (0, 10] seconds')
    import rclpy
    from rcl_interfaces.msg import ParameterType
    from rcl_interfaces.srv import GetParameters
    from drone_interfaces.msg import ApprovedCommand
    from std_srvs.srv import Trigger
    rclpy.init(args=[])
    node = rclpy.create_node('pipeline_diagnostics')
    namespace = '/' + args.namespace.strip('/')
    result = {'namespace': namespace, 'topics': {}, 'node_counts': {}, 'use_sim_time': {},
              'clock_prerequisite': 'Run check_clocks.py across Windows/WSL with nodes stopped; '
                                    'this diagnostic cannot establish Windows clock agreement.'}
    approved = None
    unsafe_approved_seen = False
    observed_conflicts = set()

    def on_approved(msg):
        nonlocal approved, unsafe_approved_seen
        approved = {'receipt': time.monotonic(),
                    'flags': [msg.autonomy_enabled, msg.tracking_allowed, msg.request_land],
                    'values': [msg.lateral, msg.vertical]}
        if approved['flags'] != [False, False, False] or approved['values'] != [0.0, 0.0]:
            unsafe_approved_seen = True

    subscription = node.create_subscription(
        ApprovedCommand, namespace + '/approved_command', on_approved, 10)

    def snapshot():
        counts = Counter(ns.rstrip('/') + '/' + name
                         for name, ns in node.get_node_names_and_namespaces())
        result['node_counts'] = {namespace + '/' + name: counts[namespace + '/' + name]
                                 for name in NODES}
        for topic in TOPICS:
            full_name = namespace + '/' + topic
            info = node.get_publishers_info_by_topic(full_name)
            result['topics'][full_name] = {
                'publisher_count': len(info), 'publishers': [{
                    'node': ep.node_namespace.rstrip('/') + '/' + ep.node_name,
                    'type': ep.topic_type, 'qos': str(ep.qos_profile)} for ep in info]}
        if args.expect_empty:
            observed_conflicts.update(name for name, count in result['node_counts'].items() if count)
            observed_conflicts.update(name for name, value in result['topics'].items()
                                      if value['publisher_count'])

    def call(service_type, path, request):
        client = node.create_client(service_type, path)
        deadline = time.monotonic() + args.service_timeout
        try:
            if not client.wait_for_service(timeout_sec=max(0, deadline - time.monotonic())):
                return None
            future = client.call_async(request)
            rclpy.spin_until_future_complete(
                node, future, timeout_sec=max(0, deadline - time.monotonic()))
            if not future.done():
                future.cancel()
                return None
            return future.result()
        finally:
            node.destroy_client(client)

    try:
        deadline = time.monotonic() + args.discovery_seconds
        while time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.05)
            if args.expect_empty:
                snapshot()
        snapshot()
        if args.expect_empty:
            result['errors'] = ['Existing pipeline endpoint: ' + name
                                for name in sorted(observed_conflicts)]
        else:
            for name in NODES:
                remote = namespace + '/' + name
                response = call(GetParameters, remote + '/get_parameters',
                                GetParameters.Request(names=['use_sim_time']))
                result['use_sim_time'][remote] = (
                    response.values[0].bool_value if response is not None and
                    len(response.values) == 1 and
                    response.values[0].type == ParameterType.PARAMETER_BOOL else 'UNAVAILABLE')
            bridge = None
            if args.require_disabled:
                response = call(Trigger, namespace + '/tello_bridge/status', Trigger.Request())
                if response is not None and response.success:
                    try:
                        bridge = json.loads(response.message)
                    except (ValueError, TypeError):
                        pass
                # Process queued approved messages after service discovery/calls.
                deadline = time.monotonic() + 0.25
                while time.monotonic() < deadline:
                    rclpy.spin_once(node, timeout_sec=0.02)
                result['bridge_status'] = bridge
                result['approved_output'] = approved
            snapshot()  # Do not report only the early discovery snapshot.
            result['errors'] = graph_errors(result)
            if args.require_disabled:
                result['errors'].extend(disabled_errors(approved, bridge, time.monotonic()))
                if unsafe_approved_seen:
                    result['errors'].append('Enabled/nonneutral/landing output observed during startup')
        if not args.skip_packages:
            for package in ('drone_interfaces', 'tracking_controller', 'mission_manager', 'tello_bridge'):
                command = ['ros2', 'pkg', 'prefix' if package == 'drone_interfaces' else 'executables',
                           package]
                process = subprocess.run(command, capture_output=True, text=True, timeout=5)
                result[package] = {'exit_code': process.returncode, 'output': process.stdout.strip()}
                if process.returncode:
                    result['errors'].append('Package lookup failed: ' + package)
        result['ok'] = not result['errors']
    finally:
        node.destroy_subscription(subscription)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
