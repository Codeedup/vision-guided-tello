"""Windows FAKE, grounded-bench and flight gateways with local Space abort."""

import argparse
from collections import deque
import hashlib
import json
from pathlib import Path
import socket
import tempfile
import time

from tello_bridge.arbiter import Arbiter
from tello_bridge.forward_approved import valid_session_id
from tello_bridge.policy import Authority, Command, Limits, Telemetry
from tello_bridge.telemetry import StateReceiver
from tello_bridge.transport import FakeTransport, OwnerLock, UdpSdkTransport


WIRE_KEYS = frozenset({'version', 'decision_ns', 'source_ns', 'enabled',
                       'tracking', 'land', 'lateral', 'vertical'})
FLIGHT_WIRE_KEYS = WIRE_KEYS | {'session_id', 'source_frame_id'}
LEASE_KEYS = frozenset({'version', 'kind', 'session_id', 'sequence'})


def decode_object(data):
    """Parse one bounded JSON object without duplicate wire keys."""
    if len(data) > 512:
        raise ValueError('Oversize packet')

    def unique(pairs):
        item = {}
        for key, value in pairs:
            if key in item:
                raise ValueError('Duplicate wire key')
            item[key] = value
        return item

    item = json.loads(data.decode('utf-8'), object_pairs_hook=unique)
    if type(item) is not dict:
        raise ValueError('Invalid wire object')
    return item


def decode_packet(data, session_id=None, source_frame_id=None):
    """Decode a strict approved decision without creating authority."""
    item = decode_object(data)
    flight = session_id is not None
    if (frozenset(item) != (FLIGHT_WIRE_KEYS if flight else WIRE_KEYS)
            or type(item['version']) is not int
            or item['version'] != (2 if flight else 1)
            or (flight and item['session_id'] != session_id)):
        raise ValueError('Invalid wire schema')
    if flight and (type(item['source_frame_id']) is not str or
                   len(item['source_frame_id']) > 64 or
                   (item['tracking'] and
                    item['source_frame_id'] != source_frame_id)):
        raise ValueError('Unexpected flight motion source')
    if (type(item['decision_ns']) is not int
            or type(item['source_ns']) is not int):
        raise ValueError('Invalid timestamp type')
    if any(type(item[key]) is not bool
           for key in ('enabled', 'tracking', 'land')):
        raise ValueError('Invalid flag type')
    return Command(item['decision_ns'], item['source_ns'], item['enabled'],
                   item['tracking'], item['land'],
                   item['lateral'], item['vertical'],
                   item['source_frame_id'] if flight else '')


def decode_lease(data, session_id):
    """Decode a flight lease separately from approved movement decisions."""
    item = decode_object(data)
    if (frozenset(item) != LEASE_KEYS or type(item['version']) is not int
            or item['version'] != 2 or item['kind'] != 'operator_lease'
            or item['session_id'] != session_id
            or type(item['sequence']) is not int or item['sequence'] <= 0):
        raise ValueError('Invalid operator lease')
    return item['sequence']


def fake_arm_marker(port):
    """Return a local FAKE exercise signal path for one gateway port."""
    return Path(tempfile.gettempdir()) / f'tello-fake-gateway-{port}.armed'


