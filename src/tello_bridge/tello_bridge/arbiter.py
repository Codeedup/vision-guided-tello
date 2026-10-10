"""One transport owner. All safety and operator paths remain nonblocking."""

import time

from tello_bridge.policy import Authority


class Arbiter:
    def __init__(self, transport, authority=None):
        self.transport = transport
        self.authority = authority or Authority()
        self.last_rc = None
        self.last_send_ns = None
        self.owned_last_tick = False
        self.trace = None

    def transport_call(self, operation, *args):
        if self.trace is None:
            return getattr(self.transport, operation)(*args)
        start = time.monotonic_ns()
        error_name = None
        try:
            return getattr(self.transport, operation)(*args)
        except BaseException as error:
            error_name = type(error).__name__
            raise
        finally:
            end = time.monotonic_ns()
            self.trace.record('transport_operation', operation=operation,
                              start_ns=start, end_ns=end, duration_ns=end - start,
                              exception=error_name)

    def start(self):
        self.transport.start()
        self.authority.link(True)
        self.last_rc = self.last_send_ns = None

    def neutral(self, mono_ns):
        try:
            self.transport_call('send_rc', 0, 0, mono_ns)
            self.last_rc = (0, 0, 0, 0)
            self.last_send_ns = mono_ns
        except OSError:
            if self.authority.armed:
                self.authority.request_land('TRANSPORT_FAULT')
            self.authority.link(False)

    def takeover(self, mono_ns):
        self.authority.disarm('MANUAL_TAKEOVER')
        self.neutral(mono_ns)
        self.owned_last_tick = False

    def tick(self, ros_ns, mono_ns):
        try:
            self.transport_call('poll', mono_ns)
            kind, values = self.authority.action(ros_ns, mono_ns)
            owned = self.authority.armed or self.authority.landing_latched
            if kind == 'land':
                self.neutral(mono_ns)
                if self.authority.connected:
                    self.transport_call('send_land', mono_ns)
            elif kind == 'rc':
                if (values != self.last_rc or self.last_send_ns is None
                        or mono_ns - self.last_send_ns >= 50_000_000):
                    self.transport_call('send_rc', values[0], values[2], mono_ns)
                    self.last_rc, self.last_send_ns = values, mono_ns
            elif self.owned_last_tick:
                self.neutral(mono_ns)
            self.owned_last_tick = owned
        except OSError:
            if self.authority.armed:
                self.authority.request_land('TRANSPORT_FAULT')
            self.authority.link(False)
            self.transport.close()

    def shutdown(self, mono_ns):
        # Normal shutdown requests land only while software owns active flight.
        if self.authority.armed:
            self.authority.request_land('SHUTDOWN')
        self.neutral(mono_ns)
        if self.authority.connected and self.authority.landing_latched:
            try:
                if self.authority.land_attempts < self.authority.limits.maximum_land_attempts:
                    self.authority.land_attempts += 1
                    self.transport_call('send_land', mono_ns)
            except OSError:
                pass
        self.authority.disarm('SHUTDOWN')
        self.transport.close()
