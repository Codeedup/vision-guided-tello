"""Software operator. An enabled session has a short continuously renewed lease."""

import argparse
import json
import os
import signal
import time

import rclpy
from rclpy.signals import SignalHandlerOptions
from std_srvs.srv import SetBool, Trigger


class ServiceRejected(RuntimeError):
    """An available service deliberately rejected a request."""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('status', 'enable', 'disable', 'takeover',
                                           'land', 'prepare-fake'))
    parser.add_argument('--namespace', default='/webcam_test')
    parser.add_argument('--duration', type=float, default=None,
                        help='Optional bounded enabled session, seconds')
    args = parser.parse_args(argv)
    if args.duration is not None and not 0 < args.duration <= 600:
        parser.error('--duration must be in (0,600]')
    rclpy.init(args=[], signal_handler_options=SignalHandlerOptions.NO)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    node = rclpy.create_node('tello_operator_' + str(os.getpid()), namespace=args.namespace)
    clients = {}

    def call(path, request=None, timeout=5.0):
        kind = SetBool if 'set_autonomy' in path else Trigger
        if path not in clients:
            clients[path] = node.create_client(kind, path)
        client = clients[path]
        discovery_deadline = time.monotonic() + timeout
        while not client.service_is_ready() and time.monotonic() < discovery_deadline:
            rclpy.spin_once(node, timeout_sec=0.02)
        if not client.service_is_ready():
            raise RuntimeError('Service unavailable: ' + client.service_name)
        future = client.call_async(request or Trigger.Request())
        deadline = time.monotonic() + timeout
        while not future.done() and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.01)
        if not future.done() or future.result() is None:
            raise RuntimeError('Service timed out: ' + path)
        if not future.result().success:
            raise ServiceRejected(future.result().message)
        return future.result().message

    def takeover():
        # Local bridge authority is revoked before awaiting the upstream service.
        failures = []
        for path in ('tello_bridge/manual_takeover', 'mission_manager/manual_takeover'):
            try:
                call(path)
            except RuntimeError as error:
                failures.append(str(error))
        if failures:
            raise RuntimeError('; '.join(failures))

    enabled = False
    try:
        if args.action == 'enable':
            # Complete heartbeat endpoint discovery before starting the short lease.
            heartbeat_path = 'tello_bridge/heartbeat'
            clients[heartbeat_path] = node.create_client(Trigger, heartbeat_path)
            discovery_deadline = time.monotonic() + 5.0
            while (not clients[heartbeat_path].service_is_ready()
                   and time.monotonic() < discovery_deadline):
                rclpy.spin_once(node, timeout_sec=0.02)
            if not clients[heartbeat_path].service_is_ready():
                raise RuntimeError('Heartbeat service unavailable')
            call('mission_manager/set_autonomy', SetBool.Request(data=False))
            # Wait until bridge independently observes the disabled heartbeat.
            time_limit = time.monotonic() + 1.0
            while True:
                try:
                    call('tello_bridge/arm')
                    break
                except RuntimeError:
                    if time.monotonic() >= time_limit:
                        raise
                    rclpy.spin_once(node, timeout_sec=0.05)
            enabled = True
            call('mission_manager/set_autonomy', SetBool.Request(data=True))
            print('Software session enabled; keep this process running. '
                  'Ctrl+C takes over.', flush=True)
            deadline = None if args.duration is None else time.monotonic() + args.duration
            while deadline is None or time.monotonic() < deadline:
                try:
                    call('tello_bridge/heartbeat', timeout=0.2)
                except ServiceRejected as error:
                    state = json.loads(str(error))
                    if state.get('landing_latched'):
                        # Landing revoked the lease. Preserve both latches until
                        # the operator explicitly takes over; never auto-rearm.
                        enabled = False
                        print('Landing intent latched; explicit takeover required. '
                              + str(error), flush=True)
                        return 0 if state.get('reason') == 'SUPERVISOR_LAND' else 2
                    raise
                rclpy.spin_once(node, timeout_sec=0.08)
        elif args.action in ('disable', 'takeover'):
            takeover()
        elif args.action == 'land':
            print(call('tello_bridge/land'))
            call('mission_manager/manual_takeover')
        else:
            path = 'prepare_fake_hover' if args.action == 'prepare-fake' else 'status'
            print(call('tello_bridge/' + path))
    except KeyboardInterrupt:
        pass
    except RuntimeError as error:
        print(json.dumps({'error': str(error)}), flush=True)
        return 2
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if enabled:
            try:
                takeover()
            except RuntimeError as error:
                print('Cleanup incomplete: ' + str(error), flush=True)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0
