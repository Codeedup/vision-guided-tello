#!/usr/bin/env python3
"""Run a bounded propeller-off SDK bench with no nonzero RC command."""

import argparse
from collections import deque
import ipaddress
import json
import math
from pathlib import Path
import socket
import time

from tello_bridge.transport import UdpSdkTransport
from tello_bridge.windows_gateway import decode_packet


SAFE_QUERIES = ('command', 'battery?', 'height?')
NEUTRAL = 'rc 0 0 0 0'
SDK_HOST = '192.168.10.1'
INPUT_TIMEOUT_NS = 250_000_000
REPLY_TIMEOUT_NS = 2_000_000_000


class GroundedBench:
    """Accept only disabled, zero decisions; every RC output is neutral."""

    def __init__(self, transport):
        """Initialize a bench around one unopened SDK transport."""
        self.transport = transport
        self.events = deque(maxlen=4096)
        self.events_omitted = 0
        self.last_input_ns = None
        self.last_decision_ns = 0
        self.last_state = None
        self.closed = False

    def send(self, command, mono_ns):
        """Enforce the final positive wire allowlist, including zero RC."""
        if command not in SAFE_QUERIES and command != NEUTRAL:
            raise ValueError('Command outside grounded bench allowlist')
        self.transport._send(command, mono_ns)

    def record(self, kind, **values):
        """Retain one bounded local event."""
        if len(self.events) == self.events.maxlen:
            self.events_omitted += 1
        self.events.append(dict(kind=kind, mono_ns=time.monotonic_ns(),
                                **values))

    def receive(self, data, wall_ns=None, mono_ns=None):
        """Validate a disabled, zero decision without creating authority."""
        wall_ns = time.time_ns() if wall_ns is None else wall_ns
        mono_ns = time.monotonic_ns() if mono_ns is None else mono_ns
        try:
            decision = decode_packet(data)
            valid = (type(decision.lateral) in (int, float)
                     and type(decision.vertical) in (int, float)
                     and math.isfinite(decision.lateral)
                     and math.isfinite(decision.vertical)
                     and not decision.enabled and not decision.tracking
                     and not decision.land and decision.lateral == 0
                     and decision.vertical == 0
                     and decision.source_ns > 0
                     and decision.decision_ns > self.last_decision_ns
                     and 0 <= wall_ns - decision.decision_ns < INPUT_TIMEOUT_NS
                     and 0 <= wall_ns - decision.source_ns < INPUT_TIMEOUT_NS
                     and decision.source_ns <= decision.decision_ns)
        except (UnicodeError, ValueError, TypeError, KeyError):
            valid = False
            decision = None
        if valid:
            self.last_input_ns = mono_ns
            self.last_decision_ns = decision.decision_ns
        self.record('input', accepted=valid,
                    reason='DISABLED_ZERO' if valid else 'REJECTED')
        return valid

    def tick(self, mono_ns=None):
        """Send literal neutral and report input freshness."""
        mono_ns = time.monotonic_ns() if mono_ns is None else mono_ns
        state = ('FRESH_DISABLED_ZERO' if self.last_input_ns is not None
                 and 0 <= mono_ns - self.last_input_ns < INPUT_TIMEOUT_NS
                 else 'STALE_OR_LOST_INPUT')
        if state != self.last_state:
            self.record('input_state', state=state)
            self.last_state = state
        self.send(NEUTRAL, mono_ns)
        return state

    def close(self):
        """Attempt final neutral and release the SDK owner."""
        if not self.closed:
            try:
                if self.transport.connected:
                    self.tick()
            finally:
                self.transport.close()
                self.closed = True
                self.record('shutdown',
                            sdk_reply_confirms_physical_state=False)


def await_reply(bench, command):
    """Wait for a reply after sending, without claiming correlation."""
    bench.transport.poll(time.monotonic_ns())  # Drain earlier replies.
    started = time.monotonic_ns()
    bench.send(command, started)
    deadline = started + REPLY_TIMEOUT_NS
    while time.monotonic_ns() < deadline:
        bench.transport.poll(time.monotonic_ns())
        replies = [e for e in bench.transport.events
                   if e.get('reply_observed_ns', -1) >= started]
        if replies:
            return replies[-1]['reply'].strip()
        time.sleep(0.01)
    raise TimeoutError('No SDK reply observed after ' + command)


