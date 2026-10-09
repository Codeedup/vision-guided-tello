"""ROS adapter for the software-only image-plane plant."""

import csv
import json
import math
from pathlib import Path
import time

from drone_interfaces.msg import ApprovedCommand, HandTarget
from rcl_interfaces.msg import ParameterDescriptor
import rclpy
from rclpy.clock import Clock
from rclpy.clock_type import ClockType
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import String
from std_srvs.srv import Trigger

from mock_tello.model import Config, Decision, ImagePlane, SCENARIOS, scenario_input


def stamp_ns(stamp):
    if stamp.sec < 0 or not 0 <= stamp.nanosec < 1_000_000_000:
        return 0
    return stamp.sec * 1_000_000_000 + stamp.nanosec


class MockTello(Node):
    def __init__(self):
        super().__init__('mock_tello')
        if self.get_parameter('use_sim_time').value:
            raise ValueError('This wall-time mock requires use_sim_time=False')

        def parameter(name, default):
            return self.declare_parameter(
                name, default, ParameterDescriptor(read_only=True)).value

        config = Config(**{name: parameter(name, default)
                           for name, default in vars(Config()).items()})
        self.scenario = parameter('scenario', 'static')
        if self.scenario not in SCENARIOS:
            raise ValueError('scenario must be one of ' + ', '.join(SCENARIOS))
        self.fault_start = parameter('fault_start', 3.0)
        self.fault_duration = parameter(
            'fault_duration', 2.5 if self.scenario == 'loss' else 0.6)
        sensor_hz = parameter('sensor_hz', 20.0)
        step_seconds = parameter('step_seconds', 0.01)
        state_hz = parameter('state_hz', 10.0)
        for name, value in (('fault_start', self.fault_start),
                            ('fault_duration', self.fault_duration),
                            ('sensor_hz', sensor_hz), ('step_seconds', step_seconds),
                            ('state_hz', state_hz)):
            if not math.isfinite(value) or value < 0:
                raise ValueError(name + ' must be finite and nonnegative')
        if not 1 <= sensor_hz <= 120 or not 1 <= state_hz <= 100:
            raise ValueError('sensor_hz must be in [1,120]; state_hz in [1,100]')
        if not 0.001 <= step_seconds <= 0.05:
            raise ValueError('step_seconds must be in [0.001,0.05]')
        self.world = ImagePlane(config)
        self.run_index = 0
        self.started_at = self.last_step = time.monotonic()
        self.last_logged = self.started_at
        self.publisher = self.create_publisher(HandTarget, 'hand_target', 1)
        self.state_publisher = self.create_publisher(String, '~/state', 1)
        self.subscription = self.create_subscription(
            ApprovedCommand, 'approved_command', self.on_decision, 1)
        self.reset_service = self.create_service(Trigger, '~/reset', self.on_reset)
        self.csv_file = None
        log_path = parameter('log_path', '')
        if log_path:
            # Exclusive creation protects earlier evidence from accidental overwrite.
            self.csv_file = Path(log_path).expanduser().open('x', newline='')
            self.csv_writer = csv.DictWriter(
                self.csv_file, fieldnames=['run_index', *self.world.state(0).keys()])
            self.csv_writer.writeheader()
            self.csv_file.flush()
        steady_clock = Clock(clock_type=ClockType.STEADY_TIME)
        self.step_timer = self.create_timer(step_seconds, self.on_step, clock=steady_clock)
        self.sensor_timer = self.create_timer(1 / sensor_hz, self.on_capture, clock=steady_clock)
        self.state_timer = self.create_timer(1 / state_hz, self.on_state, clock=steady_clock)
        self.get_logger().info(
            'Software-only mock prepositioned in hover; explicit supervisor enable '
            'required. No takeoff, SDK, UDP, or aircraft connection.')

    def on_decision(self, message):
        decision = Decision(
            stamp_ns(message.header.stamp), stamp_ns(message.source_header.stamp),
            message.autonomy_enabled, message.tracking_allowed, message.request_land,
            message.lateral, message.vertical)
        self.world.gate.accept(
            decision, self.get_clock().now().nanoseconds, time.monotonic())

    def scenario_values(self, steady_now):
        return scenario_input(self.scenario, steady_now - self.started_at,
                              self.fault_start, self.fault_duration)

    def on_step(self):
        steady_now = time.monotonic()
        dt = steady_now - self.last_step
        self.last_step = steady_now
        if dt <= 0:
            return
        if dt > 0.1:
            # Do not extrapolate a held active command across an executor stall.
            self.world.gate.invalidate('STEP_OVERRUN')
        vx, vy, _ = self.scenario_values(steady_now)
        self.world.advance(dt, self.get_clock().now().nanoseconds, steady_now, vx, vy)
        for observation in self.world.deliver(steady_now):
            message = HandTarget()
            message.header.stamp.sec = observation.stamp_ns // 1_000_000_000
            message.header.stamp.nanosec = observation.stamp_ns % 1_000_000_000
            message.header.frame_id = 'mock_camera'
            message.detected = observation.detected
            message.tracked_hands = observation.tracked_hands
            message.error_x = observation.error_x
            message.error_y = observation.error_y
            self.publisher.publish(message)

    def on_capture(self):
        steady_now = time.monotonic()
        _, _, mode = self.scenario_values(steady_now)
        self.world.capture(self.get_clock().now().nanoseconds, steady_now, mode)

    def on_state(self):
        steady_now = time.monotonic()
        state = dict(run_index=self.run_index,
                     **self.world.state(steady_now - self.started_at))
        self.state_publisher.publish(String(data=json.dumps(state, allow_nan=False)))
        if self.csv_file:
            self.csv_writer.writerow(state)
            self.csv_file.flush()
        if steady_now - self.last_logged >= 1:
            self.get_logger().info(json.dumps(state, allow_nan=False))
            self.last_logged = steady_now

    def on_reset(self, request, response):
        del request
        if not self.world.gate.reset_allowed(
                self.get_clock().now().nanoseconds, time.monotonic()):
            response.success = False
            response.message = 'Reset requires a fresh disabled supervisor decision; take over first.'
            return response
        # Keep replay protection across reset; clear pending old observations.
        high_water = self.world.gate.high_water_ns
        self.world = ImagePlane(self.world.config)
        self.world.gate.high_water_ns = high_water
        self.run_index += 1
        self.started_at = self.last_step = time.monotonic()
        response.success = True
        response.message = 'Mock reset to prepositioned hover. Explicit supervisor re-enable required.'
        return response

    def destroy_node(self):
        if self.csv_file:
            self.csv_file.close()
        return super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = MockTello()
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
