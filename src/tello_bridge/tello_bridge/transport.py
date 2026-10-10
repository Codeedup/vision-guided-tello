"""
Nonblocking SDK 1.3 transport; construction and import never open sockets.

No takeoff or emergency primitive exists here. RC has only lateral and vertical
inputs, and forward/yaw are inserted as literal zero at the final wire boundary.
Unsequenced SDK replies are logged as uncorrelated, never as proof of landing.
"""

from collections import deque
import hashlib
import os
from pathlib import Path
import socket
import tempfile

from tello_bridge.policy import sdk_integer

DIAGNOSTIC_COMMANDS = frozenset({
    'command', 'battery?', 'height?', 'time?', 'temp?', 'attitude?',
    'baro?', 'acceleration?', 'tof?', 'wifi?', 'speed?', 'streamon', 'streamoff',
})


class OwnerLock:
    """Local host ownership only; cannot arbitrate two separate computers."""

    def __init__(self, key):
        self.path = Path(tempfile.gettempdir()) / (
            'vision-tello-' + hashlib.sha256(key.encode()).hexdigest()[:24] + '.lock')
        self.file = None

    def acquire(self):
        self.file = self.path.open('a')
        try:
            if os.name == 'nt':
                import msvcrt
                self.file.write('0')
                self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise RuntimeError('Another local arbiter owns this endpoint') from None

    def close(self):
        if self.file:
            self.file.close()
            self.file = None


class FakeTransport:
    """Explicit fake: bounded logs and optional delayed/missing replies/errors."""

    fake = True

    def __init__(self, reply_delay_ns=0, never_reply=False):
        self.connected = False
        self.events = deque(maxlen=2048)
        self.events_omitted = 0
        self.reply_delay_ns, self.never_reply = reply_delay_ns, never_reply
        self.fail_next = False
        self.pending = deque(maxlen=16)

    def record(self, event):
        if len(self.events) == self.events.maxlen:
            self.events_omitted += 1
        self.events.append(dict(fake=True, physical_outcome='UNKNOWN', **event))

    def start(self):
        self.connected = True

    def _send(self, text, mono_ns):
        ok = self.connected and not self.fail_next
        self.fail_next = False
        self.record({'attempt': text, 'mono_ns': mono_ns,
                     'os_send_result': 'FAKE_SENT' if ok else 'FAKE_ERROR',
                     'acknowledgement': None})
        if not ok:
            raise OSError('Simulated transport failure')
        if not text.startswith('rc ') and not self.never_reply:
            self.pending.append((mono_ns + self.reply_delay_ns, text))

    def send_rc(self, lateral, vertical, mono_ns):
        self._send(f'rc {sdk_integer(lateral)} 0 {sdk_integer(vertical)} 0', mono_ns)

    def send_land(self, mono_ns):
        self._send('land', mono_ns)

    def poll(self, mono_ns):
        while self.pending and self.pending[0][0] <= mono_ns:
            _, command = self.pending.popleft()
            reply = {'battery?': '80', 'height?': '70', 'time?': '0'}.get(command, 'ok')
            self.record({'reply': reply, 'for_command': command, 'mono_ns': mono_ns,
                         'acknowledgement': 'SIMULATED_ONLY'})

    def close(self):
        self.connected = False
        self.pending.clear()


class UdpSdkTransport:
    """
    Proposed real adapter. start() is the sole explicit network activation.

    No retries, sleeps, joins or automatic cleanup commands. The arbiter handles
    bounded land attempts. Nonblocking sendto reports OS acceptance only.
    """

    fake = False

    def __init__(self, host='192.168.10.1', port=8889, bind='0.0.0.0', local_port=8889):
        # Numeric IPv4 only: DNS lookup must not enter a safety callback.
        socket.inet_aton(host)
        socket.inet_aton(bind)
        if not 1 <= port <= 65535 or not 0 <= local_port <= 65535:
            raise ValueError('Invalid UDP port')
        self.peer, self.local = (host, port), (bind, local_port)
        self.socket = None
        self.connected = False
        self.events = deque(maxlen=2048)
        self.events_omitted = 0
        self.lock = OwnerLock('hardware:' + host)

    def record(self, event):
        if len(self.events) == self.events.maxlen:
            self.events_omitted += 1
        self.events.append(dict(fake=False, physical_outcome='UNKNOWN', **event))

    def start(self):
        if self.socket is not None:
            raise RuntimeError('Already open')
        self.lock.acquire()
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.setblocking(False)
            self.socket.bind(self.local)
            self.connected = True
        except BaseException:
            self.close()
            raise

    def _send(self, command, mono_ns):
        event = {'attempt': command, 'mono_ns': mono_ns, 'acknowledgement': None}
        try:
            if self.socket is None:
                raise OSError('Transport not explicitly opened')
            count = self.socket.sendto(command.encode('ascii'), self.peer)
            event['os_send_result'] = count
            if count != len(command):
                raise OSError('Incomplete UDP send')
        except OSError as error:
            event['error'] = type(error).__name__
            self.record(event)
            raise
        self.record(event)

    def send_rc(self, lateral, vertical, mono_ns):
        self._send(f'rc {sdk_integer(lateral)} 0 {sdk_integer(vertical)} 0', mono_ns)

    def send_land(self, mono_ns):
        self._send('land', mono_ns)

    def poll(self, mono_ns):
        if self.socket is None:
            return
        for _ in range(64):
            try:
                data, peer = self.socket.recvfrom(2048)
            except BlockingIOError:
                break
            if peer == self.peer:
                self.record({'reply': data.decode('ascii', errors='replace'),
                             'mono_ns': mono_ns, 'acknowledgement': 'UNCORRELATED_SDK_REPLY'})

    def close(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None
        self.connected = False
        self.lock.close()


class DiagnosticChannel:
    """Positive allowlist enforced before delegating to a socket or fake."""

    def __init__(self, transport):
        self.transport = transport

    def send(self, command, mono_ns):
        if command not in DIAGNOSTIC_COMMANDS:
            raise ValueError('Diagnostic command is not allowlisted')
        self.transport._send(command, mono_ns)

    def close(self):
        # Socket close has no SDK/flight side effects.
        self.transport.close()
