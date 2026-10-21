"""WSL ROS ApprovedCommand to Windows data-only UDP forwarding adapter."""

import argparse
import json
import socket


def stamp_ns(stamp):
    """Retain an original ROS stamp in integer Unix nanoseconds."""
    if type(stamp.sec) is not int or type(stamp.nanosec) is not int:
        raise ValueError('Invalid stamp type')
    if stamp.sec < 0 or not 0 <= stamp.nanosec < 1_000_000_000:
        raise ValueError('Invalid stamp range')
    return stamp.sec * 1_000_000_000 + stamp.nanosec


def encode_approved(msg):
    """Preserve original stamps and values; gateway independently validates."""
    return json.dumps({
        'version': 1,
        'decision_ns': stamp_ns(msg.header.stamp),
        'source_ns': stamp_ns(msg.source_header.stamp),
        'enabled': msg.autonomy_enabled,
        'tracking': msg.tracking_allowed,
        'land': msg.request_land,
        'lateral': msg.lateral,
        'vertical': msg.vertical,
    }, allow_nan=False, separators=(',', ':')).encode('utf-8')


def main(argv=None):
    """Run the data-only ROS forwarding subscriber."""
    parser = argparse.ArgumentParser(
        description='Forward ROS decisions to FAKE Windows gateway')
    parser.add_argument('--windows-ip', required=True,
                        help='Explicit reachable Windows IPv4')
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--namespace', required=True)
    args = parser.parse_args(argv)
    socket.inet_aton(args.windows_ip)
    if not 1 <= args.port <= 65535:
        parser.error('port must be in [1,65535]')
    from drone_interfaces.msg import ApprovedCommand
    import rclpy
    from rclpy.node import Node

    rclpy.init()
    node = Node('approved_forwarder', namespace=args.namespace)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)

    def forward(msg):
        try:
            sock.sendto(encode_approved(msg), (args.windows_ip, args.port))
        except (OSError, ValueError, TypeError) as error:
            node.get_logger().warning('Decision not forwarded: ' + str(error))

    node.create_subscription(ApprovedCommand, 'approved_command', forward, 1)
    try:
        rclpy.spin(node)
    finally:
        sock.close()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
