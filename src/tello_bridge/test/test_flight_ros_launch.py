"""Offline ROS decision and lease path through the real gateway policy."""

import socket
import time
import unittest

from drone_interfaces.msg import CandidateCommand
import launch
import launch_ros.actions
import launch_testing
import launch_testing.actions
import rclpy
from std_msgs.msg import Bool, Empty
from std_srvs.srv import SetBool

from tello_bridge.policy import Telemetry
from tello_bridge.transport import FakeTransport
from tello_bridge.windows_gateway import RealGateway

SESSION = 'b' * 32
FRAME = 'supervised_lateral_trial_' + SESSION
UDP = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
UDP.bind(('127.0.0.1', 0))
UDP.setblocking(False)
PORT = UDP.getsockname()[1]


def generate_test_description():
    manager = launch_ros.actions.Node(
        package='mission_manager', executable='mission_manager_node',
        namespace='flight_ros_test',
        parameters=[{'use_sim_time': False,
                     'required_candidate_frame_id': FRAME}], output='screen')
    forwarder = launch_ros.actions.Node(
        package='tello_bridge', executable='tello_approved_forwarder',
        namespace='flight_ros_test',
        arguments=['--namespace', '/flight_ros_test', '--windows-ip', '127.0.0.1',
                   '--port', str(PORT), '--flight-session-id', SESSION,
                   '--flight-source-frame-id', FRAME], output='screen')
    return launch.LaunchDescription([
        manager, forwarder, launch_testing.actions.ReadyToTest(),
    ]), {'manager': manager, 'forwarder': forwarder}


class FreshState:
    def start(self):
        pass

    def read(self, mono_ns):
        return Telemetry(mono_ns, 80.0, 70.0)

    def close(self):
        pass


class TestFlightRosPath(unittest.TestCase):
    def setUp(self):
        rclpy.init()
        self.addCleanup(rclpy.shutdown)
        self.node = rclpy.create_node('flight_ros_probe', namespace='flight_ros_test')
        self.addCleanup(self.node.destroy_node)
        self.candidate = self.node.create_publisher(CandidateCommand,
                                                    'candidate_command', 1)
        self.lease = self.node.create_publisher(Empty, 'flight_operator_lease', 1)
        self.releases = 0
        self.node.create_subscription(Bool, 'flight_pilot_release',
                                      self.on_release, 1)
        self.autonomy = self.node.create_client(SetBool, 'mission_manager/set_autonomy')
        self.gateway = RealGateway('flight', FakeTransport(), FreshState(),
                                   SESSION, FRAME)
        self.addCleanup(self.gateway.close)
        self.wait(lambda: self.autonomy.service_is_ready() and
                  self.candidate.get_subscription_count() == 1 and
                  self.lease.get_subscription_count() == 1, 5.0)

    def pump(self, seconds=0.02):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.lease.publish(Empty())
            rclpy.spin_once(self.node, timeout_sec=0.01)
            while True:
                try:
                    data, peer = UDP.recvfrom(1024)
                except BlockingIOError:
                    break
                if self.gateway.receive(data):
                    self.gateway.last_upstream_peer = peer
            self.gateway.tick()

    def on_release(self, msg):
        self.assertFalse(msg.data)
        self.releases += 1

    def wait(self, predicate, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.pump()
            if predicate():
                return
        self.fail('Timed out waiting for flight ROS path')

    def set_autonomy(self, enabled):
        future = self.autonomy.call_async(SetBool.Request(data=enabled))
        self.wait(future.done, 2.0)
        self.assertTrue(future.result().success, future.result().message)

    def publish_candidate(self, lateral, frame=FRAME):
        item = CandidateCommand()
        item.header.stamp = self.node.get_clock().now().to_msg()
        item.header.frame_id = frame
        item.target_valid = True
        item.lateral = float(lateral)
        item.vertical = 0.0
        self.candidate.publish(item)

    def attempts(self):
        return [event['attempt'] for event in self.gateway.arbiter.transport.events
                if 'attempt' in event]

    def test_only_approved_trial_source_reaches_limited_lateral_boundary(self):
        self.set_autonomy(False)
        self.wait(lambda: self.gateway.guard.disabled_seen, 2.0)
        self.assertTrue(self.gateway.confirm_hover())
        self.assertTrue(self.gateway.arm_flight())
        self.assertTrue(self.gateway.release_trial(UDP))
        self.wait(lambda: self.releases == 1, 2.0)
        self.assertFalse(self.gateway.release_trial(UDP))
        self.set_autonomy(True)
        for _ in range(8):
            self.publish_candidate(-2, frame='other_source')
            self.pump(0.03)
        self.assertFalse(any(command == 'rc -2 0 0 0'
                             for command in self.attempts()))
        for _ in range(12):
            self.publish_candidate(-2)
            self.pump(0.03)
        self.assertIn('rc -2 0 0 0', self.attempts())
        self.assertFalse(self.gateway.guard.landing_latched)
        self.set_autonomy(False)
        self.wait(lambda: not self.gateway.guard.armed, 2.0)
        self.assertEqual(self.attempts()[-1], 'rc 0 0 0 0')
        self.assertNotIn('land', self.attempts())


@launch_testing.post_shutdown_test()
class TestProcessesExit(unittest.TestCase):
    def test_processes_exit(self, proc_info, manager, forwarder):
        launch_testing.asserts.assertExitCodes(proc_info, process=manager)
        launch_testing.asserts.assertExitCodes(proc_info, process=forwarder)
