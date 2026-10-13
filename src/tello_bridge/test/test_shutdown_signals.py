"""Production bridge signals must revoke FAKE authority and finish its trace."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from drone_interfaces.msg import ApprovedCommand
import pytest
import rclpy
from std_srvs.srv import Trigger


@pytest.mark.parametrize('stop_signal', [signal.SIGINT, signal.SIGTERM])
def test_active_bridge_shutdown_signals_write_complete_trace(tmp_path, stop_signal):
    namespace = '/shutdown_{}_{}'.format(os.getpid(), int(stop_signal))
    path = tmp_path / 'bridge.json'
    process = subprocess.Popen([
        sys.executable, '-c', 'from tello_bridge.node import main; main()',
        '--ros-args', '-r', '__ns:=' + namespace, '--log-level', 'warn',
        '-p', 'timing_trace_path:=' + str(path), '-p', 'timing_trace_capacity:=32'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    rclpy.init()
    node = rclpy.create_node('signal_probe', namespace=namespace)
    publisher = node.create_publisher(ApprovedCommand, 'approved_command', 1)
    clients = {name: node.create_client(Trigger, 'tello_bridge/' + name)
               for name in ('prepare_fake_hover', 'arm', 'heartbeat', 'status')}

    def publish(tracking=False):
        msg = ApprovedCommand()
        msg.header.stamp = node.get_clock().now().to_msg()
        msg.source_header.stamp = msg.header.stamp
        msg.autonomy_enabled = msg.tracking_allowed = tracking
        msg.lateral = 1.0 if tracking else 0.0
        publisher.publish(msg)

    def call(name):
        future = clients[name].call_async(Trigger.Request())
        deadline = time.monotonic() + 2
        while not future.done() and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.005)
        assert future.done(), name + ' timed out'
        result = future.result()
        assert result.success, result.message
        return json.loads(result.message)

    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            publish()
            rclpy.spin_once(node, timeout_sec=0.01)
            assert process.poll() is None, 'Bridge exited before signal'
            if publisher.get_subscription_count() and all(
                    client.service_is_ready() for client in clients.values()):
                break
        assert all(client.service_is_ready() for client in clients.values())
        # Exercise ring eviction before shutdown as well as file reservation.
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            publish()
            rclpy.spin_once(node, timeout_sec=0.01)
        assert path.exists() and path.stat().st_size == 0
        assert call('prepare_fake_hover')['flight_state'] == 'HOVER_CONFIRMED'
        assert call('arm')['armed']
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            publish(tracking=True)
            call('heartbeat')
            state = call('status')
            if state['reason'] == 'TRACKING':
                break
            rclpy.spin_once(node, timeout_sec=0.005)
        assert state['armed'] and state['reason'] == 'TRACKING'
        process.send_signal(stop_signal)
        stdout, stderr = process.communicate(timeout=3)
        assert process.returncode == 0, stdout + stderr
        report = json.loads(path.read_text())
        assert report['completion'] == 'shutdown'
        assert report['events_omitted'] > 0
        shutdown = report['events'][-1]
        assert shutdown['kind'] == 'shutdown'
        assert shutdown['before']['armed']
        assert not shutdown['after']['armed']
        assert shutdown['after']['landing_latched']
        assert shutdown['after']['reason'] == 'SHUTDOWN'
        assert shutdown['after']['operator_receipt_ns'] is None
        assert report['first_fault']['kind'] == 'shutdown'
        operations = [event['operation'] for event in report['events']
                      if event['kind'] == 'transport_operation']
        assert operations[-2:] == ['send_rc', 'send_land']
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=3)
        node.destroy_node()
        rclpy.shutdown()


@pytest.mark.parametrize('stop_signal', [signal.SIGINT, signal.SIGTERM])
def test_receiver_repeated_signals_do_not_interrupt_callback_or_cleanup(stop_signal):
    root = Path(__file__).resolve().parents[3]
    code = """
import os
import signal
import time
import rclpy
from ros_hand_receiver import WebcamReceiver, main
original = WebcamReceiver.receive_packets
def receive(self):
    original(self)
    for _ in range(2):
        os.kill(os.getpid(), int(os.environ['RECEIVER_STOP_SIGNAL']))
        time.sleep(0.05)
        assert rclpy.ok(), 'Receiver context stopped during callback'
        original(self)
    print('RECEIVER_CALLBACK_COMPLETED', flush=True)
WebcamReceiver.receive_packets = receive
main()
assert not rclpy.ok()
print('RECEIVER_SHUTDOWN_COMPLETED', flush=True)
"""
    env = dict(os.environ, RECEIVER_STOP_SIGNAL=str(int(stop_signal)))
    env['PYTHONPATH'] = os.pathsep.join(
        [str(root / 'tools/webcam_perception'), env.get('PYTHONPATH', '')])
    result = subprocess.run([sys.executable, '-c', code, '--bind', '127.0.0.1', '--port', '0'],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'RECEIVER_CALLBACK_COMPLETED' in result.stdout
    assert 'RECEIVER_SHUTDOWN_COMPLETED' in result.stdout
