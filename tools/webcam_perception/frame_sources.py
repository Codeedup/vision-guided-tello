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
import time


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


def decode_av(uri):
    """Yield genuine decoder events, never a library's cached .frame property."""
    import av
    with av.open(uri, timeout=(2.0, 0.2)) as stream:
        for sequence, image in enumerate(stream.decode(video=0)):
            receipt_ns, mono_ns = time.time_ns(), time.monotonic_ns()
            pixels = image.to_ndarray(format='bgr24')
            yield Frame(sequence, receipt_ns, mono_ns, image.width, image.height,
                        pixels.tobytes(), decoder_pts=image.pts)


def decoder_worker(uri, output, stop, decoder):
    try:
        for frame in decoder(uri):
            if stop.is_set():
                break
            try:
                output.put_nowait(frame)
            except queue.Full:
                try:
                    output.get_nowait()
                except queue.Empty:
                    pass
                try:
                    output.put_nowait(frame)
                except queue.Full:
                    pass
    except Exception as error:
        # Parent observes timeout even if a decoder fails without delivering this event.
        try:
            output.put_nowait(('decoder_error', type(error).__name__))
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
            from urllib.parse import urlparse
            host = urlparse(uri).hostname
            if not hardware and not ipaddress.ip_address(host).is_loopback:
                raise ValueError('Hardware video requires explicit hardware=True')
        elif not Path(uri).is_file():
            raise ValueError('Expected a recorded file or explicit UDP URI')
        self.uri, self.decoder = uri, decoder
        self.slot = LatestFrames(timeout_ns)
        self.process = self.output = self.stop = None
        self.last_error = None

    def start(self):
        context = multiprocessing.get_context('spawn')
        self.output, self.stop = context.Queue(maxsize=1), context.Event()
        self.process = context.Process(target=decoder_worker,
                                       args=(self.uri, self.output, self.stop, self.decoder),
                                       daemon=True)
        self.process.start()

    def read(self, now_ns=None):
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
