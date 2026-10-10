"""
Fresh decoded-frame identity, latest-only buffering, and lazy frame sources.

Receipt stamps are software timestamps, never camera exposure timestamps.
A new decode event supplies an identity; polling a cached image cannot mint one.
"""

from dataclasses import dataclass
import ipaddress
import multiprocessing
from pathlib import Path
import queue
import re
import socket
import time
from urllib.parse import urlparse


@dataclass(frozen=True)
class Frame:
    sequence: int
    receipt_ns: int
    monotonic_ns: int
    width: int
    height: int
    pixels: bytes
    colour: str = 'BGR'
    mirrored: bool = False
    decoder_pts: int | None = None

    def __post_init__(self):
        if (self.sequence < 0 or self.receipt_ns <= 0 or self.monotonic_ns < 0
                or self.width <= 0 or self.height <= 0
                or len(self.pixels) != self.width * self.height * 3
                or self.colour not in ('BGR', 'RGB') or self.mirrored):
            raise ValueError('Invalid frame or unsupported mirroring')

    def rgb_bytes(self):
        if self.colour == 'RGB':
            return self.pixels
        data = bytearray(self.pixels)
        data[0::3], data[2::3] = self.pixels[2::3], self.pixels[0::3]
        return bytes(data)

    def bgr_array(self):
        import numpy as np
        pixels = self.pixels if self.colour == 'BGR' else self.rgb_bytes_to_bgr()
        return np.frombuffer(pixels, dtype=np.uint8).reshape(self.height, self.width, 3)

    def rgb_bytes_to_bgr(self):
        data = bytearray(self.pixels)
        data[0::3], data[2::3] = self.pixels[2::3], self.pixels[0::3]
        return bytes(data)


class LatestFrames:
    def __init__(self, timeout_ns=250_000_000):
        if timeout_ns <= 0:
            raise ValueError('timeout must be positive')
        self.timeout_ns = timeout_ns
        self.frame = None
        self.high_water = -1
        self.consumed = -1
        self.replaced = 0

    def offer(self, frame):
        if frame.sequence <= self.high_water:
            return False
        if self.frame is not None and self.frame.sequence > self.consumed:
            self.replaced += 1
        self.frame = frame
        self.high_water = frame.sequence
        return True

    def read(self, now_ns=None):
        now_ns = time.monotonic_ns() if now_ns is None else now_ns
        frame = self.frame
        if (frame is None or frame.sequence <= self.consumed or not
                0 <= now_ns - frame.monotonic_ns < self.timeout_ns):
            return None
        self.consumed = frame.sequence
        return frame


class WebcamSource:
    """Existing webcam path. Only explicit start() accesses a camera."""

    def __init__(self, index=0, factory=None):
        self.index, self.factory = index, factory
        self.camera = None
        self.sequence = 0

    def start(self):
        if self.factory is None:
            import cv2
            self.factory = cv2.VideoCapture
        self.camera = self.factory(self.index)
        if not self.camera.isOpened():
            self.close()
            raise RuntimeError('Could not open webcam')

    def read(self):
        success, image = self.camera.read()
        # Original contract: capture receipt is immediately after camera.read().
        receipt_ns, mono_ns = time.time_ns(), time.monotonic_ns()
        if not success:
            return None
        self.sequence += 1
        height, width = image.shape[:2]
        return Frame(self.sequence, receipt_ns, mono_ns, width, height, image.tobytes())

    def close(self):
        if self.camera is not None:
            self.camera.release()
            self.camera = None


class RecordedFrames:
    """Known-provenance fixture frames retain their original stamps and identities."""

    def __init__(self, frames):
        self.frames = iter(frames)
        self.slot = LatestFrames()

    def start(self):
        pass

    def read(self, now_ns=None):
        frame = next(self.frames, None)
        if frame is not None:
            self.slot.offer(frame)
        return self.slot.read(now_ns)

    def close(self):
        pass


