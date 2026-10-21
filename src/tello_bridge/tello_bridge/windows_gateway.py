"""FAKE Windows gateway with local Space abort and no aircraft socket."""

import argparse
from collections import deque
import json
import socket
import time

from tello_bridge.arbiter import Arbiter
from tello_bridge.policy import Authority, Command, Telemetry
from tello_bridge.transport import FakeTransport, OwnerLock


WIRE_KEYS = frozenset({'version', 'decision_ns', 'source_ns', 'enabled',
                       'tracking', 'land', 'lateral', 'vertical'})


def decode_packet(data):
    """Decode a strict version-one decision without creating authority."""
    if len(data) > 512:
        raise ValueError('Oversize decision')

    def unique(pairs):
        item = {}
        for key, value in pairs:
            if key in item:
                raise ValueError('Duplicate wire key')
            item[key] = value
        return item

    item = json.loads(data.decode('utf-8'), object_pairs_hook=unique)
    if (type(item) is not dict or frozenset(item) != WIRE_KEYS
            or type(item['version']) is not int or item['version'] != 1):
        raise ValueError('Invalid wire schema')
    if (type(item['decision_ns']) is not int
            or type(item['source_ns']) is not int):
        raise ValueError('Invalid timestamp type')
    if any(type(item[key]) is not bool
           for key in ('enabled', 'tracking', 'land')):
        raise ValueError('Invalid flag type')
    return Command(item['decision_ns'], item['source_ns'], item['enabled'],
                   item['tracking'], item['land'],
                   item['lateral'], item['vertical'])


class FakeGateway:
    """Single-threaded policy owner; operator callbacks bypass UDP and ROS."""

    def __init__(self, transport=None):
        """Start only a FAKE transport."""
        self.arbiter = Arbiter(transport or FakeTransport(), Authority())
        self.arbiter.start()
        self.events = deque(maxlen=4096)
        self.fake_hover = False
        self.closed = False
        self.log('startup_disabled')

    @property
    def guard(self):
        """Return the shared command authority."""
        return self.arbiter.authority

    def log(self, event, **fields):
        """Retain bounded local evidence with a monotonic timestamp."""
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
            command = decode_packet(data)
        except (UnicodeError, ValueError, TypeError, KeyError) as error:
            self.log('decision_rejected', error=type(error).__name__)
            self.guard.command = None
            return False
        accepted = self.guard.accept(command, ros_ns, mono_ns)
        self.log('decision', accepted=accepted, reason=self.guard.reason)
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


def main(argv=None):
    """Run the focused Tk operator surface and bounded UDP listener."""
    parser = argparse.ArgumentParser(
        description='FAKE Windows Space abort gateway')
    parser.add_argument('--bind', required=True,
                        help='Explicit Windows IPv4 bind address')
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--trace', required=True,
                        help='Output JSON trace on clean close')
    args = parser.parse_args(argv)
    socket.inet_aton(args.bind)
    if not 1 <= args.port <= 65535:
        parser.error('port must be in [1,65535]')
    import tkinter as tk

    owner = OwnerLock(
        'fake-windows-gateway:' + args.bind + ':' + str(args.port))
    owner.acquire()
    sock = None
    gateway = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        sock.bind((args.bind, args.port))
        gateway = FakeGateway()
        root = tk.Tk()
        root.title('FAKE Tello gateway — Space abort')
        status = tk.StringVar(
            value='FAKE DISABLED — focus this window for Space')
        tk.Label(root, textvariable=status, width=72, pady=18).pack()
        buttons = (
            tk.Button(root, text='Prepare FAKE hover',
                      command=gateway.prepare_fake_hover),
            tk.Button(root, text='Arm FAKE session',
                      command=gateway.arm_fake),
            tk.Button(root, text='Explicit FAKE reset',
                      command=gateway.reset_fake),
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
            latest = None
            for _ in range(32):
                try:
                    latest, _peer = sock.recvfrom(1024)
                except BlockingIOError:
                    break
            if latest is not None:
                gateway.receive(latest)
            gateway.tick()
            if gateway.guard.landing_latched:
                status.set('ABORT LATCHED — landing requested/unconfirmed; '
                           + gateway.guard.status()['landing_delivery'])
            elif gateway.guard.armed:
                status.set('FAKE ARMED — focus this window; '
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
        root.mainloop()
    finally:
        if gateway:
            gateway.close()
            with open(args.trace, 'x', encoding='utf-8') as output:
                json.dump({'fake': True, 'events': list(gateway.events),
                           'transport_events': list(
                               gateway.arbiter.transport.events),
                           'final_status': gateway.guard.status()},
                          output, indent=2)
        if sock:
            sock.close()
        owner.close()


if __name__ == '__main__':
    main()
