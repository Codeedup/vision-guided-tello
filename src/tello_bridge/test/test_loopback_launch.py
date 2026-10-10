"""Actual loopback UDP, production ROS policies, fake consumer and passive monitor."""

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from drone_interfaces.msg import ApprovedCommand, CandidateCommand, HandTarget
import launch
import launch_ros.actions
import launch_testing
import launch_testing.actions
import launch_testing.asserts
import pytest
import rclpy
from rclpy.executors import SingleThreadedExecutor
from std_msgs.msg import String
from std_srvs.srv import SetBool, Trigger

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / 'tools/webcam_perception'
sys.path.insert(0, str(TOOLS))
from ros_hand_receiver import WebcamReceiver  # noqa: E402, I100
from synthetic_observations import observation  # noqa: E402, I100


@pytest.mark.launch_test
def generate_test_description():
    processes = {name: launch_ros.actions.Node(
        package=package, executable=executable, namespace='loopback_boundary',
        parameters=[{'use_sim_time': False}],
        arguments=['--ros-args', '--log-level', 'warn'], output='screen')
        for name, package, executable in (
            ('controller', 'tracking_controller', 'tracking_controller_node'),
            ('manager', 'mission_manager', 'mission_manager_node'),
            ('bridge', 'tello_bridge', 'tello_bridge_node'))}
    return launch.LaunchDescription([
        *processes.values(), launch_testing.actions.ReadyToTest(),
    ]), {'processes': processes}


