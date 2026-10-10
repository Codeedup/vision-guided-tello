"""SDK 1.3 telemetry parsing. Reception proves link activity, not flight state."""

import math

from tello_bridge.policy import Telemetry


def parse_state(packet, receipt_ns):
    fields = {}
    try:
        for item in packet.decode('ascii').strip().split(';'):
            if not item:
                continue
            key, value = item.split(':', 1)
            if key in fields:
                raise ValueError('Duplicate telemetry key')
            fields[key] = float(value)
    except (UnicodeError, ValueError, TypeError, AttributeError):
        return Telemetry(receipt_ns, None, None)

    def number(key, lower, upper):
        value = fields.get(key)
        return value if value is not None and math.isfinite(
            value) and lower <= value <= upper else None
    return Telemetry(receipt_ns, number('bat', 0, 100), number('h', 0, 3000))


class StateReceiver:
    """Explicit nonblocking telemetry socket, with bounded receive work."""

    def __init__(self, host, bind='0.0.0.0', port=8890):
        self.host, self.bind, self.port = host, bind, port
        self.socket = None

    def start(self):
        import socket
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.socket.setblocking(False)
            self.socket.bind((self.bind, self.port))
        except BaseException:
            self.close()
            raise

    def read(self, mono_ns):
        latest = None
        for _ in range(64):
            try:
                packet, peer = self.socket.recvfrom(2048)
            except BlockingIOError:
                break
            if peer[0] == self.host:
                latest = parse_state(packet, mono_ns)
        return latest

    def close(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None
