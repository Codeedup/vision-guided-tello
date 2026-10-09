"""Real supervisor acquisition, deadline and recovery checks with wall-clock ROS time."""

from pathlib import Path
import sys
import time
import unittest

import launch
import launch_ros.actions
import launch_testing
import launch_testing.actions
import launch_testing.asserts
from std_srvs.srv import SetBool

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safety_test_support import SafetyProbe  # noqa: E402,I100


def generate_test_description():
    manager = launch_ros.actions.Node(
        package='mission_manager', executable='mission_manager_node',
        namespace='supervisor_edges', parameters=[{'use_sim_time': False}], output='screen',
    )
    return launch.LaunchDescription([
        manager, launch_testing.actions.ReadyToTest(),
    ]), {'manager': manager}


class TestSupervisorEdges(SafetyProbe, unittest.TestCase):
    def test_initial_acquisition_deadline_survives_repeated_enable(self):
        started = self.enable()
        timer, futures = self.repeat_enable()
        landing = self.landing()
        timer.cancel()
        self.assertLess(self.messages[landing][0] - started, 2.3)
        self.assertGreaterEqual(sum(f.done() and f.result().success for f in futures), 4)
        self.assert_neutral()
        self.assertEqual(self.stamp_ns(self.messages[landing][1].source_header.stamp), 0)

    def test_one_to_four_observations_cannot_postpone_initial_landing(self):
        for count in range(1, 5):
            with self.subTest(observations=count):
                self.reset()
                started = self.enable()
                for _ in range(count):
                    self.observe('partial_acquisition')
                landing = self.landing()
                self.assertLess(self.messages[landing][0] - started, 2.3)
                self.assert_neutral()

    def test_invalid_stream_and_repeated_enable_preserve_initial_deadline(self):
        started = self.enable()
        invalid = self.stream('invalid', target_valid=False)
        repeated, futures = self.repeat_enable()
        landing = self.landing()
        invalid.cancel()
        repeated.cancel()
        self.assertLess(self.messages[landing][0] - started, 2.3)
        self.assertGreaterEqual(sum(f.done() and f.result().success for f in futures), 4)
        self.assert_neutral()

    def test_partial_recovery_and_repeated_enable_preserve_loss_deadline(self):
        self.enable()
        self.acquire()
        start = len(self.messages)
        self.publish(self.make_input('invalid', target_valid=False))
        neutral = self.neutral(start)
        lost_at = self.messages[neutral][0]
        repeated, futures = self.repeat_enable()
        # Fresh observations keep arriving, but every fifth input is invalid.
        count = [0]

        def partial_recovery():
            count[0] += 1
            self.publish(self.make_input('partial_recovery', target_valid=count[0] % 5 != 0))

        recovery = self.timer(partial_recovery)
        landing = self.landing(neutral)
        repeated.cancel()
        recovery.cancel()
        self.assertGreaterEqual(count[0], 10)
        self.assertGreaterEqual(sum(f.done() and f.result().success for f in futures), 4)
        self.assertLess(self.messages[landing][0] - lost_at, 2.3)
        self.assert_neutral(neutral)

    def test_five_observation_recovery_resets_previous_loss_interval(self):
        self.enable()
        self.acquire()
        start = len(self.messages)
        self.publish(self.make_input('invalid', target_valid=False))
        neutral = self.neutral(start)
        lost_at = self.messages[neutral][0]
        self.pump(0.7)
        self.acquire('recovered')
        stream = self.stream('recovered')
        start = len(self.messages)
        self.pump(0.9)
        self.assertGreater(time.monotonic() - lost_at, 1.5)
        self.assertGreaterEqual(len(self.messages[start:]), 20)
        self.assertTrue(all(msg.tracking_allowed for _, msg in self.messages[start:]))
        stream.cancel()
        start = len(self.messages)
        self.publish(self.make_input('second_loss', target_valid=False))
        neutral = self.neutral(start)
        self.pump(0.5)
        self.assert_neutral(neutral, landed=False)
        landing = self.landing(neutral)
        self.assertLess(self.messages[landing][0] - self.messages[neutral][0], 2.3)
        # Fresh recovery attempts and ordinary services cannot clear the latch.
        fresh = self.stream('too_late')
        self.service(self.autonomy, SetBool.Request(data=False), success=False)
        self.service(self.autonomy, SetBool.Request(data=True), success=False)
        self.pump(0.3)
        fresh.cancel()
        self.assert_neutral(landing, landed=True)


@launch_testing.post_shutdown_test()
class TestShutdown(unittest.TestCase):
    def test_clean_exit(self, proc_info, manager):
        launch_testing.asserts.assertExitCodes(proc_info, process=manager)