class TestLoopback(unittest.TestCase):
    def setUp(self):
        rclpy.init()
        self.addCleanup(rclpy.shutdown)
        self.node = rclpy.create_node('loopback_probe', namespace='loopback_boundary')
        self.addCleanup(self.node.destroy_node)
        self.receiver = WebcamReceiver('127.0.0.1', 0, '/loopback_boundary')
        self.addCleanup(self.receiver.destroy_node)
        self.addCleanup(self.receiver.receiver.close)
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.node)
        self.executor.add_node(self.receiver)
        self.addCleanup(self.executor.shutdown)
        self.sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(self.sender.close)
        self.endpoint = self.receiver.receiver.getsockname()
        self.targets, self.candidates, self.approved, self.states = [], [], [], []
        self.subs = [self.node.create_subscription(kind, name, callback, 100)
                     for kind, name, callback in (
                         (HandTarget, 'hand_target', self.targets.append),
                         (CandidateCommand, 'candidate_command', self.candidates.append),
                         (ApprovedCommand, 'approved_command', self.approved.append),
                         (String, 'tello_bridge/state',
                          lambda msg: self.states.append(
                              (time.monotonic(), json.loads(msg.data)))))]
        self.clients = {}
        self.wait(lambda: all(sub.get_publisher_count() == 1 for sub in self.subs), 10)
        self.call('tello_bridge/manual_takeover')
        self.call('mission_manager/manual_takeover')
        self.wait(lambda: self.states and self.states[-1][1]['upstream_disabled'])
        self.call('tello_bridge/prepare_fake_hover')
        # Graph discovery is not an end-to-end delivery barrier. Warm up while
        # autonomy is disabled before asserting individual UDP/DDS observations.
        deadline = time.monotonic() + 5
        while not self.candidates and time.monotonic() < deadline:
            self.packet()
            self.pump(0.05)
        self.assertTrue(self.candidates, 'No end-to-end startup delivery')
        self.pump(0.02)
        self.targets.clear()
        self.candidates.clear()
        self.approved.clear()

    @staticmethod
    def stamp(stamp):
        return stamp.sec * 1_000_000_000 + stamp.nanosec

    def wait(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.executor.spin_once(timeout_sec=0.005)
        self.assertTrue(predicate(), 'Timed out waiting for loopback pipeline')

    def pump(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.executor.spin_once(timeout_sec=0.005)

    def client(self, path):
        if path not in self.clients:
            kind = SetBool if 'set_autonomy' in path else Trigger
            self.clients[path] = self.node.create_client(kind, path)
        return self.clients[path]

    def call(self, path, request=None, success=True):
        client = self.client(path)
        self.wait(client.service_is_ready)
        future = client.call_async(request or Trigger.Request())
        self.wait(future.done)
        response = future.result()
        self.assertEqual(response.success, success, response.message)
        return response

    def packet(self, data=None, **changes):
        data = observation(time.time_ns()) if data is None else data
        data.update(changes)
        self.sender.sendto(json.dumps(data, allow_nan=False).encode(), self.endpoint)
        return data['capture_time_ns']

    def send_and_wait(self, **changes):
        stamp = self.packet(**changes)
        self.wait(lambda: any(self.stamp(msg.header.stamp) == stamp for msg in self.candidates))
        return next(msg for msg in self.candidates if self.stamp(msg.header.stamp) == stamp)

    def enable(self):
        self.call('tello_bridge/arm')
        self.call('mission_manager/set_autonomy', SetBool.Request(data=True))
        client = self.client('tello_bridge/heartbeat')
        timer = self.node.create_timer(0.08, lambda: client.call_async(Trigger.Request()))
        self.addCleanup(self.node.destroy_timer, timer)
        self.lease = timer

    def acquire(self):
        self.enable()
        stamps = []
        for index in range(5):
            message = self.send_and_wait()
            stamps.append(self.stamp(message.header.stamp))
            if index < 4:
                self.assertFalse(any(msg.tracking_allowed for msg in self.approved))
        self.wait(lambda: any(msg.tracking_allowed for msg in self.approved))
        self.assertEqual(len(set(stamps)), 5)
        return stamps[-1]

    def start_sender(self):
        timer = self.node.create_timer(0.04, self.packet)
        self.addCleanup(self.node.destroy_timer, timer)
        return timer

    def test_bounded_batches_malformed_newest_replay_and_final_recheck(self):
        self.receiver.timer.cancel()
        base = time.time_ns() - 5_000_000
        self.sender.sendto(b'malformed', self.endpoint)
        for offset in (0, 2, 1):
            self.packet(observation(base + offset * 1_000_000))
        self.receiver.receive_packets()
        self.wait(lambda: len(self.targets) == 1)
        self.assertEqual(self.stamp(self.targets[0].header.stamp), base + 2_000_000)
        count = len(self.targets)
        self.packet(observation(base))
        self.packet(observation(base + 2_000_000))
        self.packet(observation(time.time_ns() - 300_000_000))
        self.packet(observation(time.time_ns() + 1_000_000_000))
        self.receiver.receive_packets()
        self.pump(0.05)
        self.assertEqual(len(self.targets), count)
        # Actual kernel queue: >64 valid datagrams exercise bounded callback batches.
        for index in range(100):
            self.packet(observation(time.time_ns() - 1_000_000 + index))
        self.receiver.receive_packets()
        first = self.receiver.last_published_stamp
        self.receiver.receive_packets()
        self.assertGreaterEqual(self.receiver.last_published_stamp, first)
        self.receiver.timer.reset()

    def test_controller_signs_deadzones_saturation_and_invalid_zero(self):
        for x, y, expected in ((0.7, -0.7, (10, 10)), (-0.7, 0.7, (-10, -10)),
                               (0.05, -0.05, (0, 0)), (0.2, 0.1, (4, -2))):
            msg = self.send_and_wait(error_x=x, error_y=y)
            self.assertTrue(msg.target_valid)
            self.assertAlmostEqual(msg.lateral, expected[0], places=5)
            self.assertAlmostEqual(msg.vertical, expected[1], places=5)
            self.assertEqual(msg.header.frame_id, 'webcam_optical_frame')
        for mode in ('invalid', 'two_hands'):
            data = observation(time.time_ns(), mode)
            stamp = self.packet(data)
            self.wait(lambda: any(self.stamp(m.header.stamp) == stamp for m in self.candidates))
            msg = next(m for m in self.candidates if self.stamp(m.header.stamp) == stamp)
            self.assertFalse(msg.target_valid)
            self.assertEqual((msg.lateral, msg.vertical), (0, 0))

    def test_duplicate_frames_do_not_acquire(self):
        self.enable()
        source = observation(time.time_ns())
        for _ in range(10):
            self.packet(dict(source))
            self.pump(0.01)
        self.assertFalse(any(msg.tracking_allowed for msg in self.approved))
        for _ in range(4):
            self.send_and_wait()
        self.wait(lambda: any(msg.tracking_allowed for msg in self.approved))

    def test_monitor_callback_qos_metadata_missing_topics_and_ctrl_c_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            for interrupted in (False, True):
                path = Path(directory) / f'monitor-{interrupted}.json'
                log_path = path.with_suffix('.log')
                log_stream = log_path.open('w')
                process = subprocess.Popen([
                    sys.executable, str(TOOLS / 'measure_pipeline.py'),
                    '--namespace', '/loopback_boundary', '--duration', '4',
                    '--source', 'synthetic', '--run-kind', 'smoke', '--output', str(path)],
                    stdout=log_stream, stderr=subprocess.PIPE, text=True)
                stream = self.start_sender()
                try:
                    try:
                        self.wait(lambda: 'Observing' in log_path.read_text(), 5)
                        self.wait(
                            lambda: 'All pipeline topics observed.' in log_path.read_text(), 3)
                    except AssertionError:
                        print({'monitor_process': process.poll(),
                               'domain_environment': os.environ.get('ROS_DOMAIN_ID'),
                               'context_domain': self.node.context.get_domain_id(),
                               'nodes': self.node.get_node_names_and_namespaces()}, flush=True)
                        raise
                    self.pump(0.3 if interrupted else 4.2)
                    if interrupted:
                        process.send_signal(signal.SIGINT)
                    stdout, stderr = process.communicate(timeout=4)
                    self.assertTrue(path.exists(), stderr)
                    report = json.loads(path.read_text())
                    self.assertEqual(process.returncode, 0,
                                     str(report['missing_topics']) + stderr)
                    self.assertEqual(report['missing_topics'], [])
                    self.assertEqual(report['metadata']['source_classification'], 'synthetic')
                    self.assertEqual(report['interrupted'], interrupted)
                    self.assertTrue(report['message_records'])
                    approved = report['topics']['approved_command']
                    self.assertGreater(approved['observed_messages'],
                                       approved['advancing_source_stamps'])
                    self.assertEqual(report['nonneutral_messages_without_tracking'], 0)
                finally:
                    log_stream.close()
                    stream.cancel()
                    if process.poll() is None:
                        process.kill()
                        process.wait()
            missing = subprocess.run([
                sys.executable, str(TOOLS / 'measure_pipeline.py'), '--duration', '1',
                '--namespace', '/unused_monitor_namespace'], capture_output=True, timeout=5)
            self.assertEqual(missing.returncode, 2, missing.stderr)

    def test_operator_process_loss_latches_consumer_independently(self):
        self.acquire()
        stream = self.start_sender()
        self.wait(lambda: self.states[-1][1]['reason'] == 'TRACKING')
        self.lease.cancel()
        self.wait(lambda: self.states[-1][1]['landing_latched'], 1)
        self.assertEqual(self.states[-1][1]['reason'], 'OPERATOR_LEASE_EXPIRED')
        self.assertFalse(self.states[-1][1]['armed'])
        stream.cancel()

    def test_operator_cli_bounded_session_and_takeover(self):
        stream = self.start_sender()
        process = subprocess.Popen([
            sys.executable, str(ROOT / 'src/tello_bridge/scripts/tello_operator'),
            'enable', '--namespace', '/loopback_boundary', '--duration', '0.8'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.wait(lambda: process.poll() is not None, 12)
            stdout, stderr = process.communicate(timeout=1)
            self.assertEqual(process.returncode, 0, stdout + stderr)
            self.assertTrue(any(state['reason'] == 'TRACKING' for _, state in self.states))
            self.wait(lambda: self.states[-1][1]['upstream_disabled'])
            self.assertFalse(self.states[-1][1]['armed'])
        finally:
            stream.cancel()
            if process.poll() is None:
                process.kill()
                process.wait()

    def test_sender_silence_landing_latch_and_takeover(self):
        source = self.acquire()
        started = time.monotonic()
        self.wait(lambda: any(not m.target_valid and self.stamp(m.header.stamp) == source
                              for m in self.candidates), 1)
        neutral_at = time.monotonic()
        self.assertLess(neutral_at - started, 0.75)
        self.wait(lambda: any(m.request_land for m in self.approved), 2.5)
        landing_at = time.monotonic()
        self.assertLess(landing_at - started, 2.5)
        print(json.dumps({'synthetic_loopback_observer': True,
                          'neutral_after_last_sent_observation_s': neutral_at - started,
                          'land_after_last_sent_observation_s': landing_at - started,
                          'scheduler_margin_s': 0.5}), flush=True)
        self.call('mission_manager/set_autonomy', SetBool.Request(data=False), success=False)
        self.wait(lambda: self.states[-1][1]['landing_latched'])
        self.call('mission_manager/manual_takeover')
        self.wait(lambda: self.states[-1][1]['upstream_disabled'])
        self.assertTrue(self.states[-1][1]['landing_latched'])
        self.assertTrue(any(self.stamp(m.source_header.stamp) == source for m in self.approved))

    def test_supervisor_suspension_expires_consumer_authority(self, proc_info, processes):
        self.acquire()
        self.start_sender()
        self.wait(lambda: self.states[-1][1]['reason'] == 'TRACKING')
        process = processes['manager']
        proc_info.assertWaitForStartup(process, timeout=5)
        pid = proc_info[process].pid
        os.kill(pid, signal.SIGSTOP)
        try:
            self.wait(lambda: self.states[-1][1]['reason'] != 'TRACKING', 1)
            self.assertFalse(self.states[-1][1]['landing_latched'])
            self.wait(lambda: self.states[-1][1]['landing_latched'], 2.5)
            self.assertEqual(self.states[-1][1]['reason'], 'APPROVED_INPUT_LOSS')
        finally:
            os.kill(pid, signal.SIGCONT)

    def test_z_controller_process_exit_independent_supervisor_timeout(self, proc_info, processes):
        self.acquire()
        self.start_sender()
        process = processes['controller']
        proc_info.assertWaitForStartup(process, timeout=5)
        os.kill(proc_info[process].pid, signal.SIGINT)
        self.wait(lambda: any(msg.request_land for msg in self.approved), 2.5)
        self.assertTrue(self.targets)


@launch_testing.post_shutdown_test()
class TestShutdown(unittest.TestCase):
    def test_processes_exit_cleanly(self, proc_info, processes):
        for process in processes.values():
            launch_testing.asserts.assertExitCodes(proc_info, process=process)
