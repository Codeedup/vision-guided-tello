"""Freeze positive ROS time to separate source freshness from steady receipt/loss time."""

import copy
import os
from pathlib import Path
import signal
import sys
import unittest

import launch
import launch_ros.actions
import launch_testing
import launch_testing.actions
import launch_testing.asserts

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safety_test_support import SafetyProbe  # noqa: E402


def generate_test_description():
    manager = launch_ros.actions.Node(
        package='mission_manager', executable='mission_manager_node',
        namespace='supervisor_clock', parameters=[{'use_sim_time': True}], output='screen',
    )
    return launch.LaunchDescription([
        manager, launch_testing.actions.ReadyToTest(),
    ]), {'manager': manager}


class TestSupervisorClock(SafetyProbe, unittest.TestCase):
    namespace = 'supervisor_clock'
    controlled_clock = True

    def test_duplicate_stamp_cannot_acquire_or_postpone_landing(self):
        self.enable()
        accepted = self.observe('accepted_once')
        duplicate = copy.deepcopy(accepted)
        duplicate.header.frame_id = 'duplicate_payload'
        duplicate.lateral, duplicate.vertical = -9.0, 9.0
        count = [0]

        def replay():
            count[0] += 1
            self.publish(duplicate)

        timer = self.timer(replay, 0.02)
        landing = self.landing()
        timer.cancel()
        self.assertGreaterEqual(count[0], 10)
        self.assert_neutral()
        for _, msg in self.messages:
            if self.stamp_ns(msg.source_header.stamp):
                self.assertEqual(msg.source_header, accepted.header)
        self.assertEqual(self.stamp_ns(self.messages[landing][1].header.stamp), self.clock_ns)
        self.assertLess((self.clock_ns - self.stamp_ns(accepted.header.stamp)) / 1e9, 0.25)

    def test_replay_preserves_payload_and_receipt_watchdog_expires(self):
        self.enable()
        accepted = self.acquire('original_payload')
        replay_count = [0, 0]

        def replay():
            older = sum(replay_count) % 2
            msg = copy.deepcopy(accepted)
            if older:
                stamp = self.stamp_ns(msg.header.stamp) - 1_000_000
                msg.header.stamp.sec, msg.header.stamp.nanosec = divmod(stamp, 1_000_000_000)
            msg.header.frame_id = 'older_payload' if older else 'duplicate_payload'
            msg.lateral, msg.vertical = -9.0, 9.0
            replay_count[older] += 1
            self.publish(msg)

        start = len(self.messages)
        timer = self.timer(replay, 0.02)
        # Sample the still-tracking interval before receipt expiry, then the loss.
        self.pump(0.12)
        tracking = [msg for _, msg in self.messages[start:] if msg.tracking_allowed]
        self.assertTrue(tracking)
        for msg in tracking:
            self.assertEqual(msg.source_header, accepted.header)
            self.assertEqual((msg.lateral, msg.vertical), (5.0, -3.0))
        neutral = self.neutral(start)
        landing = self.landing(neutral)
        timer.cancel()
        self.assertTrue(all(count >= 10 for count in replay_count))
        self.assert_neutral(neutral)
        for _, msg in self.messages[start:]:
            self.assertEqual(msg.source_header, accepted.header)
            self.assertEqual(self.stamp_ns(msg.header.stamp), self.clock_ns)
        self.assertTrue(self.messages[landing][1].request_land)

    def test_disable_takeover_reenable_reject_cached_observations(self):
        for ordinary in (True, False):
            with self.subTest(ordinary_disable=ordinary):
                self.reset()
                self.enable()
                accepted = self.acquire('before_reset')
                self.reset(ordinary=ordinary)
                timer = self.timer(lambda: self.publish(accepted), 0.02)
                self.pump(0.12)
                self.assert_neutral(disabled=True, landed=False)
                self.enable()
                self.pump(0.15)
                self.assert_neutral(landed=False)
                self.assertTrue(all(self.stamp_ns(msg.source_header.stamp) == 0
                                    for _, msg in self.messages))
                timer.cancel()
                self.acquire('genuinely_new')

    def test_overdue_deadline_is_checked_before_late_fifth_observation(self, proc_info, manager):
        self.enable()
        for _ in range(4):
            self.observe('partial_before_pause')
        self.assert_neutral(landed=False)
        # Suspend only this isolated child so no timer can latch landing in advance.
        # A frozen source clock keeps the queued fifth observation source-fresh.
        proc_info.assertWaitForStartup(manager, timeout=5)
        pid = proc_info[manager].pid
        os.kill(pid, signal.SIGSTOP)
        try:
            self.pump(1.65)
            start = len(self.messages)
            late = self.make_input('late_fifth')
            self.publish(late)
            self.pump(0.05)
        finally:
            os.kill(pid, signal.SIGCONT)
        landing = self.landing(start)
        self.wait(lambda: any(msg.source_header == late.header
                              for _, msg in self.messages[start:]))
        self.assert_neutral(start, landed=True)
        self.assertTrue(self.messages[landing][1].request_land)


@launch_testing.post_shutdown_test()
class TestShutdown(unittest.TestCase):
    def test_clean_exit(self, proc_info, manager):
        launch_testing.asserts.assertExitCodes(proc_info, process=manager)
