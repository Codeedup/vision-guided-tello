"""ROS adapter: autonomous motion subscribes only to ApprovedCommand."""

from contextlib import nullcontext
import json
import signal
import time

from drone_interfaces.msg import ApprovedCommand
from rcl_interfaces.msg import ParameterDescriptor
import rclpy
from rclpy.clock import Clock
from rclpy.clock_type import ClockType
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions
from std_msgs.msg import String
from std_srvs.srv import Trigger
from tello_bridge.arbiter import Arbiter
from tello_bridge.policy import Command, Telemetry
from tello_bridge.timing import source_hash, TimingTrace
from tello_bridge.transport import FakeTransport, OwnerLock


def stamp_ns(stamp):
    if stamp.sec < 0 or not 0 <= stamp.nanosec < 1_000_000_000:
        return None
    value = stamp.sec * 1_000_000_000 + stamp.nanosec
    return value if value > 0 else None


class Bridge(Node):
    def __init__(self):
        super().__init__('tello_bridge')
        self.arbiter = None
        self.trace = None
        self.heartbeat_sequence = 0
        self.owner = None
        self.fake_hover = False
        self.upstream_disabled = False
        if self.get_parameter('use_sim_time').value:
            raise ValueError('Bridge requires use_sim_time=false')
        mode = self.declare_parameter(
            'transport', 'fake', ParameterDescriptor(read_only=True)).value
        if mode != 'fake':
            raise ValueError('Hardware ROS actuation is uncommissioned: use the explicit '
                             'non-actuating diagnostic for network evidence first')
        trace_path = self.declare_parameter(
            'timing_trace_path', '', ParameterDescriptor(read_only=True)).value
        trace_label = self.declare_parameter(
            'timing_trace_label', '', ParameterDescriptor(read_only=True)).value
        trace_capacity = self.declare_parameter(
            'timing_trace_capacity', 20000, ParameterDescriptor(read_only=True)).value
        self.owner = OwnerLock('fake:' + self.get_namespace())
        self.owner.acquire()
        try:
            if trace_path:
                self.trace = TimingTrace(
                    trace_path, fake=True, capacity=trace_capacity,
                    metadata={'role': 'bridge', 'namespace': self.get_namespace(),
                              'label': trace_label, 'node_sha256': source_hash(__file__),
                              'operator_timeout_ns': 300000000,
                              'freshness_ns': 250000000, 'tick_period_ns': 20000000})
            self.arbiter = Arbiter(FakeTransport())
            self.arbiter.trace = self.trace
            self.arbiter.start()
            self.publisher = self.create_publisher(String, '~/state', 1)
            self.subscription = self.create_subscription(
                ApprovedCommand, 'approved_command', self.on_command, 1)
            self.operator_services = []
            for name in ('arm', 'heartbeat', 'disable', 'manual_takeover', 'land',
                         'status', 'prepare_fake_hover'):
                self.operator_services.append(self.create_service(
                    Trigger, '~/' + name,
                    lambda req, res, action=name: self.operator(action, res)))
            steady = Clock(clock_type=ClockType.STEADY_TIME)
            self.timer = self.create_timer(0.02, self.tick, clock=steady)
            self.state_timer = self.create_timer(0.1, self.publish_status, clock=steady)
            self.get_logger().info('FAKE transport only; authority disabled, lifecycle UNKNOWN. '
                                   'No aircraft connection or automatic takeoff.')
        except BaseException:
            self.owner.close()
            if self.arbiter:
                self.arbiter.transport.close()
            if self.trace:
                self.trace.finish('initialization_failed')
            raise

    def on_command(self, msg):
        with self.measure('approved_callback') as event:
            event.update(decision_ns=stamp_ns(msg.header.stamp),
                         source_ns=stamp_ns(msg.source_header.stamp),
                         flags=[msg.autonomy_enabled, msg.tracking_allowed, msg.request_land])
            event['accepted'] = self.accept_command(msg)

    def measure(self, kind, **fields):
        if self.trace:
            return self.trace.measure(kind, self.arbiter.authority, **fields)
        return nullcontext({})

    def accept_command(self, msg):
        if self.get_parameter('use_sim_time').value:
            self.arbiter.authority.request_land('SIM_TIME_CHANGED')
            return False
        command = Command(stamp_ns(msg.header.stamp), stamp_ns(msg.source_header.stamp),
                          msg.autonomy_enabled, msg.tracking_allowed, msg.request_land,
                          msg.lateral, msg.vertical)
        accepted = self.arbiter.authority.accept(
            command, self.get_clock().now().nanoseconds, time.monotonic_ns())
        if accepted:
            self.upstream_disabled = not command.enabled
        return accepted

    def operator(self, action, response):
        if action == 'heartbeat':
            self.heartbeat_sequence += 1
        with self.measure('operator_callback', action=action,
                          heartbeat_sequence=self.heartbeat_sequence) as event:
            result = self.handle_operator(action, response)
            event['accepted'] = result.success
            return result

    def handle_operator(self, action, response):
        mono_ns, ros_ns = time.monotonic_ns(), self.get_clock().now().nanoseconds
        guard = self.arbiter.authority
        success = True
        if action == 'arm':
            success = guard.arm(ros_ns, mono_ns)
        elif action == 'heartbeat':
            success = guard.heartbeat(mono_ns)
        elif action in ('disable', 'manual_takeover'):
            self.arbiter.takeover(mono_ns)
        elif action == 'land':
            guard.request_land()
            self.arbiter.tick(ros_ns, mono_ns)
        elif action == 'prepare_fake_hover':
            success = self.upstream_disabled and not guard.armed
            if success:
                # Explicit fake lifecycle reset, never inferred from supervisor takeover.
                guard.landing_latched = False
                guard.land_attempts = 0
                guard.last_land_ns = None
                guard.flight_state = 'HOVER_CONFIRMED'
                self.fake_hover = True
                guard.telemetry = Telemetry(mono_ns, 80.0, 70.0)
        response.success = success
        response.message = json.dumps(self.status(), allow_nan=False)
        return response

    def tick(self):
        with self.measure('policy_tick'):
            self.policy_tick()

    def policy_tick(self):
        mono_ns = time.monotonic_ns()
        if self.fake_hover:
            self.arbiter.authority.telemetry = Telemetry(mono_ns, 80.0, 70.0)
        self.arbiter.tick(self.get_clock().now().nanoseconds, mono_ns)

    def status(self):
        transport = self.arbiter.transport
        return dict(fake=True, **self.arbiter.authority.status(),
                    upstream_disabled=self.upstream_disabled,
                    last_rc=self.arbiter.last_rc,
                    last_transport_event=transport.events[-1] if transport.events else None,
                    transport_events_retained=len(transport.events),
                    transport_events_omitted=transport.events_omitted,
                    simulated_telemetry=self.fake_hover)

    def publish_status(self):
        with self.measure('status_callback'):
            self.publisher.publish(String(data=json.dumps(self.status(), allow_nan=False)))

    def destroy_node(self):
        try:
            if self.arbiter:
                self.arbiter.shutdown(time.monotonic_ns())
            if self.owner:
                self.owner.close()
            return super().destroy_node()
        finally:
            if self.trace:
                self.trace.finish()


def main(args=None):
    rclpy.init(args=args, signal_handler_options=SignalHandlerOptions.NO)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    node = None
    try:
        node = Bridge()
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if node:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
