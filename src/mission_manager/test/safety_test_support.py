"""ROS probes shared by the supervisor and real-chain launch tests."""

import math
import time

from drone_interfaces.msg import ApprovedCommand, CandidateCommand, HandTarget
import rclpy
from rclpy.parameter import Parameter
from std_srvs.srv import SetBool, Trigger


class SafetyProbe:
    namespace = 'supervisor_edges'
    combined = False
    controlled_clock = False

    def setUp(self):
        rclpy.init()
        self.addCleanup(rclpy.shutdown)
        self.node = rclpy.create_node(
            'safety_probe', namespace=self.namespace,
            parameter_overrides=[Parameter('use_sim_time', value=self.controlled_clock)],
        )
        self.addCleanup(self.node.destroy_node)
        self.messages = []
        self.candidates = []
        self.sent = {}
        self.subscription = self.node.create_subscription(
            ApprovedCommand, 'approved_command', self.record_output, 100,
        )
        self.publisher = self.node.create_publisher(
            HandTarget if self.combined else CandidateCommand,
            'hand_target' if self.combined else 'candidate_command', 10,
        )
        if self.combined:
            self.candidate_subscription = self.node.create_subscription(
                CandidateCommand, 'candidate_command',
                lambda msg: self.candidates.append((time.monotonic(), msg)), 100,
            )
        self.autonomy = self.node.create_client(SetBool, 'mission_manager/set_autonomy')
        self.takeover = self.node.create_client(Trigger, 'mission_manager/manual_takeover')
        if self.controlled_clock:
            from rosgraph_msgs.msg import Clock
            # Positive, frozen ROS time; only steady time advances during waits.
            self.clock_ns = getattr(type(self), 'clock_epoch', 100_000_000_000) + 10_000_000_000
            type(self).clock_epoch = self.clock_ns
            self.source_ns = self.clock_ns - 100_000_000
            self.clock_pub = self.node.create_publisher(Clock, '/clock', 10)

            def publish_clock():
                clock = Clock()
                clock.clock.sec, clock.clock.nanosec = divmod(self.clock_ns, 1_000_000_000)
                self.clock_pub.publish(clock)

            self.timer(publish_clock, 0.01)
            self.wait(lambda: self.node.get_clock().now().nanoseconds == self.clock_ns)
        self.wait(lambda: (
            self.publisher.get_subscription_count() == 1
            and self.subscription.get_publisher_count() == 1
            and (not self.combined or self.candidate_subscription.get_publisher_count() == 1)
            and self.autonomy.service_is_ready() and self.takeover.service_is_ready()
        ), timeout=5.0)
        if self.controlled_clock:
            self.wait(lambda: any(self.stamp_ns(msg.header.stamp) == self.clock_ns
                                  for _, msg in self.messages))
        self.reset()

    @staticmethod
    def stamp_ns(stamp):
        return stamp.sec * 1_000_000_000 + stamp.nanosec

    def record_output(self, msg):
        # Check every observed decision, including transitions and service outputs.
        if msg.tracking_allowed:
            self.assertTrue(msg.autonomy_enabled)
            self.assertFalse(msg.request_land)
            self.assertTrue(math.isfinite(msg.lateral) and math.isfinite(msg.vertical))
            self.assertLessEqual(abs(msg.lateral), 10.0)
            self.assertLessEqual(abs(msg.vertical), 10.0)
            self.assertGreater(self.stamp_ns(msg.source_header.stamp), 0)
            age = (self.stamp_ns(msg.header.stamp) - self.stamp_ns(msg.source_header.stamp)) / 1e9
            self.assertGreaterEqual(age, 0.0)
            self.assertLess(age, 0.25)
        else:
            self.assertEqual((msg.lateral, msg.vertical), (0.0, 0.0))
        if msg.request_land:
            self.assertTrue(msg.autonomy_enabled)
            self.assertFalse(msg.tracking_allowed)
        self.messages.append((time.monotonic(), msg))

    def wait(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            rclpy.spin_once(self.node, timeout_sec=0.01)
        self.assertTrue(predicate(), 'Timed out waiting for ROS behavior')

    def pump(self, duration):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            rclpy.spin_once(self.node, timeout_sec=0.01)

    def timer(self, callback, period=0.05):
        timer = self.node.create_timer(period, callback, clock=rclpy.clock.Clock())
        self.addCleanup(self.node.destroy_timer, timer)
        return timer

    def service(self, client, request, success=True):
        future = client.call_async(request)
        self.wait(future.done)
        response = future.result()
        self.assertIsNotNone(response)
        self.assertEqual(response.success, success, response.message)
        return response

    def reset(self, ordinary=False):
        start = len(self.messages)
        if ordinary:
            self.service(self.autonomy, SetBool.Request(data=False))
        else:
            self.service(self.takeover, Trigger.Request())
        self.wait(lambda: any(
            not msg.autonomy_enabled and self.stamp_ns(msg.source_header.stamp) == 0
            for _, msg in self.messages[start:]
        ))
        self.messages.clear()

    def enable(self):
        start = len(self.messages)
        self.service(self.autonomy, SetBool.Request(data=True))
        self.wait(lambda: any(msg.autonomy_enabled for _, msg in self.messages[start:]))
        self.messages.clear()
        return time.monotonic()

    def make_input(self, frame='camera', **changes):
        msg = HandTarget() if self.combined else CandidateCommand()
        if self.controlled_clock:
            self.source_ns += 1_000_000
            msg.header.stamp.sec, msg.header.stamp.nanosec = divmod(self.source_ns, 1_000_000_000)
        else:
            msg.header.stamp = self.node.get_clock().now().to_msg()
        msg.header.frame_id = frame
        if self.combined:
            msg.detected, msg.tracked_hands = True, 1
            msg.error_x, msg.error_y = 0.5, -0.3
        else:
            msg.target_valid = True
            msg.lateral, msg.vertical = 5.0, -3.0
        for field, value in changes.items():
            setattr(msg, field, value)
        return msg

    def publish(self, msg):
        self.sent[(self.stamp_ns(msg.header.stamp), msg.header.frame_id)] = msg
        self.publisher.publish(msg)

    def observe(self, frame='camera', **changes):
        msg = self.make_input(frame, **changes)
        start = len(self.messages)
        self.publish(msg)
        self.wait(lambda: any(
            output.source_header == msg.header for _, output in self.messages[start:]
        ))
        return msg

    def acquire(self, frame='camera'):
        for index in range(5):
            msg = self.observe(frame)
            if index < 4:
                self.assertFalse(self.messages[-1][1].tracking_allowed)
        self.wait(lambda: any(
            output.tracking_allowed and output.source_header == msg.header
            for _, output in self.messages
        ))
        return msg

    def stream(self, frame='camera', **changes):
        return self.timer(lambda: self.publish(self.make_input(frame, **changes)))

    def stop_valid_stream(self, timer):
        timer.cancel()
        # The final frame must pass through the supervisor before a service
        # starts a new acquisition. Cancelling a timer cannot drain DDS traffic.
        final = list(self.sent.values())[-1]
        self.wait(lambda: any(msg.source_header == final.header for _, msg in self.messages))

    def neutral(self, start=0):
        self.wait(lambda: any(
            msg.autonomy_enabled and not msg.tracking_allowed and not msg.request_land
            for _, msg in self.messages[start:]
        ))
        return next(i for i in range(start, len(self.messages)) if (
            self.messages[i][1].autonomy_enabled
            and not self.messages[i][1].tracking_allowed
            and not self.messages[i][1].request_land
        ))

    def landing(self, start=0, timeout=2.3):
        self.wait(lambda: any(msg.request_land for _, msg in self.messages[start:]), timeout)
        return next(i for i in range(start, len(self.messages))
                    if self.messages[i][1].request_land)

    def assert_neutral(self, start=0, landed=None, disabled=False):
        self.assertGreater(len(self.messages[start:]), 0)
        for _, msg in self.messages[start:]:
            self.assertEqual(msg.autonomy_enabled, not disabled)
            self.assertFalse(msg.tracking_allowed)
            if landed is not None:
                self.assertEqual(msg.request_land, landed)

    def repeat_enable(self):
        futures = []
        timer = self.timer(
            lambda: futures.append(self.autonomy.call_async(SetBool.Request(data=True))), 0.2,
        )
        return timer, futures
