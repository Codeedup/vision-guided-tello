"""Grounded bench checks at the final SDK wire boundary."""

import json
import socket
import threading
import time
from unittest.mock import Mock, patch

from grounded_sdk_bench import GroundedBench, NEUTRAL, run_bench

import pytest

from review_grounded_sdk_bench import review

from tello_bridge.transport import UdpSdkTransport


class ReplyTransport:
    """Return scripted SDK replies without opening a socket."""

    fake = True
    connected = False
    events_omitted = 0

    def __init__(self, replies=None):
        """Load responses for sequential query attempts."""
        self.events = []
        self.replies = list(replies or [])
        self.closed = False

    def start(self):
        """Mark the simulated connection active."""
        self.connected = True

    def _send(self, command, mono_ns):
        """Record one simulated OS send."""
        self.events.append({'attempt': command, 'mono_ns': mono_ns,
                            'os_send_result': len(command),
                            'acknowledgement': None})

    def poll(self, mono_ns):
        """Release one scripted reply after its send."""
        if self.replies and self.events and self.events[-1].get('attempt'):
            self.events.append({'reply': self.replies.pop(0),
                                'reply_observed_ns': time.monotonic_ns(),
                                'acknowledgement': 'UNCORRELATED_SDK_REPLY'})

    def close(self):
        """Close the simulated transport."""
        self.closed = True
        self.connected = False


def decision(stamp, **changes):
    """Build one version-one disabled wire decision."""
    item = {'version': 1, 'decision_ns': stamp, 'source_ns': stamp,
            'enabled': False, 'tracking': False, 'land': False,
            'lateral': 0, 'vertical': 0}
    item.update(changes)
    return json.dumps(item).encode()


def test_input_freshness_and_nonzero_rejection_still_wire_neutral():
    """Reject movement and expired input at the final bench boundary."""
    transport = ReplyTransport()
    transport.start()
    bench = GroundedBench(transport)
    wall, mono = 1_800_000_000_000_000_000, 1_000_000_000
    assert bench.receive(decision(wall), wall, mono)
    assert bench.tick(mono) == 'FRESH_DISABLED_ZERO'
    assert not bench.receive(decision(wall + 1, enabled=True, lateral=5),
                             wall + 1, mono + 1)
    assert bench.tick(mono + 250_000_000) == 'STALE_OR_LOST_INPUT'
    assert not bench.receive(decision(wall - 1), wall + 250_000_000,
                             mono + 250_000_000)
    assert not bench.receive(decision(wall + 2, lateral=True),
                             wall + 2, mono + 2)
    assert [e['attempt'] for e in transport.events] == [NEUTRAL, NEUTRAL]
    with pytest.raises(ValueError):
        bench.send('rc 5 0 0 0', mono)
    with pytest.raises(ValueError):
        bench.send('land', mono)


def test_complete_bench_query_neutral_and_distinct_receipts():
    """Run query and neutral flow without conflating sends and replies."""
    transport = ReplyTransport(['ok', '80', '0'])
    listener = Mock()
    listener.recvfrom.side_effect = BlockingIOError
    report = run_bench(transport, listener, 0.06)
    assert report['outcome'] == 'COMPLETED'
    assert transport.closed
    attempts = [e['attempt'] for e in report['transport_events']
                if 'attempt' in e]
    assert attempts[:3] == ['command', 'battery?', 'height?']
    assert set(attempts) <= {'command', 'battery?', 'height?', NEUTRAL}
    assert attempts[-1] == NEUTRAL
    assert len([e for e in report['transport_events'] if 'reply' in e]) == 3
    assert all(e['correlation'] == 'UNVERIFIED' for e in report['events']
               if e['kind'] == 'reply_observed_after_send')
    assert any(e.get('state') == 'STALE_OR_LOST_INPUT'
               for e in report['events'])
    assert report['physical_outcome'] == 'UNKNOWN'
    # The fake reply labels are intentionally unsuitable as real SDK evidence.
    assert not review(report)['pass']


def test_no_sdk_reply_stops_bench_and_closes_after_neutral():
    """Stop on missing SDK entry reply and attempt final neutral."""
    transport = ReplyTransport()
    listener = Mock()
    with patch('grounded_sdk_bench.REPLY_TIMEOUT_NS', 5_000_000):
        report = run_bench(transport, listener, 0.01)
    assert report['outcome'].startswith('TimeoutError:')
    assert transport.closed
    assert [e['attempt'] for e in report['transport_events']
            if 'attempt' in e] == ['command', NEUTRAL]
    assert report['events'][-1]['kind'] == 'shutdown'


def test_reply_loss_during_session_keeps_neutral_cadence_then_stops():
    """Continue neutral during a missing periodic SDK reply."""
    transport = ReplyTransport(['ok', '80', '0'])
    listener = Mock()
    listener.recvfrom.side_effect = BlockingIOError
    with patch('grounded_sdk_bench.REPLY_TIMEOUT_NS', 20_000_000):
        report = run_bench(transport, listener, 1.2)
    assert report['outcome'].startswith('TimeoutError: SDK link reply lost')
    attempts = [e['attempt'] for e in report['transport_events']
                if 'attempt' in e]
    query = len(attempts) - 1 - attempts[::-1].index('battery?')
    assert attempts[query + 1:] and set(attempts[query + 1:]) == {NEUTRAL}
    assert report['events'][-1]['kind'] == 'shutdown'


def test_loopback_udp_wire_and_sdk_reply_are_separate():
    """Observe exact wire commands and uncorrelated replies over UDP."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(('127.0.0.1', 0))
        server.settimeout(0.1)
        wire = []
        stop = threading.Event()

        def respond():
            """Act as a minimal local SDK peer."""
            while not stop.is_set():
                try:
                    data, peer = server.recvfrom(128)
                except socket.timeout:
                    continue
                command = data.decode('ascii')
                wire.append(command)
                if command in ('command', 'battery?', 'height?'):
                    server.sendto({'command': b'ok', 'battery?': b'80',
                                   'height?': b'0'}[command], peer)

        thread = threading.Thread(target=respond, daemon=True)
        thread.start()
        try:
            transport = UdpSdkTransport('127.0.0.1', server.getsockname()[1],
                                        '127.0.0.1', 0)
            listener = Mock()
            listener.recvfrom.side_effect = BlockingIOError
            report = run_bench(transport, listener, 0.06)
        finally:
            stop.set()
            thread.join(timeout=1)
    assert report['outcome'] == 'COMPLETED'
    assert set(wire) == {'command', 'battery?', 'height?', NEUTRAL}
    sends = [e for e in report['transport_events'] if 'attempt' in e]
    replies = [e for e in report['transport_events'] if 'reply' in e]
    assert all(e['acknowledgement'] is None and e['os_send_result'] > 0
               for e in sends)
    assert len(replies) == 3
    assert all(e['acknowledgement'] == 'UNCORRELATED_SDK_REPLY'
               for e in replies)
    assert not review(report)['pass']  # Loopback is not aircraft evidence.
    reviewed = dict(report, sdk_peer=['192.168.10.1', 8889],
                    local_endpoint=['192.168.10.2', 8889])
    assert review(reviewed)['pass']