def frame_from_av(sequence, image, report_status=None):
    receipt_ns, mono_ns = time.time_ns(), time.monotonic_ns()
    if sequence == 0 and report_status is not None:
        report_status('first_frame_decoded')
    pixels = image.to_ndarray(format='bgr24')
    return Frame(sequence, receipt_ns, mono_ns, image.width, image.height,
                 pixels.tobytes(), decoder_pts=image.pts)


def decode_udp_av(uri, av, report_status=None):
    """Feed raw UDP chunks to one persistent H.264 parser/decoder."""
    address = urlparse(uri)
    host = address.hostname
    port = 11111 if address.port is None else address.port
    family = socket.AF_INET6 if ipaddress.ip_address(host).version == 6 else socket.AF_INET
    if report_status is not None:
        report_status('creating_h264_decoder')
    av.logging.set_level(av.logging.ERROR)
    codec = av.CodecContext.create('h264', 'r')
    with socket.socket(family, socket.SOCK_DGRAM) as receiver:
        receiver.bind((host, port))
        # Initial data can take longer than subsequent packets after streamon.
        receiver.settimeout(2.0)
        if report_status is not None:
            report_status('video_socket_bound')
        sequence = 0
        first_packet = True
        last_decoded_at = None
        invalid_packets = 0
        last_invalid_error = None
        while True:
            if last_decoded_at is not None and time.monotonic() - last_decoded_at >= 2.0:
                raise RuntimeError('No decodable H.264 frame for 2 seconds; '
                                   'last packet error: ' + str(last_invalid_error))
            try:
                payload, _ = receiver.recvfrom(65535)
            except socket.timeout as error:
                raise TimeoutError('UDP video packet receipt timed out') from error
            if not payload:
                continue
            if first_packet:
                last_decoded_at = time.monotonic()
                receiver.settimeout(0.2)
                if report_status is not None:
                    report_status('first_video_packet')
                first_packet = False
            # A datagram is not necessarily a complete encoded frame. Keep parser
            # state across datagrams; only decoder outputs create Frame identities.
            for packet in codec.parse(payload):
                try:
                    # Capture per-packet logs rather than letting repeated corrupt
                    # packet messages flood the diagnostic terminal or IPC queue.
                    with av.logging.Capture(local=False) as logs:
                        images = codec.decode(packet)
                except av.InvalidDataError as error:
                    invalid_packets += 1
                    last_invalid_error = str(error)
                    if invalid_packets == 1 and report_status is not None:
                        packet_bytes = bytes(packet)
                        nal_types = sorted({m[1][0] & 31 for m in re.finditer(
                            b'\x00\x00\x01(.)', packet_bytes[:65536], re.DOTALL)})
                        report_status('invalid_h264_packet', error_type=type(error).__name__,
                                      message=str(error), packet_bytes=len(packet_bytes),
                                      nal_unit_types=nal_types,
                                      ffmpeg_logs=[str(log)[:1000] for log in logs[-4:]])
                    continue
                for image in images:
                    last_decoded_at = time.monotonic()
                    if sequence == 0 and invalid_packets and report_status is not None:
                        report_status('h264_recovered', invalid_packets=invalid_packets)
                    yield frame_from_av(sequence, image, report_status)
                    sequence += 1


def decode_av(uri, report_status=None):
    """Yield genuine decoder events, never a library's cached .frame property."""
    if report_status is not None:
        report_status('importing_av')
    import av
    if uri.startswith('udp://'):
        yield from decode_udp_av(uri, av, report_status)
        return
    if report_status is not None:
        report_status('opening_video')
    with av.open(uri, timeout=(2.0, 0.2)) as stream:
        if report_status is not None:
            report_status('video_opened')
        for sequence, image in enumerate(stream.decode(video=0)):
            yield frame_from_av(sequence, image, report_status)


