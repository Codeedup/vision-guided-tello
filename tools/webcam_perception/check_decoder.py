#!/usr/bin/env python3
"""Optional real PyAV test using author-created colour bars; no network/camera."""
from pathlib import Path
import tempfile
import time

from frame_sources import decode_av, DecoderSource


def main():
    import av
    import numpy as np
    with tempfile.TemporaryDirectory() as directory:
        fixture = Path(directory) / 'synthetic.mkv'
        pixels = np.array([[[255, 0, 0], [0, 0, 255]],
                           [[0, 255, 0], [255, 255, 255]]], dtype=np.uint8)
        with av.open(str(fixture), 'w') as container:
            stream = container.add_stream('ffv1', rate=10)
            stream.width, stream.height, stream.pix_fmt = 2, 2, 'bgr0'
            for _ in range(3):
                frame = av.VideoFrame.from_ndarray(pixels, format='rgb24')
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        frames = list(decode_av(str(fixture)))
        assert len(frames) == 3
        assert [f.sequence for f in frames] == [0, 1, 2]
        for frame in frames:
            assert frame.rgb_bytes() == pixels.tobytes()
            assert (frame.width, frame.height, frame.mirrored) == (2, 2, False)
        source = DecoderSource(str(fixture), timeout_ns=5_000_000_000)
        try:
            source.start()
            deadline = time.monotonic() + 5
            latest = None
            while latest is None and time.monotonic() < deadline:
                latest = source.read()
                time.sleep(0.01)
            assert latest is not None
            assert latest.rgb_bytes() == pixels.tobytes()
        finally:
            source.close()
    print(f'PyAV {av.__version__}: lossless synthetic decode, colours, identity, dimensions OK')


if __name__ == '__main__':
    main()
