"""Exercise HandTarget -> real controller -> real supervisor -> ApprovedCommand."""

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
from std_srvs.srv import SetBool

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safety_test_support import SafetyProbe  # noqa: E402,I100


def generate_test_description():
    nodes = {
        'controller': launch_ros.actions.Node(
            package='tracking_controller', executable='tracking_controller_node',
            namespace='controller_supervisor', parameters=[{'use_sim_time': False}],
            output='screen',
        ),
        'manager': launch_ros.actions.Node(
            package='mission_manager', executable='mission_manager_node',
            namespace='controller_supervisor', parameters=[{'use_sim_time': False}],
            output='screen',
        ),
    }
    return launch.LaunchDescription([
        *nodes.values(), launch_testing.actions.ReadyToTest(),
    ]), nodes


class TestControllerSupervisor(SafetyProbe, unittest.TestCase):
    namespace = 'controller_supervisor'
    combined = True

    def approved_for(self, target):
        self.wait(lambda: any(
            msg.tracking_allowed and msg.source_header == target.header
            for _, msg in self.messages
        ))
        return next(msg for _, msg in self.messages
                    if msg.tracking_allowed and msg.source_header == target.header)

    def test_disabled_start_acquisition_signs_headers_and_dead_zone(self):
        stream = self.stream('disabled_input')
        self.pump(0.35)
        self.stop_valid_stream(stream)
        self.assertGreaterEqual(sum(msg.target_valid for _, msg in self.candidates), 5)
        self.assert_neutral(disabled=True, landed=False)
        self.enable()
        target = self.acquire('original_camera_frame')
        approved = self.approved_for(target)
        self.assertAlmostEqual(approved.lateral, 10.0, places=5)
        self.assertAlmostEqual(approved.vertical, 6.0, places=5)
        candidate = next(msg for _, msg in self.candidates if msg.header == target.header)
        self.assertTrue(candidate.target_valid)
        self.assertEqual(candidate.header, target.header)
        self.assertEqual(approved.source_header, candidate.header)
        self.assertGreaterEqual(self.stamp_ns(approved.header.stamp),
                                self.stamp_ns(target.header.stamp))
        for x, y, lateral, vertical in (
            (-0.9, 0.9, -10.0, -10.0),
            (0.0, 0.0, 0.0, 0.0),
            (0.05, -0.05, 0.0, 0.0),
            (0.05, 0.2, 0.0, -4.0),
        ):
            with self.subTest(error=(x, y)):
                target = self.observe('signs_and_dead_zone', error_x=x, error_y=y)
                approved = self.approved_for(target)
                self.assertAlmostEqual(approved.lateral, lateral, places=5)
                self.assertAlmostEqual(approved.vertical, vertical, places=5)

    def test_invalid_hand_targets_invalidate_the_real_chain(self):
        cases = (
            ('no_hand', {'detected': False}, 'fresh'),
            ('multiple_hands', {'tracked_hands': 2}, 'fresh'),
            ('nan_error', {'error_x': float('nan')}, 'fresh'),
            ('infinite_error', {'error_y': float('inf')}, 'fresh'),
            ('outside_x', {'error_x': 1.1}, 'fresh'),
            ('outside_y', {'error_y': -1.1}, 'fresh'),
            ('stale', {}, 'stale'),
            ('future', {}, 'future'),
            ('missing', {}, 'missing'),
            ('malformed', {}, 'malformed'),
            ('negative', {}, 'negative'),
        )
        for frame, changes, stamp_kind in cases:
            with self.subTest(invalid=frame):
                self.reset()
                self.enable()
                accepted = self.acquire('good_' + frame)
                start = len(self.messages)
                candidate_start = len(self.candidates)

                def publish_bad():
                    bad = self.make_input(frame, **changes)
                    if stamp_kind == 'stale':
                        bad.header.stamp.sec -= 1
                    elif stamp_kind == 'future':
                        bad.header.stamp.sec += 1
                    elif stamp_kind == 'missing':
                        bad.header.stamp.sec = bad.header.stamp.nanosec = 0
                    elif stamp_kind == 'malformed':
                        bad.header.stamp.nanosec = 1_000_000_000
                    elif stamp_kind == 'negative':
                        bad.header.stamp.sec = -1
                    self.publish(bad)

                timer = self.timer(publish_bad, 0.02)
                self.wait(lambda: any(
                    msg.header.frame_id == frame
                    for _, msg in self.candidates[candidate_start:]
                ))
                neutral = self.neutral(start)
                self.pump(0.15)
                timer.cancel()
                invalid_candidates = [msg for _, msg in self.candidates[candidate_start:]
                                      if msg.header.frame_id == frame]
                self.assertGreaterEqual(len(invalid_candidates), 5)
                for candidate in invalid_candidates:
                    self.assertFalse(candidate.target_valid)
                    self.assertEqual((candidate.lateral, candidate.vertical), (0.0, 0.0))
                self.assert_neutral(neutral, landed=False)
                for _, msg in self.messages[start:]:
                    self.assertEqual(msg.source_header, accepted.header)

    def test_silence_controller_watchdog_and_latched_landing(self):
        self.enable()
        target = self.acquire('last_visual_observation')
        start = len(self.messages)
        candidate_start = len(self.candidates)
        neutral = self.neutral(start)
        self.wait(lambda: any(
            not msg.target_valid and msg.header == target.header
            for _, msg in self.candidates[candidate_start:]
        ))
        expired = [msg for _, msg in self.candidates[candidate_start:]
                   if not msg.target_valid and msg.header == target.header]
        self.assertEqual(len(expired), 1)
        self.assertEqual((expired[0].lateral, expired[0].vertical), (0.0, 0.0))
        landing = self.landing(neutral)
        self.assert_neutral(neutral)
        for _, msg in self.messages[start:]:
            self.assertEqual(msg.source_header, target.header)
        self.assertGreater(self.stamp_ns(self.messages[landing][1].header.stamp),
                           self.stamp_ns(target.header.stamp))
        stream = self.stream('fresh_after_landing')
        self.service(self.autonomy, SetBool.Request(data=False), success=False)
        self.service(self.autonomy, SetBool.Request(data=True), success=False)
        self.pump(0.35)
        stream.cancel()
        self.assert_neutral(landing, landed=True)
        self.assertTrue(any(msg.target_valid and msg.header.frame_id == 'fresh_after_landing'
                            for _, msg in self.candidates))

    def test_takeover_with_live_hand_stream_requires_new_five_observations(self):
        self.enable()
        self.acquire()
        stream = self.stream('live_during_takeover')
        self.reset()
        candidate_start = len(self.candidates)
        self.pump(0.35)
        self.assert_neutral(disabled=True, landed=False)
        self.assertGreaterEqual(sum(msg.target_valid
                                    for _, msg in self.candidates[candidate_start:]), 5)
        self.stop_valid_stream(stream)
        self.enable()
        self.acquire('new_after_explicit_enable')

    def test_supervisor_expires_input_when_controller_process_is_suspended(
        self, proc_info, controller,
    ):
        self.enable()
        self.acquire('before_controller_stall')
        proc_info.assertWaitForStartup(controller, timeout=5)
        pid = proc_info[controller].pid
        os.kill(pid, signal.SIGSTOP)
        try:
            start = len(self.messages)
            candidate_start = len(self.candidates)
            neutral = self.neutral(start)
            landing = self.landing(neutral)
            self.assert_neutral(neutral)
            self.assertEqual(self.candidates[candidate_start:], [])
            self.assertTrue(self.messages[landing][1].request_land)
        finally:
            os.kill(pid, signal.SIGCONT)


@launch_testing.post_shutdown_test()
class TestShutdown(unittest.TestCase):
    def test_both_nodes_exit_cleanly(self, proc_info, controller, manager):
        for process in (controller, manager):
            launch_testing.asserts.assertExitCodes(proc_info, process=process)
