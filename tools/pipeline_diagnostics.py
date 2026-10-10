#!/usr/bin/env python3
"""Read-only ROS graph/runtime diagnostics. No publishers, authority services or SDK calls."""
import argparse
import json
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace', default='/webcam_test')
    args = parser.parse_args()
    import rclpy
    from rcl_interfaces.srv import GetParameters
    rclpy.init(args=[])
    node = rclpy.create_node('pipeline_diagnostics')
    result = {'namespace': args.namespace, 'topics': {}, 'use_sim_time': {},
              'clock_prerequisite': 'Run check_clocks.py across Windows/WSL with nodes stopped; '
                                    'this diagnostic cannot establish Windows clock agreement.'}
    try:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.05)
        namespace = '/' + args.namespace.strip('/')
        for topic in ('hand_target', 'candidate_command', 'approved_command'):
            full_name = namespace.rstrip('/') + '/' + topic
            info = node.get_publishers_info_by_topic(full_name)
            result['topics'][full_name] = {
                'publisher_count': len(info), 'publishers': [{
                    'node': endpoint.node_namespace + '/' + endpoint.node_name,
                    'type': endpoint.topic_type, 'qos': str(endpoint.qos_profile)}
                    for endpoint in info]}
        # Parameter GET is read-only; never set clocks, autonomy or lifecycle state.
        for name in ('webcam_receiver', 'tracking_controller', 'mission_manager', 'tello_bridge'):
            remote = namespace.rstrip('/') + '/' + name
            client = node.create_client(GetParameters, remote + '/get_parameters')
            if client.wait_for_service(timeout_sec=0.2):
                future = client.call_async(GetParameters.Request(names=['use_sim_time']))
                rclpy.spin_until_future_complete(node, future, timeout_sec=0.5)
                value = future.result()
                result['use_sim_time'][remote] = (
                    value.values[0].bool_value if value is not None else 'UNAVAILABLE')
            else:
                result['use_sim_time'][remote] = 'UNAVAILABLE'
        packages = ('drone_interfaces', 'tracking_controller', 'mission_manager', 'tello_bridge')
        for package in packages:
            command = ['ros2', 'pkg', 'prefix' if package == 'drone_interfaces' else 'executables',
                       package]
            process = subprocess.run(command, capture_output=True, text=True, timeout=5)
            result[package] = {'exit_code': process.returncode, 'output': process.stdout.strip()}
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    print(json.dumps(result, indent=2))
    return 2 if any(topic['publisher_count'] != 1 for topic in result['topics'].values()) else 0


if __name__ == '__main__':
    raise SystemExit(main())