def decoder_worker(uri, output, stop, decoder, status_output=None):
    def report_status(stage, **details):
        if status_output is not None:
            try:
                status_output.put_nowait({'stage': stage, 'mono_ns': time.monotonic_ns(),
                                         **details})
            except queue.Full:
                pass

    report_status('worker_started')
    try:
        frames = (decoder(uri, report_status=report_status)
                  if decoder is decode_av else decoder(uri))
        first_queued = False
        for frame in frames:
            if stop.is_set():
                break
            try:
                output.put_nowait(frame)
                if not first_queued:
                    report_status('first_frame_queued')
                    first_queued = True
            except queue.Full:
                try:
                    output.get_nowait()
                except queue.Empty:
                    pass
                try:
                    output.put_nowait(frame)
                except queue.Full:
                    pass
        report_status('worker_stopped')
    except Exception as error:
        report_status('worker_error')
        # Parent observes timeout even if a decoder fails without delivering this event.
        try:
            output.put_nowait(('decoder_error', type(error).__name__, str(error)))
        except queue.Full:
            pass


class DecoderSource:
    """
    Proposed file/Tello PyAV source; blocking decode lives in a bounded worker.

    No worker is started at construction. Hardware UDP requires explicit opt-in.
    Process termination bounds shutdown even when the decoder gets stuck.
    """

    def __init__(self, uri, hardware=False, decoder=decode_av, timeout_ns=250_000_000):
        if uri.startswith('udp://'):
            host = urlparse(uri).hostname
            if not hardware and not ipaddress.ip_address(host).is_loopback:
                raise ValueError('Hardware video requires explicit hardware=True')
        elif not Path(uri).is_file():
            raise ValueError('Expected a recorded file or explicit UDP URI')
        self.uri, self.decoder = uri, decoder
        self.slot = LatestFrames(timeout_ns)
        self.process = self.output = self.stop = None
        self.status_output = None
        self.progress_events = []
        self.last_error = None

    def start(self):
        context = multiprocessing.get_context('spawn')
        self.output, self.stop = context.Queue(maxsize=1), context.Event()
        self.status_output = context.Queue(maxsize=16)
        self.process = context.Process(target=decoder_worker,
                                       args=(self.uri, self.output, self.stop, self.decoder,
                                             self.status_output),
                                       daemon=True)
        self.process.start()

    def read(self, now_ns=None):
        self.read_progress()
        # Max two dequeues bounds callback work even if producer is continuously active.
        for _ in range(2):
            try:
                frame = self.output.get_nowait()
            except queue.Empty:
                break
            if isinstance(frame, Frame):
                self.slot.offer(frame)
            else:
                self.last_error = frame
        return self.slot.read(now_ns)

    def read_progress(self):
        # Status is separate from the latest-frame slot; it never supplies a frame.
        if self.status_output is not None:
            for _ in range(16):
                try:
                    event = self.status_output.get_nowait()
                except queue.Empty:
                    break
                self.progress_events.append(event)
                self.progress_events = self.progress_events[-16:]
        return list(self.progress_events)

    def wait_for_video_socket(self, timeout=10, poll=None):
        """Standalone setup handshake; never call this from a safety callback."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.read()
            if self.last_error is not None:
                raise RuntimeError('Video receiver setup failed: ' + str(self.last_error))
            if any(e['stage'] == 'video_socket_bound' for e in self.progress_events):
                return
            if self.process.exitcode is not None:
                raise RuntimeError('Video receiver worker exited before binding')
            if poll is not None:
                poll()
            time.sleep(0.01)
        stages = [e['stage'] for e in self.progress_events]
        raise TimeoutError('Video socket setup timed out; progress: ' + str(stages))

    def close(self):
        if self.process is not None:
            self.stop.set()
            self.process.join(timeout=0.2)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=0.5)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=0.5)
            self.process = None
            self.output.cancel_join_thread()
            self.output.close()
            self.status_output.cancel_join_thread()
            self.status_output.close()