class FakeGateway:
    """Single-threaded policy owner; operator callbacks bypass UDP and ROS."""

    def __init__(self, transport=None):
        """Start only a FAKE transport."""
        self.arbiter = Arbiter(transport or FakeTransport(), Authority())
        self.arbiter.start()
        self.events = deque(maxlen=8192)
        self.events_omitted = 0
        self.fake_hover = False
        self.closed = False
        self.log('startup_disabled')

    @property
    def guard(self):
        """Return the shared command authority."""
        return self.arbiter.authority

    def log(self, event, **fields):
        """Retain bounded local evidence with a monotonic timestamp."""
        if len(self.events) == self.events.maxlen:
            self.events_omitted += 1
        self.events.append(dict(event=event, mono_ns=time.monotonic_ns(),
                                **fields))

    def receive(self, data, ros_ns=None, mono_ns=None):
        """Validate the latest wire decision independently."""
        mono_ns = time.monotonic_ns() if mono_ns is None else mono_ns
        ros_ns = time.time_ns() if ros_ns is None else ros_ns
        if self.guard.landing_latched:
            self.log('decision_discarded_aborted')
            return False
        try:
            command = decode_packet(data, getattr(self, 'flight_session_id', None),
                                    getattr(self, 'flight_source_frame_id', None))
        except (UnicodeError, ValueError, TypeError, KeyError) as error:
            self.log('decision_rejected', error=type(error).__name__)
            self.guard.command = None
            return False
        accepted = self.guard.accept(command, ros_ns, mono_ns)
        self.log('decision', accepted=accepted, reason=self.guard.reason,
                 source_frame_id=command.source_frame_id,
                 enabled=command.enabled, tracking=command.tracking,
                 lateral=command.lateral, vertical=command.vertical)
        return accepted

    def prepare_fake_hover(self, ros_ns=None, mono_ns=None):
        """Set simulated hover only after a disabled decision."""
        mono_ns = time.monotonic_ns() if mono_ns is None else mono_ns
        if (self.guard.armed or self.guard.landing_latched
                or not self.guard.disabled_seen):
            return False
        self.fake_hover = True
        self.guard.flight_state = 'HOVER_CONFIRMED'
        self.guard.telemetry = Telemetry(mono_ns, 80.0, 70.0)
        self.log('fake_hover_prepared')
        return True

    def arm_fake(self, ros_ns=None, mono_ns=None):
        """Arm a local simulated session when all policy gates pass."""
        mono_ns = time.monotonic_ns() if mono_ns is None else mono_ns
        ros_ns = time.time_ns() if ros_ns is None else ros_ns
        accepted = self.fake_hover and self.guard.arm(ros_ns, mono_ns)
        self.log('local_fake_arm', accepted=bool(accepted))
        return bool(accepted)

    def abort(self, cause='space', ros_ns=None, mono_ns=None):
        """Latch abort and dispatch neutral then land without ROS."""
        mono_ns = time.monotonic_ns() if mono_ns is None else mono_ns
        ros_ns = time.time_ns() if ros_ns is None else ros_ns
        self.log('operator_input', cause=cause, prior_reason=self.guard.reason)
        if self.guard.reason != 'OPERATOR_ABORT':
            self.guard.request_land('OPERATOR_ABORT')
        self.log('abort_latch', reason=self.guard.reason)
        before = self.guard.land_attempts
        self.arbiter.operator_abort(mono_ns)
        self.log_land_attempt(before)
        self.log('abort_dispatch', reason=self.guard.reason,
                 attempts=self.guard.land_attempts)

    def log_land_attempt(self, previous_count):
        """Record the reserved retry number and final transport result."""
        if self.guard.land_attempts > previous_count:
            event = self.arbiter.transport.events[-1]
            self.log('land_attempt', retry_number=self.guard.land_attempts,
                     os_send_result=event.get('os_send_result'))

    def focus_lost(self):
        """Treat loss of the required operator focus as abort."""
        if self.guard.armed:
            self.abort('focus_loss')

    def tick(self, ros_ns=None, mono_ns=None):
        """Run one bounded policy and FAKE transport turn."""
        mono_ns = time.monotonic_ns() if mono_ns is None else mono_ns
        ros_ns = time.time_ns() if ros_ns is None else ros_ns
        if self.fake_hover and not self.guard.landing_latched:
            self.guard.telemetry = Telemetry(mono_ns, 80.0, 70.0)
        if self.guard.armed:
            self.guard.heartbeat(mono_ns)
        before = self.guard.land_attempts
        self.arbiter.tick(ros_ns, mono_ns)
        self.log_land_attempt(before)

    def reset_fake(self):
        """Reset the FAKE lifecycle only on explicit local input."""
        self.arbiter.takeover(time.monotonic_ns())
        self.guard.landing_latched = False
        self.guard.land_attempts = 0
        self.guard.last_land_ns = None
        self.guard.flight_state = 'UNKNOWN'
        self.guard.disabled_seen = False
        self.guard.reason = 'FAKE_RESET_DISABLED'
        self.fake_hover = False
        self.log('explicit_fake_reset')

    def close(self):
        """Abort an active session, then close FAKE transport."""
        if not self.closed:
            if self.guard.armed:
                self.abort('window_close')
            self.arbiter.shutdown(time.monotonic_ns())
            self.closed = True


