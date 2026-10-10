#!/usr/bin/env python3
"""
Explicit SDK1.3 query/video diagnostic. Defaults to FAKE transport.

Positive allowlist excludes all flight/motor commands, including cleanup. This
program must not run concurrently with another aircraft command owner.
"""
import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/tello_bridge'))
sys.path.insert(0, str(ROOT / 'tools/webcam_perception'))
from tello_bridge.telemetry import StateReceiver  # noqa: E402
from tello_bridge.transport import (  # noqa: E402
    DiagnosticChannel,
    FakeTransport,
    UdpSdkTransport,
)


def run_diagnostic(transport, duration=2, video=False, decoder_factory=None, state_source=None):
    channel = DiagnosticChannel(transport)
    decoder = None
    stream_attempted = False
    last_frame_ns = None
    maximum_gap_ms = None
    result = {'fake': transport.fake, 'frames': 0, 'telemetry': None, 'physical_state': 'UNKNOWN',
              'exposure_timestamps_available': False}
    try:
        transport.start()
        if state_source:
            state_source.start()
        for command in ('command', 'battery?', 'height?', 'time?'):
            channel.send(command, time.monotonic_ns())
            started = time.monotonic()
            received = False
            while time.monotonic() - started < 2:
                transport.poll(time.monotonic_ns())
                received = any(event.get('reply') is not None and
                               event.get('mono_ns', 0) >= int(started * 1e9)
                               for event in transport.events)
                if received:
                    break
                time.sleep(0.01)
            if not received:
                raise TimeoutError('No SDK reply to ' + command)
            if command == 'command' and transport.events[-1].get('reply') != 'ok':
                raise RuntimeError('SDK initialization was not acknowledged')
            # This cadence is a provisional host limit, not a measured aircraft limit.
            time.sleep(0.1)
        if video:
            stream_attempted = True
            channel.send('streamon', time.monotonic_ns())
            if decoder_factory:
                decoder = decoder_factory()
                decoder.start()
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            transport.poll(time.monotonic_ns())
            mono_ns = time.monotonic_ns()
            if state_source is not None:
                telemetry = state_source.read(mono_ns)
                if telemetry is not None:
                    result['telemetry'] = asdict(telemetry)
            if decoder is not None and decoder.read() is not None:
                result['frames'] += 1
                if last_frame_ns is not None:
                    gap = (mono_ns - last_frame_ns) / 1e6
                    maximum_gap_ms = max(maximum_gap_ms or 0, gap)
                last_frame_ns = mono_ns
            time.sleep(0.01)
    finally:
        try:
            if decoder:
                decoder.close()
        finally:
            try:
                if stream_attempted:
                    try:
                        channel.send('streamoff', time.monotonic_ns())
                    except OSError:
                        pass
            finally:
                try:
                    if state_source:
                        state_source.close()
                finally:
                    channel.close()
    result['maximum_frame_receipt_gap_ms'] = maximum_gap_ms
    result['terminal_frame_silence_ms'] = (None if last_frame_ns is None else
                                           (time.monotonic_ns() - last_frame_ns) / 1e6)
    result['telemetry_age_ms_at_finish'] = (
        None if result['telemetry'] is None else
        (time.monotonic_ns() - result['telemetry']['receipt_ns']) / 1e6)
    result['events'] = list(transport.events)
    result['events_omitted'] = transport.events_omitted
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hardware', action='store_true', help='Explicit physical SDK connection')
    parser.add_argument('--host', default='192.168.10.1')
    parser.add_argument('--bind', default='0.0.0.0')
    parser.add_argument('--local-port', type=int, default=8889)
    parser.add_argument('--duration', type=float, default=2)
    parser.add_argument('--video', action='store_true')
    args = parser.parse_args(argv)
    if not math.isfinite(args.duration) or not 0 < args.duration <= 60:
        parser.error('duration must be in (0,60]')
    transport = (UdpSdkTransport(args.host, bind=args.bind, local_port=args.local_port)
                 if args.hardware else FakeTransport())
    state_source = StateReceiver(args.host, args.bind) if args.hardware else None
    decoder_factory = None
    if args.video and args.hardware:
        from frame_sources import DecoderSource

        def hardware_decoder():
            return DecoderSource(f'udp://{args.bind}:11111', hardware=True)
        decoder_factory = hardware_decoder
    try:
        result = run_diagnostic(transport, args.duration, args.video,
                                decoder_factory, state_source)
    except (OSError, RuntimeError, TimeoutError, KeyboardInterrupt) as error:
        print(json.dumps({'error': str(error), 'fake': transport.fake,
                          'events': list(transport.events)}, indent=2))
        return 2
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
