"""Software operator. An enabled session has a short continuously renewed lease."""

import argparse
import json
import os
import signal
import time

import rclpy
from rclpy.signals import SignalHandlerOptions
from std_srvs.srv import SetBool, Trigger
from tello_bridge.cadence import run_heartbeats
from tello_bridge.timing import source_hash, TimingTrace


class ServiceRejected(RuntimeError):
    """An available service deliberately rejected a request."""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('status', 'enable', 'disable', 'takeover',
                                           'land', 'prepare-fake'))
    parser.add_argument('--namespace', default='/webcam_test')
    parser.add_argument('--duration', type=float, default=None,
                        help='Optional bounded enabled session, seconds')
    parser.add_argument('--reset-mock', action='store_true',
                        help='Explicit synthetic session: reset the mock before enable')
    parser.add_argument('--trace', help='New FAKE-only client timing report (no overwrite)')
    parser.add_argument('--legacy-heartbeat-cadence', action='store_true',
                        help='FAKE-only comparison with the original unpaced loop')
    args = parser.parse_args(argv)
    if args.duration is not None and not 0 < args.duration <= 600:
        parser.error('--duration must be in (0,600]')
    if args.reset_mock and args.action != 'enable':
        parser.error('--reset-mock requires enable')
    if (args.trace or args.legacy_heartbeat_cadence) and args.action != 'enable':
        parser.error('Timing diagnostics require enable')
    rclpy.init(args=[], signal_handler_options=SignalHandlerOptions.NO)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    node = rclpy.create_node('tello_operator_' + str(os.getpid()), namespace=args.namespace)
    clients = {}
    trace = None
    request_sequence = 0

    def get_client(path):
        kind = SetBool if 'set_autonomy' in path else Trigger
        if path not in clients:
            clients[path] = node.create_client(kind, path)
        return clients[path]

    def ready(client, timeout):
        discovery_deadline = time.monotonic() + timeout
        while not client.service_is_ready() and time.monotonic() < discovery_deadline:
            rclpy.spin_once(node, timeout_sec=0.02)
        if not client.service_is_ready():
            raise RuntimeError('Service unavailable: ' + client.service_name)

    def call(path, request=None, timeout=5.0):
        nonlocal request_sequence
        client = get_client(path)
        ready(client, timeout)
        sent_ns = time.monotonic_ns() if trace else None
        request_sequence += 1
        future = client.call_async(request or Trigger.Request())
        deadline = time.monotonic() + timeout
        while not future.done() and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.01)
        complete = future.done()
        result = future.result() if complete else None
        if trace:
            observed_ns = time.monotonic_ns()
            state = None
            if result is not None:
                try:
                    state = json.loads(result.message)
                except (ValueError, TypeError):
                    pass
            trace.record('service_call', path=path, request_sequence=request_sequence,
                         send_ns=sent_ns, response_observed_ns=observed_ns,
                         observed_delay_ns=observed_ns - sent_ns, completed=complete,
                         success=None if result is None else result.success, state=state,
                         timeout_s=timeout)
        if not complete or result is None:
            raise RuntimeError('Service timed out: ' + path)
        if not result.success:
            raise ServiceRejected(result.message)
        return result.message

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
            # Discover all session/cleanup endpoints before starting the short lease.
            paths = ['tello_bridge/heartbeat', 'tello_bridge/arm',
                     'tello_bridge/manual_takeover', 'mission_manager/set_autonomy',
                     'mission_manager/manual_takeover']
            if args.reset_mock:
                paths += ['tello_bridge/status', 'mock_tello/reset']
            if args.trace or args.legacy_heartbeat_cadence:
                paths += ['tello_bridge/status']
            for path in paths:
                ready(get_client(path), 5.0)
            if args.trace or args.legacy_heartbeat_cadence:
                state = json.loads(call('tello_bridge/status'))
                if state.get('fake') is not True:
                    raise RuntimeError('Timing diagnostics require a FAKE bridge')
                if (state.get('armed') or state.get('landing_latched')
                        or not state.get('upstream_disabled')
                        or state.get('flight_state') != 'HOVER_CONFIRMED'):
                    raise RuntimeError('Diagnostic requires prepared, unarmed, '
                                       'disabled FAKE state')
                if args.trace:
                    trace = TimingTrace(
                        args.trace, fake=True,
                        metadata={'role': 'operator', 'namespace': args.namespace,
                                  'operator_sha256': source_hash(__file__),
                                  'requested_duration_s': args.duration,
                                  'cadence': ('legacy' if args.legacy_heartbeat_cadence
                                              else 'paced'),
                                  'heartbeat_period_ns': 80000000,
                                  'server_receipt_times': False})
            call('mission_manager/set_autonomy', SetBool.Request(data=False))
            if args.reset_mock:
                if not json.loads(call('tello_bridge/status')).get('fake'):
                    raise RuntimeError('Mock reset requires a fake bridge')
                reset_deadline = time.monotonic() + 1.0
                while True:
                    try:
                        call('mock_tello/reset')
                        break
                    except ServiceRejected:
                        if time.monotonic() >= reset_deadline:
                            raise
                        rclpy.spin_once(node, timeout_sec=0.02)
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
            try:
                run_heartbeats(
                    lambda: call('tello_bridge/heartbeat', timeout=0.2),
                    lambda **kwargs: rclpy.spin_once(node, **kwargs), args.duration,
                    legacy=args.legacy_heartbeat_cadence)
            except ServiceRejected as error:
                state = json.loads(str(error))
                if state.get('landing_latched'):
                    # Preserve both latches; never automatically rearm.
                    enabled = False
                    print('Landing intent latched; explicit takeover required. '
                          + str(error), flush=True)
                    return 0 if state.get('reason') == 'SUPERVISOR_LAND' else 2
                raise
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
    except (RuntimeError, OSError, ValueError) as error:
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
        if trace:
            trace.finish('operator_exit')
    return 0
