"""WSL ROS ApprovedCommand to Windows data-only UDP forwarding adapter."""

import argparse
import json
import socket


def valid_session_id(value):
    """Require a fresh UUID4-style hex run ID for a real flight session."""
    return (type(value) is str and len(value) == 32 and
            all(char in '0123456789abcdef' for char in value))


def stamp_ns(stamp):
    """Retain an original ROS stamp in integer Unix nanoseconds."""
    if type(stamp.sec) is not int or type(stamp.nanosec) is not int:
        raise ValueError('Invalid stamp type')
    if stamp.sec < 0 or not 0 <= stamp.nanosec < 1_000_000_000:
        raise ValueError('Invalid stamp range')
    return stamp.sec * 1_000_000_000 + stamp.nanosec


def encode_approved(msg, session_id=None):
    """Preserve original stamps and values; gateway independently validates."""
    if session_id is not None and not valid_session_id(session_id):
        raise ValueError('Invalid flight session ID')
    fields = {
        'version': 2 if session_id is not None else 1,
        'decision_ns': stamp_ns(msg.header.stamp),
        'source_ns': stamp_ns(msg.source_header.stamp),
        'enabled': msg.autonomy_enabled,
        'tracking': msg.tracking_allowed,
        'land': msg.request_land,
        'lateral': msg.lateral,
        'vertical': msg.vertical,
    }
    if session_id is not None:
        fields['session_id'] = session_id
        frame_id = msg.source_header.frame_id
        if type(frame_id) is not str or len(frame_id) > 64:
            raise ValueError('Invalid source frame ID')
        fields['source_frame_id'] = frame_id
    return json.dumps(fields, allow_nan=False, separators=(',', ':')).encode('utf-8')


def encode_lease(session_id, sequence):
    """Renew only the locally armed Windows authority for this run."""
    if not valid_session_id(session_id) or type(sequence) is not int or sequence <= 0:
        raise ValueError('Invalid flight operator lease')
    return json.dumps({'version': 2, 'kind': 'operator_lease',
                       'session_id': session_id, 'sequence': sequence},
                      separators=(',', ':')).encode('utf-8')


def decode_trial_release(data, session_id):
    if len(data) > 512:
        raise ValueError('Oversize trial release')
    item = json.loads(data.decode('utf-8'))
    if (type(item) is not dict or frozenset(item) !=
            frozenset({'version', 'kind', 'session_id', 'fake'}) or
            type(item['version']) is not int or item['version'] != 2 or
            item['kind'] != 'trial_release' or item['session_id'] != session_id or
            type(item['fake']) is not bool):
        raise ValueError('Invalid trial release')
    return item['fake']


def main(argv=None):
    """Run the data-only ROS forwarding subscriber."""
    parser = argparse.ArgumentParser(
        description='Forward ROS decisions to a Windows gateway')
    parser.add_argument('--windows-ip', required=True,
                        help='Explicit reachable Windows IPv4')
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--flight-session-id',
                        help='Fresh 32-character hex ID shared with the flight gateway')
    parser.add_argument('--flight-source-frame-id',
                        help='Only forward flight motion from this source frame ID')
    args, ros_args = parser.parse_known_args(argv)
    if ros_args and ros_args[0] != '--ros-args':
        parser.error('Unknown forwarding argument: ' + ros_args[0])
    socket.inet_aton(args.windows_ip)
    if not 1 <= args.port <= 65535:
        parser.error('port must be in [1,65535]')
    if args.flight_session_id is not None and not valid_session_id(args.flight_session_id):
        parser.error('--flight-session-id must be 32 lowercase hex characters')
    if bool(args.flight_session_id) != bool(args.flight_source_frame_id):
        parser.error('Flight session and source frame ID must be supplied together')
    if (args.flight_source_frame_id and args.flight_source_frame_id !=
            'supervised_lateral_trial_' + args.flight_session_id):
        parser.error('Flight source frame ID must match the trial session')
    from drone_interfaces.msg import ApprovedCommand
    import rclpy
    from rclpy.node import Node

    rclpy.init(args=ros_args)
    node = Node('approved_forwarder', namespace=args.namespace)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    lease_sequence = 0

    def send_lease():
        nonlocal lease_sequence
        lease_sequence += 1
        try:
            sock.sendto(encode_lease(args.flight_session_id, lease_sequence),
                        (args.windows_ip, args.port))
        except OSError as error:
            node.get_logger().warning('Operator lease not forwarded: ' + str(error))

    if args.flight_session_id is not None:
        from std_msgs.msg import Bool, Empty
        node.create_subscription(Empty, 'flight_operator_lease',
                                 lambda _msg: send_lease(), 1)
        release_publisher = node.create_publisher(Bool, 'flight_pilot_release', 1)

        def poll_release():
            for _ in range(8):
                try:
                    data, peer = sock.recvfrom(1024)
                except BlockingIOError:
                    break
                try:
                    if peer != (args.windows_ip, args.port):
                        raise ValueError('Unexpected release peer')
                    fake = decode_trial_release(data, args.flight_session_id)
                except (UnicodeError, ValueError, TypeError, KeyError):
                    node.get_logger().warning('Trial release rejected')
                    continue
                release_publisher.publish(Bool(data=fake))

        node.create_timer(0.02, poll_release)

    def forward(msg):
        try:
            if (args.flight_session_id is not None and msg.tracking_allowed and
                    msg.source_header.frame_id != args.flight_source_frame_id):
                node.get_logger().warning('Motion from unexpected source was not forwarded')
                return
            sock.sendto(encode_approved(msg, args.flight_session_id),
                        (args.windows_ip, args.port))
        except (OSError, ValueError, TypeError) as error:
            node.get_logger().warning('Decision not forwarded: ' + str(error))

    node.create_subscription(ApprovedCommand, 'approved_command', forward, 1)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
