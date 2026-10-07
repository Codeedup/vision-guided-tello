import time
import unittest

import launch
import launch_ros.actions
import launch_testing.actions
import launch_testing.asserts
import rclpy

from drone_interfaces.msg import CandidateCommand, HandTarget


def generate_test_description():
    controller = launch_ros.actions.Node(
        package="tracking_controller",
        executable="tracking_controller_node",
        namespace="watchdog_test",
        parameters=[{"use_sim_time": False}],
        output="screen",
    )

    return (
        launch.LaunchDescription([
            controller,
            launch_testing.actions.ReadyToTest(),
        ]),
        {"controller": controller},
    )


class TestTargetWatchdog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init()

    @classmethod
    def tearDownClass(cls):
        rclpy.shutdown()

    def setUp(self):
        self.node = rclpy.create_node(
            "watchdog_probe",
            namespace="watchdog_test",
        )

        self.messages = []

        self.subscription = self.node.create_subscription(
            CandidateCommand,
            "candidate_command",
            self.messages.append,
            10,
        )

        self.publisher = self.node.create_publisher(
            HandTarget,
            "hand_target",
            1,
        )

    def tearDown(self):
        self.node.destroy_node()

    def wait_until(self, condition, timeout_seconds):
        deadline = time.monotonic() + timeout_seconds

        while not condition():
            remaining = deadline - time.monotonic()

            if remaining <= 0.0:
                return False

            rclpy.spin_once(
                self.node,
                timeout_sec=min(0.02, remaining),
            )

        return True

    def test_target_silence_produces_neutral_command(self):
        connected = self.wait_until(
            lambda: (
                self.publisher.get_subscription_count() >= 1
                and self.subscription.get_publisher_count() >= 1
            ),
            timeout_seconds=5.0,
        )

        self.assertTrue(connected, "ROS endpoints did not connect")

        target = HandTarget()
        target.header.stamp = self.node.get_clock().now().to_msg()
        target.header.frame_id = "test_camera"
        target.detected = True
        target.error_x = 0.5
        target.error_y = -0.3
        target.tracked_hands = 1

        def matching_commands():
            return [
                command
                for command in self.messages
                if command.header == target.header
            ]

        # Publish exactly once, then remain silent.
        self.publisher.publish(target)

        valid_received = self.wait_until(
            lambda: any(
                command.target_valid
                for command in matching_commands()
            ),
            timeout_seconds=2.0,
        )

        self.assertTrue(
            valid_received,
            "No valid command received for the fresh target",
        )

        valid_command = next(
            command
            for command in matching_commands()
            if command.target_valid
        )

        self.assertAlmostEqual(valid_command.lateral, 10.0, places=5)
        self.assertAlmostEqual(valid_command.vertical, 6.0, places=5)

        neutral_received = self.wait_until(
            lambda: any(
                not command.target_valid
                for command in matching_commands()
            ),
            timeout_seconds=2.0,
        )

        self.assertTrue(
            neutral_received,
            "Watchdog did not publish an invalid command after silence",
        )

        neutral_command = next(
            command
            for command in matching_commands()
            if not command.target_valid
        )

        self.assertEqual(neutral_command.lateral, 0.0)
        self.assertEqual(neutral_command.vertical, 0.0)


@launch_testing.post_shutdown_test()
class TestControllerShutdown(unittest.TestCase):
    def test_controller_exits_cleanly(self, proc_info, controller):
        launch_testing.asserts.assertExitCodes(
            proc_info,
            process=controller,
        )