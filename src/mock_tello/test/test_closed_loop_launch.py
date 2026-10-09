"""Actual ROS feedback loop; the probe observes and calls operator services only."""

import json
import math
import os
import signal
import time
import unittest

from drone_interfaces.msg import ApprovedCommand, CandidateCommand, HandTarget
import launch
import launch_ros.actions
import launch_testing
import launch_testing.actions
import launch_testing.asserts
import rclpy
from std_msgs.msg import String
from std_srvs.srv import SetBool, Trigger


def generate_test_description():
    cases = {
        'static': {'sensor_delay': 0.08, 'noise_stddev': 0.004},
        'dropout': {'scenario': 'dropout', 'fault_start': 1.5},
        'loss': {'scenario': 'loss', 'fault_start': 1.5, 'fault_duration': 2.5},
        'stale': {'sensor_delay': 0.3},
    }
    processes = {}
    for name, parameters in cases.items():
        for key, package, executable in (
            ('controller', 'tracking_controller', 'tracking_controller_node'),
            ('manager', 'mission_manager', 'mission_manager_node'),
            ('mock', 'mock_tello', 'mock_tello_node'),
        ):
            processes[name + '_' + key] = launch_ros.actions.Node(
                package=package, executable=executable, namespace='mock_test_' + name,
                parameters=[{'use_sim_time': False}, parameters if key == 'mock' else {}],
                arguments=['--ros-args', '--log-level', 'warn'], output='screen')
    return launch.LaunchDescription([
        *processes.values(), launch_testing.actions.ReadyToTest(),
    ]), {'processes': processes}