def run_bench(transport, listener, duration):
    """Run a bounded real or loopback bench and return trace data."""
    bench = GroundedBench(transport)
    outcome = 'ERROR'
    try:
        transport.start()
        for command in SAFE_QUERIES:
            reply = await_reply(bench, command)
            bench.record('reply_observed_after_send', command=command,
                         reply=reply, correlation='UNVERIFIED')
            if command == 'command' and reply.lower() != 'ok':
                raise RuntimeError('SDK entry did not receive ok')
            if command == 'battery?' and (not reply.isdecimal()
                                          or not 25 <= int(reply) <= 100):
                raise RuntimeError('Battery reply is low or invalid')
            if command == 'height?' and (not reply.isdecimal()
                                         or not 0 <= int(reply) <= 20):
                raise RuntimeError('Grounded height reply is invalid')
        deadline = time.monotonic() + duration
        next_send = 0.0
        next_query = time.monotonic() + 1.0
        pending_query_ns = None
        while time.monotonic() < deadline:
            for _ in range(32):
                try:
                    data, _ = listener.recvfrom(1024)
                except BlockingIOError:
                    break
                bench.receive(data)
            now = time.monotonic()
            if now >= next_send:
                bench.tick()
                next_send = now + 0.05
            transport.poll(time.monotonic_ns())
            if pending_query_ns is not None:
                replies = [e for e in transport.events
                           if e.get('reply_observed_ns', -1)
                           >= pending_query_ns]
                if replies:
                    reply = replies[-1]['reply'].strip()
                    bench.record('reply_observed_after_send',
                                 command='battery?',
                                 reply=reply, correlation='UNVERIFIED')
                    if not reply.isdecimal() or not 25 <= int(reply) <= 100:
                        raise RuntimeError('Battery reply is low or invalid')
                    pending_query_ns = None
                    next_query = now + 1.0
                elif (time.monotonic_ns() - pending_query_ns
                      >= REPLY_TIMEOUT_NS):
                    raise TimeoutError('SDK link reply lost after battery?')
            elif (now >= next_query
                  and now + REPLY_TIMEOUT_NS / 1e9 <= deadline):
                pending_query_ns = time.monotonic_ns()
                bench.send('battery?', pending_query_ns)
            time.sleep(0.005)
        if pending_query_ns is not None:
            raise TimeoutError('Unresolved SDK reply at bench deadline')
        outcome = 'COMPLETED'
    except (OSError, RuntimeError, TimeoutError, KeyboardInterrupt) as error:
        outcome = type(error).__name__ + ': ' + str(error)
        bench.record('fault', detail=outcome)
    finally:
        try:
            bench.close()
        except OSError as error:
            outcome = type(error).__name__ + ': ' + str(error)
            bench.record('shutdown_fault', detail=outcome)
    return {'mode': 'GROUNDED_REAL_SDK_BENCH',
            'fake': getattr(transport, 'fake', None), 'outcome': outcome,
            'sdk_peer': getattr(transport, 'peer', None),
            'local_endpoint': getattr(transport, 'local', None),
            'allowlist': list(SAFE_QUERIES) + [NEUTRAL],
            'events': list(bench.events),
            'events_omitted': bench.events_omitted,
            'transport_events': list(transport.events),
            'transport_events_omitted': transport.events_omitted,
            'physical_outcome': 'UNKNOWN'}


def main(argv=None):
    """Require physical assertions and write one nonoverwriting trace."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hardware', action='store_true', required=True,
                        help='Explicit real SDK opt-in')
    parser.add_argument('--propellers-removed', action='store_true',
                        required=True)
    parser.add_argument('--aircraft-grounded', action='store_true',
                        required=True)
    parser.add_argument('--bind', required=True, help='Explicit Windows IPv4')
    parser.add_argument('--input-port', type=int, default=5011)
    parser.add_argument('--duration', type=float, default=10)
    parser.add_argument('--trace', required=True,
                        help='New JSON path; never overwrite')
    args = parser.parse_args(argv)
    if not 1 <= args.input_port <= 65535 or not 1 <= args.duration <= 30:
        parser.error('input-port must be valid and duration in [1,30] seconds')
    try:
        address = ipaddress.IPv4Address(args.bind)
    except ipaddress.AddressValueError:
        parser.error('bind must be a numeric IPv4 address')
    if address not in ipaddress.IPv4Network('192.168.10.0/24') or address in (
            ipaddress.IPv4Address('192.168.10.0'),
            ipaddress.IPv4Address('192.168.10.1'),
            ipaddress.IPv4Address('192.168.10.255')):
        parser.error('bind must be the Windows Tello Wi-Fi IPv4 address')
    trace = Path(args.trace)
    with trace.open('x', encoding='utf-8') as output:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
            listener.setblocking(False)
            listener.bind((args.bind, args.input_port))
            transport = UdpSdkTransport(SDK_HOST, bind=args.bind)
            result = run_bench(transport, listener, args.duration)
        json.dump(result, output, indent=2)
        output.write('\n')
    print(json.dumps({'outcome': result['outcome'], 'trace': str(trace)}))
    return 0 if result['outcome'] == 'COMPLETED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
