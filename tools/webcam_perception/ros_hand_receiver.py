import argparse
import json
import math
import socket

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node

from drone_interfaces.msg import HandTarget


MAX_AGE_NS = 250_000_000


def decode_observation(packet, now_ns):
    data = json.loads(packet.decode("utf-8"))

    capture_ns = data["capture_time_ns"]
    detected = data["detected"]
    hands = data["tracked_hands"]
    errors = (data["error_x"], data["error_y"])

    if type(capture_ns) is not int or capture_ns <= 0:
        raise ValueError("Invalid timestamp")

    if not 0 <= now_ns - capture_ns < MAX_AGE_NS:
        raise ValueError("Stale or future observation")

    if type(detected) is not bool:
        raise ValueError("Invalid detected flag")

    if type(hands) is not int or not 0 <= hands <= 255:
        raise ValueError("Invalid hand count")

    if any(type(value) not in (int, float) for value in errors):
        raise ValueError("Invalid error types")

    error_x, error_y = map(float, errors)

    if not all(
        math.isfinite(value) and abs(value) <= 1.0
        for value in (error_x, error_y)
    ):
        raise ValueError("Invalid coordinates")

    if detected and hands != 1:
        raise ValueError("Ambiguous target marked valid")

    if not detected and (error_x != 0.0 or error_y != 0.0):
        raise ValueError("Invalid target must have zero errors")

    seconds, nanoseconds = divmod(capture_ns, 1_000_000_000)

    if seconds > 2_147_483_647:
        raise ValueError("Timestamp outside ROS message range")

    message = HandTarget()
    message.header.stamp.sec = seconds
    message.header.stamp.nanosec = nanoseconds
    message.header.frame_id = "webcam_optical_frame"
    message.detected = detected
    message.error_x = error_x
    message.error_y = error_y
    message.tracked_hands = hands

    return capture_ns, message


class WebcamReceiver(Node):
    def __init__(self, bind_address):
        super().__init__("webcam_receiver", namespace="/webcam_test")

        if self.get_parameter("use_sim_time").value:
            raise ValueError("This receiver requires use_sim_time=false")

        self.publisher = self.create_publisher(
            HandTarget, "hand_target", 1
        )

        self.receiver = socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM
        )
        self.receiver.bind((bind_address, 5005))
        self.receiver.setblocking(False)

        self.last_published_stamp = 0
        self.timer = self.create_timer(0.005, self.receive_packets)

        self.get_logger().info(
            f"Listening on {bind_address}:5005; "
            "publishing /webcam_test/hand_target"
        )

    def receive_packets(self):
        if self.get_parameter("use_sim_time").value:
            return

        newest = None

        # Bound the work in each callback.
        for _ in range(64):
            try:
                packet, _ = self.receiver.recvfrom(4096)
            except BlockingIOError:
                break

            self.get_logger().info(
                "UDP packet received",
                throttle_duration_sec=1.0,
            )
            try:
                observation = decode_observation(
                    packet,
                    self.get_clock().now().nanoseconds,
                )
            except (
                UnicodeError, ValueError, KeyError,
                TypeError, OverflowError,
            ) as error:
                self.get_logger().warning(
                    f"Dropped observation: {error}",
                    throttle_duration_sec=1.0,
                )
                continue

            capture_ns, message = observation


            if capture_ns <= self.last_published_stamp:
                continue

            if newest is None or capture_ns > newest[0]:
                newest = observation

        if newest is not None:
            capture_ns, message = newest
            age_ns = self.get_clock().now().nanoseconds - capture_ns

            # Check freshness again immediately before publishing.
            if 0 <= age_ns < MAX_AGE_NS:
                self.publisher.publish(message)
                self.last_published_stamp = capture_ns

            self.get_logger().info(
                    f"Published HandTarget; age_ms={age_ns / 1_000_000:.1f}",
                    throttle_duration_sec=1.0,
                )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", required=True)
    args, ros_args = parser.parse_known_args()

    rclpy.init(args=ros_args)
    node = None

    try:
        node = WebcamReceiver(args.bind)
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if node is not None:
            node.receiver.close()
            node.destroy_node()

        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()