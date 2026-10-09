import time
import unittest

import launch
import launch_ros.actions
import launch_testing
import launch_testing.actions
import launch_testing.asserts
import rclpy

from drone_interfaces.msg import ApprovedCommand, CandidateCommand
from std_srvs.srv import SetBool, Trigger


def generate_test_description():
    mission_manager = launch_ros.actions.Node(
        package="mission_manager",
        executable="mission_manager_node",
        namespace="approved_output_test",
        parameters=[{"use_sim_time": False}],
        output="screen",
    )

    return (
        launch.LaunchDescription([
            mission_manager,
            launch_testing.actions.ReadyToTest(),
        ]),
        {"mission_manager": mission_manager},
    )


class TestApprovedOutput(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init()
        cls.node = rclpy.create_node(
            "approved_output_probe",
            namespace="approved_output_test",
        )

        cls.messages = []

        cls.subscription = cls.node.create_subscription(
            ApprovedCommand,
            "approved_command",
            lambda message: cls.messages.append(
                (time.monotonic(), message)
            ),
            1,
        )

        cls.publisher = cls.node.create_publisher(
            CandidateCommand,
            "candidate_command",
            1,
        )

        cls.autonomy_client = cls.node.create_client(
            SetBool,
            "mission_manager/set_autonomy",
        )

        cls.takeover_client = cls.node.create_client(
            Trigger,
            "mission_manager/manual_takeover",
        )

    @classmethod
    def tearDownClass(cls):
        cls.node.destroy_node()
        rclpy.shutdown()

    def wait_until(self, predicate, timeout):
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            if predicate():
                return True

            rclpy.spin_once(self.node, timeout_sec=0.05)

        return predicate()

    @staticmethod
    def stamp_ns(stamp):
        return stamp.sec * 1_000_000_000 + stamp.nanosec

    def call_service(self, client, request):
        future = client.call_async(request)

        completed = self.wait_until(future.done, timeout=2.0)
        self.assertTrue(completed, "Operator service did not respond")

        response = future.result()
        self.assertIsNotNone(response)
        self.assertTrue(response.success, response.message)
        return response

    def setUp(self):
        discovered = self.wait_until(
            lambda: (
                self.subscription.get_publisher_count() == 1
                and self.publisher.get_subscription_count() == 1
                and self.autonomy_client.service_is_ready()
                and self.takeover_client.service_is_ready()
            ),
            timeout=5.0,
        )
        self.assertTrue(discovered, "Node interfaces not discovered")

        self.call_service(self.takeover_client, Trigger.Request())
        self.messages.clear()

    def test_disabled_output_continues_without_input(self):
        continued = self.wait_until(
            lambda: (
                len(self.messages) >= 2
                and self.messages[-1][0] - self.messages[0][0] >= 0.3
            ),
            timeout=3.0,
        )
        self.assertTrue(continued, "Approved heartbeat did not continue")

        for _, message in self.messages:
            self.assertFalse(message.autonomy_enabled)
            self.assertFalse(message.tracking_allowed)
            self.assertFalse(message.request_land)
            self.assertEqual(message.lateral, 0.0)
            self.assertEqual(message.vertical, 0.0)

            self.assertGreater(self.stamp_ns(message.header.stamp), 0)
            self.assertEqual(
                self.stamp_ns(message.source_header.stamp), 0
            )
            self.assertEqual(message.source_header.frame_id, "")

        first = self.messages[0][1]
        last = self.messages[-1][1]

        self.assertGreater(
            self.stamp_ns(last.header.stamp),
            self.stamp_ns(first.header.stamp),
            "Publication timestamp did not advance",
        )

    def test_fresh_candidates_produce_approved_motion(self):
        sent_stamps = set()

        def publish_candidate():
            candidate = CandidateCommand()
            candidate.header.stamp = self.node.get_clock().now().to_msg()
            candidate.header.frame_id = "test_camera"
            candidate.target_valid = True
            candidate.lateral = 5.0
            candidate.vertical = -3.0

            sent_stamps.add(self.stamp_ns(candidate.header.stamp))
            self.publisher.publish(candidate)

        timer = self.node.create_timer(0.05, publish_candidate)

        try:
            request = SetBool.Request()
            request.data = True
            self.call_service(self.autonomy_client, request)

            def matching_outputs():
                return [
                    message
                    for _, message in self.messages
                    if (
                        message.tracking_allowed
                        and self.stamp_ns(message.source_header.stamp)
                        in sent_stamps
                    )
                ]

            acquired = self.wait_until(
                lambda: bool(matching_outputs()),
                timeout=2.0,
            )
            self.assertTrue(
                acquired,
                "Fresh candidates did not produce approved motion",
            )

            approved = matching_outputs()[0]

            self.assertTrue(approved.autonomy_enabled)
            self.assertTrue(approved.tracking_allowed)
            self.assertFalse(approved.request_land)
            self.assertEqual(approved.lateral, 5.0)
            self.assertEqual(approved.vertical, -3.0)
            self.assertEqual(
                approved.source_header.frame_id, "test_camera"
            )

            self.assertGreaterEqual(
                self.stamp_ns(approved.header.stamp),
                self.stamp_ns(approved.source_header.stamp),
            )

            # Take over while the candidate timer continues running.
            self.call_service(self.takeover_client, Trigger.Request())

            # Exclude decisions published before takeover completed.
            cutoff_ns = self.node.get_clock().now().nanoseconds
            sent_count_at_takeover = len(sent_stamps)
            self.messages.clear()

            def outputs_after_takeover():
                return [
                    (received_at, message)
                    for received_at, message in self.messages
                    if self.stamp_ns(message.header.stamp) >= cutoff_ns
                ]

            def continued_after_takeover():
                outputs = outputs_after_takeover()
                return (
                    len(outputs) >= 2
                    and outputs[-1][0] - outputs[0][0] >= 0.4
                    and len(sent_stamps) - sent_count_at_takeover >= 5
                )

            continued = self.wait_until(
                continued_after_takeover,
                timeout=2.0,
            )
            self.assertTrue(
                continued,
                "Output or fresh input did not continue after takeover",
            )

            for _, message in outputs_after_takeover():
                self.assertFalse(message.autonomy_enabled)
                self.assertFalse(message.tracking_allowed)
                self.assertFalse(message.request_land)
                self.assertEqual(message.lateral, 0.0)
                self.assertEqual(message.vertical, 0.0)

        finally:
            self.node.destroy_timer(timer)
            self.call_service(self.takeover_client, Trigger.Request())
    def test_input_silence_produces_neutral_then_landing(self):
        sent_stamps = set()

        def publish_candidate():
            candidate = CandidateCommand()
            candidate.header.stamp = self.node.get_clock().now().to_msg()
            candidate.header.frame_id = "loss_test_camera"
            candidate.target_valid = True
            candidate.lateral = 5.0
            candidate.vertical = -3.0

            sent_stamps.add(self.stamp_ns(candidate.header.stamp))
            self.publisher.publish(candidate)

        timer = self.node.create_timer(0.05, publish_candidate)

        try:
            request = SetBool.Request()
            request.data = True
            self.call_service(self.autonomy_client, request)

            acquired = self.wait_until(
                lambda: any(
                    message.tracking_allowed
                    and self.stamp_ns(message.source_header.stamp)
                    in sent_stamps
                    for _, message in self.messages
                ),
                timeout=2.0,
            )
            self.assertTrue(acquired, "Tracking was not acquired")

            # Stop input while leaving the supervisor running.
            self.node.destroy_timer(timer)
            timer = None

            cutoff_ns = self.node.get_clock().now().nanoseconds
            self.messages.clear()

            def neutral_outputs():
                return [
                    message
                    for _, message in self.messages
                    if (
                        self.stamp_ns(message.header.stamp) >= cutoff_ns
                        and message.autonomy_enabled
                        and not message.tracking_allowed
                        and not message.request_land
                    )
                ]

            neutral_received = self.wait_until(
                lambda: bool(neutral_outputs()),
                timeout=1.0,
            )
            self.assertTrue(
                neutral_received,
                "Input silence did not produce pre-landing neutral output",
            )

            neutral = neutral_outputs()[0]
            self.assertEqual(neutral.lateral, 0.0)
            self.assertEqual(neutral.vertical, 0.0)

            source_stamp = self.stamp_ns(neutral.source_header.stamp)
            self.assertIn(source_stamp, sent_stamps)

            def landing_outputs():
                return [
                    message
                    for _, message in self.messages
                    if (
                        self.stamp_ns(message.header.stamp) >= cutoff_ns
                        and message.request_land
                    )
                ]

            landing_received = self.wait_until(
                lambda: bool(landing_outputs()),
                timeout=3.0,
            )
            self.assertTrue(
                landing_received,
                "Sustained input silence did not request landing",
            )

            landing = landing_outputs()[0]
            self.assertTrue(landing.autonomy_enabled)
            self.assertFalse(landing.tracking_allowed)
            self.assertEqual(landing.lateral, 0.0)
            self.assertEqual(landing.vertical, 0.0)

            self.assertEqual(
                self.stamp_ns(landing.source_header.stamp),
                source_stamp,
            )
            self.assertEqual(
                landing.source_header.frame_id,
                "loss_test_camera",
            )
            self.assertGreater(
                self.stamp_ns(landing.header.stamp),
                self.stamp_ns(neutral.header.stamp),
            )

                        # Resume fresh input while landing remains latched.
            timer = self.node.create_timer(0.05, publish_candidate)

            # Neither ordinary disable nor enable may clear the latch.
            for enable in (False, True):
                request = SetBool.Request()
                request.data = enable
                future = self.autonomy_client.call_async(request)

                self.assertTrue(
                    self.wait_until(future.done, timeout=2.0),
                    "Autonomy service did not respond",
                )

                response = future.result()
                self.assertIsNotNone(response)
                self.assertFalse(
                    response.success,
                    "Ordinary service request cleared the landing latch",
                )

            cutoff_ns = self.node.get_clock().now().nanoseconds
            sent_count = len(sent_stamps)
            self.messages.clear()

            def outputs_after_requests():
                return [
                    (received_at, message)
                    for received_at, message in self.messages
                    if self.stamp_ns(message.header.stamp) >= cutoff_ns
                ]

            def continued_with_fresh_input():
                outputs = outputs_after_requests()
                return (
                    len(outputs) >= 2
                    and outputs[-1][0] - outputs[0][0] >= 0.4
                    and len(sent_stamps) - sent_count >= 5
                )

            self.assertTrue(
                self.wait_until(
                    continued_with_fresh_input,
                    timeout=2.0,
                ),
                "Fresh input or latched output did not continue",
            )

            for _, message in outputs_after_requests():
                self.assertTrue(message.autonomy_enabled)
                self.assertFalse(message.tracking_allowed)
                self.assertTrue(message.request_land)
                self.assertEqual(message.lateral, 0.0)
                self.assertEqual(message.vertical, 0.0)

        finally:
            if timer is not None:
                self.node.destroy_timer(timer)

            self.call_service(self.takeover_client, Trigger.Request())

    def test_invalid_candidates_block_approved_motion(self):
        # name, target_valid, lateral, vertical, timestamp condition
        cases = [
            ("invalid_target", False, 5.0, -3.0, "fresh"),
            ("missing_stamp", True, 5.0, -3.0, "missing"),
            ("malformed_stamp", True, 5.0, -3.0, "malformed"),
            ("stale_stamp", True, 5.0, -3.0, "stale"),
            ("future_stamp", True, 5.0, -3.0, "future"),
            ("nan_lateral", True, float("nan"), -3.0, "fresh"),
            ("infinite_vertical", True, 5.0, float("inf"), "fresh"),
            ("excessive_lateral", True, 50.0, -3.0, "fresh"),
            ("excessive_vertical", True, 5.0, -50.0, "fresh"),
        ]

        for name, target_valid, lateral, vertical, stamp_kind in cases:
            with self.subTest(case=name):
                self.call_service(
                    self.takeover_client, Trigger.Request()
                )
                self.messages.clear()

                good_frame = "valid_" + name
                bad_frame = "invalid_" + name
                mode = {"bad": False, "sent": 0}

                def publish_candidate():
                    candidate = CandidateCommand()
                    candidate.header.stamp = (
                        self.node.get_clock().now().to_msg()
                    )
                    candidate.header.frame_id = good_frame
                    candidate.target_valid = True
                    candidate.lateral = 5.0
                    candidate.vertical = -3.0

                    if mode["bad"]:
                        candidate.header.frame_id = bad_frame
                        candidate.target_valid = target_valid
                        candidate.lateral = lateral
                        candidate.vertical = vertical

                        if stamp_kind == "missing":
                            candidate.header.stamp.sec = 0
                            candidate.header.stamp.nanosec = 0
                        elif stamp_kind == "malformed":
                            candidate.header.stamp.nanosec = 1_000_000_000
                        elif stamp_kind == "stale":
                            candidate.header.stamp.sec -= 1
                        elif stamp_kind == "future":
                            candidate.header.stamp.sec += 1

                        mode["sent"] += 1

                    self.publisher.publish(candidate)

                timer = self.node.create_timer(0.05, publish_candidate)

                try:
                    request = SetBool.Request()
                    request.data = True
                    self.call_service(self.autonomy_client, request)

                    self.assertTrue(
                        self.wait_until(
                            lambda: any(
                                message.tracking_allowed
                                and message.source_header.frame_id
                                == good_frame
                                for _, message in self.messages
                            ),
                            timeout=2.0,
                        ),
                        "Valid input did not establish tracking",
                    )

                    mode["bad"] = True
                    cutoff_ns = self.node.get_clock().now().nanoseconds
                    self.messages.clear()

                    def outputs_after_switch():
                        return [
                            (received_at, message)
                            for received_at, message in self.messages
                            if self.stamp_ns(message.header.stamp)
                            >= cutoff_ns
                        ]

                    def neutral_outputs():
                        return [
                            message
                            for _, message in outputs_after_switch()
                            if (
                                message.autonomy_enabled
                                and not message.tracking_allowed
                            )
                        ]

                    self.assertTrue(
                        self.wait_until(
                            lambda: bool(neutral_outputs()),
                            timeout=1.0,
                        ),
                        "Invalid input did not block motion",
                    )

                    neutral_stamp = self.stamp_ns(
                        neutral_outputs()[0].header.stamp
                    )

                    def outputs_after_neutral():
                        return [
                            (received_at, message)
                            for received_at, message
                            in outputs_after_switch()
                            if self.stamp_ns(message.header.stamp)
                            >= neutral_stamp
                        ]

                    def rejection_continues():
                        outputs = outputs_after_neutral()
                        return (
                            mode["sent"] >= 5
                            and len(outputs) >= 2
                            and outputs[-1][0] - outputs[0][0] >= 0.3
                        )

                    self.assertTrue(
                        self.wait_until(
                            rejection_continues, timeout=2.0
                        ),
                        "Invalid stream or neutral output did not continue",
                    )

                    # Never approve an observation from the invalid stream.
                    for _, message in outputs_after_switch():
                        if message.tracking_allowed:
                            self.assertNotEqual(
                                message.source_header.frame_id,
                                bad_frame,
                            )

                    # Once blocked, invalid input cannot restore motion.
                    for _, message in outputs_after_neutral():
                        self.assertTrue(message.autonomy_enabled)
                        self.assertFalse(message.tracking_allowed)
                        self.assertEqual(message.lateral, 0.0)
                        self.assertEqual(message.vertical, 0.0)
                        self.assertEqual(
                            message.source_header.frame_id,
                            good_frame,
                        )

                finally:
                    self.node.destroy_timer(timer)
                    self.call_service(
                        self.takeover_client, Trigger.Request()
                    )

@launch_testing.post_shutdown_test()
class TestProcessExit(unittest.TestCase):
    def test_clean_exit(self, proc_info, mission_manager):
        launch_testing.asserts.assertExitCodes(
            proc_info,
            process=mission_manager,
        )