class RealGateway(FakeGateway):
    """
    A deliberately separate real gateway mode.

    ``grounded-bench`` never consumes upstream movement and only emits local,
    single-packet axis pulses.  ``flight`` is a fresh-process mode which retains
    the normal independent ApprovedCommand authority and requires local hover
    confirmation in addition to current aircraft state telemetry.
    """

    BENCH_AXES = {
        'lateral-positive': (5, 0), 'lateral-negative': (-5, 0),
        'vertical-positive': (0, 5), 'vertical-negative': (0, -5),
    }

    def __init__(self, mode, transport, state_receiver, flight_session_id=None,
                 flight_source_frame_id=None):
        if mode not in ('grounded-bench', 'flight'):
            raise ValueError('Invalid real gateway mode')
        if (mode == 'flight' and not valid_session_id(flight_session_id)) or (
                mode == 'grounded-bench' and flight_session_id is not None):
            raise ValueError('Flight mode requires a unique session ID')
        if (mode == 'flight' and (type(flight_source_frame_id) is not str or
                                  flight_source_frame_id !=
                                  'supervised_lateral_trial_' + flight_session_id)):
            raise ValueError('Flight mode requires the trial source frame ID')
        self.mode = mode
        self.flight_session_id = flight_session_id
        self.flight_source_frame_id = flight_source_frame_id
        self.last_lease_sequence = 0
        self.last_upstream_peer = None
        self.trial_release_sent = False
        self.rehearsal = False
        self.state_receiver = state_receiver
        self.arbiter = Arbiter(transport, Authority(Limits(
            maximum_lateral_command=5, maximum_vertical_command=5)))
        self.events = deque(maxlen=8192)
        self.events_omitted = 0
        self.fake_hover = False
        self.closed = False
        self.hover_confirmed_by_pilot = False
        self.bench_started_ns = time.monotonic_ns()
        self.bench_session_expired = False
        self.arbiter.start()
        try:
            self.state_receiver.start()
            self.arbiter.transport_call('enter_sdk_mode', time.monotonic_ns())
        except BaseException:
            self.state_receiver.close()
            self.arbiter.transport.close()
            raise
        self.log('startup_disabled', mode=mode)

    def receive(self, data, ros_ns=None, mono_ns=None):
        if self.mode == 'grounded-bench':
            # The bench is locally scripted: no WSL decision can produce RC.
            self.log('decision_discarded_grounded_bench')
            return False
        try:
            sequence = decode_lease(data, self.flight_session_id)
        except (UnicodeError, ValueError, TypeError, KeyError):
            pass
        else:
            mono_ns = time.monotonic_ns() if mono_ns is None else mono_ns
            if sequence <= self.last_lease_sequence:
                self.log('operator_lease', accepted=False, sequence=sequence)
                return False
            self.last_lease_sequence = sequence
            accepted = self.guard.heartbeat(mono_ns)
            self.log('operator_lease', accepted=accepted, sequence=sequence)
            return accepted
        return super().receive(data, ros_ns, mono_ns)

    def refresh_telemetry(self, mono_ns):
        telemetry = self.state_receiver.read(mono_ns)
        if telemetry is not None:
            self.guard.telemetry = telemetry
            self.log('state_telemetry', battery_percent=telemetry.battery_percent,
                     height_cm=telemetry.height_cm)

    def confirm_hover(self, mono_ns=None):
        """Record one local pilot assertion; it cannot outlive a stale state feed."""
        mono_ns = time.monotonic_ns() if mono_ns is None else mono_ns
        if self.mode != 'flight' or not self.guard.telemetry_ok(mono_ns):
            self.log('pilot_hover_confirmation', accepted=False)
            return False
        self.hover_confirmed_by_pilot = True
        self.guard.flight_state = 'HOVER_CONFIRMED'
        self.log('pilot_hover_confirmation', accepted=True)
        return True

    def arm_flight(self, ros_ns=None, mono_ns=None):
        if self.mode != 'flight' or not self.hover_confirmed_by_pilot:
            self.log('local_flight_arm', accepted=False)
            return False
        accepted = self.guard.arm(time.time_ns() if ros_ns is None else ros_ns,
                                  time.monotonic_ns() if mono_ns is None else mono_ns)
        self.log('local_flight_arm', accepted=bool(accepted))
        return bool(accepted)

    def release_trial(self, sock):
        """Let the focused pilot release one ROS pulse without changing focus."""
        if (self.mode != 'flight' or not self.guard.armed or
                self.guard.landing_latched or self.trial_release_sent or
                self.last_upstream_peer is None):
            self.log('trial_release', accepted=False)
            return False
        packet = json.dumps({'version': 2, 'kind': 'trial_release',
                             'session_id': self.flight_session_id,
                             'fake': self.rehearsal},
                            separators=(',', ':')).encode('utf-8')
        try:
            sent = sock.sendto(packet, self.last_upstream_peer)
            if sent != len(packet):
                raise OSError('Incomplete trial release send')
        except OSError:
            self.log('trial_release', accepted=False, error='OS_SEND_ERROR')
            self.abort('trial_release_link_error')
            return False
        self.trial_release_sent = True
        self.log('trial_release', accepted=True)
        return True

    def bench_pulse(self, axis):
        """Emit one allowlisted capped axis packet immediately followed by neutral."""
        if self.mode != 'grounded-bench' or self.guard.landing_latched:
            self.log('bench_pulse', axis=axis, accepted=False)
            return False
        values = self.BENCH_AXES.get(axis)
        if values is None:
            self.log('bench_pulse', axis=axis, accepted=False)
            return False
        mono_ns = time.monotonic_ns()
        try:
            self.arbiter.transport_call('send_rc', *values, mono_ns)
            self.arbiter.neutral(mono_ns)
        except OSError:
            self.guard.link(False)
            self.arbiter.transport.close()
            self.log('bench_pulse', axis=axis, accepted=False)
            return False
        self.log('bench_pulse', axis=axis, accepted=True, lateral=values[0],
                 vertical=values[1])
        return True

    def tick_grounded_bench(self, mono_ns):
        """Poll replies and expire the grounded bench without periodic RC output."""
        if (not self.bench_session_expired and
                mono_ns - self.bench_started_ns >= self.guard.limits.maximum_session_ns):
            self.bench_session_expired = True
            self.log('bench_session_expired')
            if not self.guard.landing_latched:
                self.abort('bench_session_expired', mono_ns=mono_ns)
        try:
            self.arbiter.transport_call('poll', mono_ns)
            self.refresh_telemetry(mono_ns)
        except OSError:
            self.log('grounded_bench_link_error')
            self.guard.link(False)
            self.arbiter.transport.close()

    def tick(self, ros_ns=None, mono_ns=None):
        mono_ns = time.monotonic_ns() if mono_ns is None else mono_ns
        ros_ns = time.time_ns() if ros_ns is None else ros_ns
        if self.mode == 'grounded-bench':
            self.tick_grounded_bench(mono_ns)
            return
        try:
            self.refresh_telemetry(mono_ns)
        except OSError:
            self.log('state_link_error')
            self.arbiter.transport_fault(mono_ns, 'STATE_LINK_FAULT')
        before = self.guard.land_attempts
        self.arbiter.tick(ros_ns, mono_ns)
        self.log_land_attempt(before)

    def close(self):
        if not self.closed:
            if self.mode == 'grounded-bench':
                # A grounded pulse always ends in neutral. On close, send one
                # final neutral and do not retry a land command against an
                # aircraft that is already grounded.
                self.arbiter.neutral(time.monotonic_ns())
                self.guard.link(False)
                self.arbiter.transport.close()
                self.state_receiver.close()
                self.closed = True
                return
            if self.guard.armed:
                self.abort('window_close')
            self.arbiter.shutdown(time.monotonic_ns())
            self.state_receiver.close()
            self.closed = True