class TestClosedLoop(unittest.TestCase):
    def setUp(self):
        case = {'test_dropout_neutral_and_reacquisition': 'dropout',
                'test_loss_latches_mock_and_requires_explicit_reset': 'loss',
                'test_delayed_observations_are_not_restamped': 'stale'}.get(
                    self._testMethodName, 'static')
        self.case = case
        rclpy.init()
        self.addCleanup(rclpy.shutdown)
        self.node = rclpy.create_node('feedback_probe', namespace='mock_test_' + case)
        self.addCleanup(self.node.destroy_node)
        self.approved, self.targets, self.candidates, self.states = [], [], [], []
        self.output_floor = 0
        self.subscriptions = [
            self.node.create_subscription(ApprovedCommand, 'approved_command',
                                          self.record_approved, 100),
            self.node.create_subscription(HandTarget, 'hand_target',
                                          lambda msg: self.targets.append((time.monotonic(), msg)), 100),
            self.node.create_subscription(CandidateCommand, 'candidate_command',
                                          lambda msg: self.candidates.append((time.monotonic(), msg)), 100),
            self.node.create_subscription(String, 'mock_tello/state',
                                          lambda msg: self.states.append(
                                              (time.monotonic(), json.loads(msg.data))), 100),
        ]
        self.autonomy = self.node.create_client(SetBool, 'mission_manager/set_autonomy')
        self.takeover = self.node.create_client(Trigger, 'mission_manager/manual_takeover')
        self.reset = self.node.create_client(Trigger, 'mock_tello/reset')
        self.wait(lambda: all(sub.get_publisher_count() == 1 for sub in self.subscriptions)
                  and all(client.service_is_ready()
                          for client in (self.autonomy, self.takeover, self.reset)), 10)
        self.disable_and_reset()

    @staticmethod
    def stamp(stamp):
        return stamp.sec * 1_000_000_000 + stamp.nanosec

    def record_approved(self, msg):
        if msg.tracking_allowed:
            self.assertTrue(msg.autonomy_enabled)
            self.assertFalse(msg.request_land)
            self.assertEqual(msg.source_header.frame_id, 'mock_camera')
            self.assertTrue(math.isfinite(msg.lateral) and math.isfinite(msg.vertical))
            self.assertLessEqual(abs(msg.lateral), 10)
            self.assertLessEqual(abs(msg.vertical), 10)
            age = (self.stamp(msg.header.stamp) - self.stamp(msg.source_header.stamp)) / 1e9
            self.assertGreaterEqual(age, 0)
            self.assertLess(age, 0.25)
        else:
            self.assertEqual((msg.lateral, msg.vertical), (0, 0))
        if self.stamp(msg.header.stamp) >= self.output_floor:
            self.approved.append((time.monotonic(), msg))

    def wait(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            rclpy.spin_once(self.node, timeout_sec=0.01)
        self.assertTrue(predicate(), 'Timed out waiting for closed-loop behavior')

    def pump(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            rclpy.spin_once(self.node, timeout_sec=0.01)

    def service(self, client, request):
        future = client.call_async(request)
        self.wait(future.done)
        response = future.result()
        self.assertIsNotNone(response)
        return response

    def disable_and_reset(self):
        self.assertTrue(self.service(self.takeover, Trigger.Request()).success)
        barrier = self.node.get_clock().now().nanoseconds
        self.wait(lambda: any(not msg.autonomy_enabled and self.stamp(msg.header.stamp) >= barrier
                              for _, msg in self.approved))
        # A service response/probe receipt does not imply the mock received the
        # disabled heartbeat. Retry a bounded reset until that independent edge arrives.
        deadline = time.monotonic() + 3
        while True:
            response = self.service(self.reset, Trigger.Request())
            if response.success:
                break
            self.assertLess(time.monotonic(), deadline, response.message)
            self.pump(0.02)
        self.approved.clear()
        self.output_floor = barrier
        self.targets.clear()
        self.candidates.clear()
        self.states.clear()
        self.wait(lambda: self.states and self.states[-1][1]['elapsed'] < 0.5)

    def enable(self):
        self.approved.clear()
        self.assertTrue(self.service(self.autonomy, SetBool.Request(data=True)).success)

    def acquire(self):
        self.enable()
        self.wait(lambda: any(msg.tracking_allowed for _, msg in self.approved))

    def test_static_convergence_signs_header_chain_and_takeover(self):
        self.wait(lambda: len(self.targets) >= 5 and self.approved)
        self.assertTrue(all(not msg.autonomy_enabled for _, msg in self.approved))
        self.assertAlmostEqual(self.states[-1][1]['error_x'], 0.6, places=5)
        self.acquire()
        moving = next(msg for _, msg in self.approved if msg.tracking_allowed)
        self.assertGreater(moving.lateral, 0)
        self.assertGreater(moving.vertical, 0)
        self.wait(lambda: any(msg.header == moving.source_header for _, msg in self.targets))
        self.wait(lambda: any(msg.header == moving.source_header for _, msg in self.candidates))
        self.wait(lambda: self.states[-1][1]['elapsed'] >= 6, 8)
        tail = [state for _, state in self.states if state['elapsed'] >= 5]
        self.assertGreaterEqual(len(tail), 5)
        self.assertLessEqual(max(abs(state['error_x']) for state in tail), 0.09)
        self.assertLessEqual(max(abs(state['error_y']) for state in tail), 0.09)
        self.assertTrue(all(not state['mock_terminal'] for state in tail))
        start = len(self.approved)
        self.assertTrue(self.service(self.takeover, Trigger.Request()).success)
        barrier = self.node.get_clock().now().nanoseconds
        self.wait(lambda: self.states[-1][1]['consumer_mode'] == 'YIELDED')
        self.pump(0.4)
        after = [msg for _, msg in self.approved[start:]
                 if self.stamp(msg.header.stamp) >= barrier]
        self.assertTrue(after)
        self.assertTrue(all(not msg.autonomy_enabled for msg in after))
        self.assertEqual(self.states[-1][1]['lateral'], 0)
        self.assertEqual(self.states[-1][1]['vertical'], 0)

    def test_dropout_neutral_and_reacquisition(self):
        self.acquire()
        self.wait(lambda: any(not msg.detected for _, msg in self.targets))
        missing_stamp = next(self.stamp(msg.header.stamp)
                             for _, msg in self.targets if not msg.detected)
        self.wait(lambda: any(msg.autonomy_enabled and not msg.tracking_allowed
                              and not msg.request_land for _, msg in self.approved
                              if self.stamp(msg.header.stamp) >= missing_stamp))
        self.wait(lambda: self.states[-1][1]['consumer_mode'] == 'NEUTRAL')
        self.assertEqual(self.states[-1][1]['lateral'], 0)
        self.assertEqual(self.states[-1][1]['vertical'], 0)
        self.wait(lambda: self.states[-1][1]['elapsed'] >= 2.7, 4)
        self.assertTrue(self.states[-1][1]['consumer_mode'] == 'TRACKING')
        self.assertFalse(any(msg.request_land for _, msg in self.approved))
        self.assertTrue(any(not msg.detected for _, msg in self.targets))
        # Recovery must be preceded by distinct usable frames (not heartbeats).
        missing = [i for i, (_, msg) in enumerate(self.targets) if not msg.detected]
        first_good = self.targets[missing[-1] + 1][1]
        recovery = next(msg for _, msg in self.approved if msg.tracking_allowed
                        and self.stamp(msg.source_header.stamp) >= self.stamp(first_good.header.stamp))
        good_after = {self.stamp(msg.header.stamp) for _, msg in self.targets[missing[-1] + 1:]
                      if msg.detected and self.stamp(msg.header.stamp)
                      <= self.stamp(recovery.source_header.stamp)}
        self.assertGreaterEqual(len(good_after), 5)

    def test_loss_latches_mock_and_requires_explicit_reset(self):
        self.acquire()
        self.wait(lambda: any(msg.request_land for _, msg in self.approved), 5)
        self.wait(lambda: self.states[-1][1]['mock_terminal'])
        self.assertFalse(self.service(self.reset, Trigger.Request()).success)
        self.assertFalse(self.service(self.autonomy, SetBool.Request(data=False)).success)
        self.assertFalse(self.service(self.autonomy, SetBool.Request(data=True)).success)
        self.wait(lambda: self.states[-1][1]['elapsed'] >= 4.6, 3)
        self.assertTrue(self.states[-1][1]['mock_terminal'])
        self.assertTrue(self.service(self.takeover, Trigger.Request()).success)
        self.pump(0.3)
        self.assertTrue(self.states[-1][1]['mock_terminal'])
        self.assertEqual(self.states[-1][1]['lateral'], 0)
        self.disable_and_reset()
        self.assertFalse(self.states[-1][1]['mock_terminal'])
        self.acquire()

    def test_delayed_observations_are_not_restamped(self):
        self.enable()
        self.wait(lambda: any(msg.request_land for _, msg in self.approved), 4)
        self.assertFalse(any(msg.tracking_allowed for _, msg in self.approved))
        self.assertTrue(self.targets)
        self.assertTrue(any(msg.detected for _, msg in self.targets))
        # ROS system clock and wall clock share the epoch in this wall-time test.
        now_wall = time.time()
        latest_receipt, latest_target = self.targets[-1]
        approximate_delivery_age = (now_wall - (time.monotonic() - latest_receipt)
                                    - self.stamp(latest_target.header.stamp) / 1e9)
        self.assertGreaterEqual(approximate_delivery_age, 0.28)
        self.assertTrue(all(not msg.target_valid for _, msg in self.candidates))

    def test_mock_neutralizes_when_supervisor_process_is_suspended(self, proc_info, processes):
        self.acquire()
        self.wait(lambda: self.states[-1][1]['lateral'] > 0)
        process = processes['static_manager']
        proc_info.assertWaitForStartup(process, timeout=5)
        pid = proc_info[process].pid
        os.kill(pid, signal.SIGSTOP)
        try:
            self.wait(lambda: self.states[-1][1]['consumer_mode'].endswith('TIMEOUT'))
            self.assertEqual(self.states[-1][1]['lateral'], 0)
            self.assertEqual(self.states[-1][1]['vertical'], 0)
            self.pump(0.3)
            self.assertLess(abs(self.states[-1][1]['apparent_velocity_x']), 0.08)
        finally:
            os.kill(pid, signal.SIGCONT)


@launch_testing.post_shutdown_test()
class TestShutdown(unittest.TestCase):
    def test_all_processes_exit_cleanly(self, proc_info, processes):
        for process in processes.values():
            launch_testing.asserts.assertExitCodes(proc_info, process=process)