class SimulatedStateFeed:
    """Fresh synthetic state for a socket-free flight protocol rehearsal."""

    def start(self):
        pass

    def read(self, mono_ns):
        return Telemetry(mono_ns, 80.0, 70.0)

    def close(self):
        pass


def main(argv=None):
    """Run the focused Tk operator surface and bounded UDP listener."""
    parser = argparse.ArgumentParser(
        description='Windows Tello gateway; FAKE is the default')
    parser.add_argument('--mode', choices=('fake', 'grounded-bench', 'flight',
                                           'flight-dry-run'),
                        default='fake')
    parser.add_argument('--bind', required=True,
                        help='Explicit Windows IPv4 decision-listener bind address')
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--trace', required=True,
                        help='Output JSON trace on clean close')
    parser.add_argument('--hardware', action='store_true',
                        help='Required for either real mode')
    parser.add_argument('--tello-bind',
                        help='Windows Tello-Wi-Fi IPv4 for SDK/state sockets')
    parser.add_argument('--propellers-removed', action='store_true')
    parser.add_argument('--propellers-installed', action='store_true')
    parser.add_argument('--aircraft-grounded', action='store_true')
    parser.add_argument('--pilot-present', action='store_true')
    parser.add_argument('--flight-session-id',
                        help='Fresh 32-character hex ID shared with the ROS forwarder')
    parser.add_argument('--upstream-ip',
                        help='Only accept flight decisions and leases from this IPv4')
    parser.add_argument('--flight-source-frame-id',
                        help='Only accept nonzero flight decisions from this frame ID')
    args = parser.parse_args(argv)
    socket.inet_aton(args.bind)
    if not 1 <= args.port <= 65535:
        parser.error('port must be in [1,65535]')
    real = args.mode in ('grounded-bench', 'flight')
    assertions = (args.propellers_removed, args.propellers_installed,
                  args.aircraft_grounded, args.pilot_present, args.tello_bind)
    if not real and (args.hardware or any(assertions)):
        parser.error('FAKE mode cannot accept hardware assertions')
    if real and (not args.hardware or not args.tello_bind or not args.pilot_present):
        parser.error('Real modes require --hardware --tello-bind --pilot-present')
    if args.tello_bind:
        socket.inet_aton(args.tello_bind)
    if args.upstream_ip:
        socket.inet_aton(args.upstream_ip)
    if args.mode == 'grounded-bench':
        if (not args.propellers_removed or args.propellers_installed or
                not args.aircraft_grounded or args.flight_session_id or args.upstream_ip or
                args.flight_source_frame_id):
            parser.error('grounded-bench requires --propellers-removed '
                         '--aircraft-grounded and rejects --propellers-installed')
    if args.mode in ('flight', 'flight-dry-run'):
        if (not args.upstream_ip or not valid_session_id(args.flight_session_id)
                or args.flight_source_frame_id !=
                'supervised_lateral_trial_' + args.flight_session_id):
            parser.error('Flight session requires --upstream-ip, '
                         '--flight-session-id and matching trial frame ID')
    if args.mode == 'flight':
        if (not args.propellers_installed or args.propellers_removed or
                args.aircraft_grounded):
            parser.error('flight requires --propellers-installed and rejects '
                         'grounded/propellers-removed assertions')
    if args.mode == 'fake' and (args.flight_session_id or args.upstream_ip or
                                args.flight_source_frame_id):
        parser.error('FAKE mode cannot accept flight session arguments')
    import tkinter as tk

    owner = OwnerLock(args.mode + '-windows-gateway:' + args.bind + ':' + str(args.port))
    owner.acquire()
    marker = fake_arm_marker(args.port)
    marker.unlink(missing_ok=True)
    sock = None
    gateway = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        sock.bind((args.bind, args.port))
        if args.mode == 'fake':
            gateway = FakeGateway()
        elif args.mode == 'flight-dry-run':
            gateway = RealGateway(
                'flight', FakeTransport(), SimulatedStateFeed(),
                flight_session_id=args.flight_session_id,
                flight_source_frame_id=args.flight_source_frame_id)
            gateway.rehearsal = True
        else:
            gateway = RealGateway(
                args.mode,
                UdpSdkTransport(bind=args.tello_bind, local_port=8889),
                StateReceiver('192.168.10.1', bind=args.tello_bind),
                flight_session_id=args.flight_session_id,
                flight_source_frame_id=args.flight_source_frame_id)
        root = tk.Tk()
        root.title(('FAKE' if args.mode == 'fake' else args.mode.upper())
                   + ' Tello gateway — Space abort')
        status = tk.StringVar(
            value=(('FAKE FLIGHT REHEARSAL' if args.mode == 'flight-dry-run'
                    else args.mode.upper()) +
                   (' — 60 s limit; Space aborts' if args.mode == 'grounded-bench'
                    else ' DISABLED — focus this window for Space')))
        tk.Label(root, textvariable=status, width=72, pady=18).pack()

        def arm_fake():
            if gateway.arm_fake():
                marker.write_text('FAKE armed\n', encoding='utf-8')

        if args.mode == 'fake':
            buttons = (
                tk.Button(root, text='Prepare FAKE hover',
                          command=gateway.prepare_fake_hover),
                tk.Button(root, text='Arm FAKE session', command=arm_fake),
                tk.Button(root, text='Explicit FAKE reset',
                          command=gateway.reset_fake),
            )
        elif args.mode == 'grounded-bench':
            buttons = tuple(tk.Button(
                root, text='Pulse ' + axis + ' then neutral',
                command=lambda axis=axis: gateway.bench_pulse(axis))
                for axis in RealGateway.BENCH_AXES)
        else:
            buttons = (
                tk.Button(root, text=('Confirm FAKE hover' if
                                      args.mode == 'flight-dry-run'
                                      else 'Pilot confirms hover'),
                          command=gateway.confirm_hover),
                tk.Button(root, text='Arm flight session',
                          command=gateway.arm_flight),
                tk.Button(root, text='Release ONE ROS lateral pulse',
                          command=lambda: gateway.release_trial(sock)),
            )
        for button in buttons:
            button.pack()

        def abort(event=None, cause='space'):
            gateway.abort(cause)
            status.set('ABORT LATCHED — landing requested/unconfirmed')
            return 'break'  # Space must not activate a focused reset button.

        def focus_lost(event):
            if gateway.guard.armed:
                root.after_idle(lambda: gateway.focus_lost() if
                                not root.focus_get() else None)

        def loop():
            # Bound work per UI turn; never wait on ROS or an SDK reply.
            for _ in range(32):
                try:
                    data, peer = sock.recvfrom(1024)
                except BlockingIOError:
                    break
                if args.mode in ('flight', 'flight-dry-run') and peer[0] != args.upstream_ip:
                    gateway.log('upstream_peer_rejected', address=peer[0])
                    continue
                accepted = gateway.receive(data)
                if (accepted and args.mode in ('flight', 'flight-dry-run')):
                    gateway.last_upstream_peer = peer
            gateway.tick()
            if (args.mode == 'grounded-bench' and
                    gateway.bench_session_expired):
                status.set('BENCH SESSION EXPIRED — shutting down')
                root.after_idle(close)
                return
            if gateway.guard.landing_latched:
                status.set('ABORT LATCHED — landing requested/unconfirmed; '
                           + gateway.guard.status()['landing_delivery'])
            elif gateway.guard.armed:
                status.set(args.mode.upper() + ' ARMED — focus this window; '
                           'Space aborts and requests land')
            root.after(20, loop)

        def close():
            gateway.close()
            root.destroy()

        root.bind('<KeyPress-space>', abort)
        for button in buttons:
            button.bind('<KeyPress-space>', abort)
        root.bind('<FocusOut>', focus_lost)
        root.protocol('WM_DELETE_WINDOW', close)
        root.after(20, loop)
        root.focus_force()
        try:
            root.mainloop()
        except KeyboardInterrupt:
            gateway.log('operator_interrupt')
    finally:
        marker.unlink(missing_ok=True)
        if gateway:
            gateway.close()
            transport_events = list(gateway.arbiter.transport.events)
            decision_names = {
                'decision', 'decision_rejected', 'decision_discarded_aborted',
                'decision_discarded_grounded_bench', 'local_flight_arm',
                'pilot_hover_confirmation', 'bench_pulse',
            }
            with open(args.trace, 'x', encoding='utf-8') as output:
                json.dump({'fake': args.mode in ('fake', 'flight-dry-run'),
                           'mode': args.mode,
                           'flight_session_id': args.flight_session_id,
                           'flight_source_frame_id': args.flight_source_frame_id,
                           'grounded_bench_session': ({
                               'started_ns': gateway.bench_started_ns,
                               'maximum_duration_ns':
                               gateway.guard.limits.maximum_session_ns,
                               'expired': gateway.bench_session_expired,
                           } if args.mode == 'grounded-bench' else None),
                           'events': list(gateway.events),
                           'events_omitted': gateway.events_omitted,
                           'decision_receipts': [
                               event for event in gateway.events
                               if event.get('event') in decision_names],
                           'command_attempts': [
                               {'command': event['attempt'],
                                'mono_ns': event.get('mono_ns'),
                                'observed_ns': event.get('attempt_observed_ns'),
                                'physical_outcome': event.get('physical_outcome')}
                               for event in transport_events
                               if 'attempt' in event],
                           'os_send_results': [
                               {'command': event['attempt'],
                                'result': event.get('os_send_result'),
                                'error': event.get('error'),
                                'observed_ns': event.get('os_send_observed_ns')}
                               for event in transport_events
                               if 'attempt' in event],
                           'sdk_replies': [
                               {'reply': event['reply'],
                                'observed_ns': event.get('reply_observed_ns'),
                                'correlation': event.get('acknowledgement')}
                               for event in transport_events
                               if 'reply' in event],
                           'physical_observations': [{
                               'outcome': 'UNKNOWN',
                               'source': 'not observable from SDK transport',
                           }],
                           'transport_events': transport_events,
                           'transport_events_omitted':
                           gateway.arbiter.transport.events_omitted,
                           'final_status': gateway.guard.status()},
                          output, indent=2)
            digest = hashlib.sha256(Path(args.trace).read_bytes()).hexdigest()
            print('trace_sha256=' + digest)
        if sock:
            sock.close()
        owner.close()


if __name__ == '__main__':
    main()
