#!/usr/bin/env python3
"""
Explicit recorded/Tello decoder -> shared perception -> observation UDP.

Does not initialize SDK mode or control the aircraft. Video must be enabled via
an explicit diagnostic. Imports and argument parsing do not open a stream.
"""
import argparse
import json
import socket
import time

from frame_sources import DecoderSource
from perception import target_from_landmarks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, help='Recorded file or explicit udp:// URI')
    parser.add_argument('--hardware-video', action='store_true')
    parser.add_argument('--receiver', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=5005)
    parser.add_argument('--duration', type=float, default=60)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 0 < args.duration <= 600:
        parser.error('Invalid port or duration')
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    from model_asset import verified_model
    import numpy as np
    source = DecoderSource(args.input, hardware=args.hardware_video)
    options = vision.HandLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_buffer=verified_model()),
        running_mode=vision.RunningMode.VIDEO, num_hands=2)
    last_timestamp = -1
    started = time.monotonic()
    try:
        source.start()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            with vision.HandLandmarker.create_from_options(options) as detector:
                while time.monotonic() - started < args.duration:
                    frame = source.read()
                    if frame is None:
                        # Silence retains the safety chain's original source timeout.
                        time.sleep(0.005)
                        continue
                    timestamp = max(frame.monotonic_ns // 1_000_000, last_timestamp + 1)
                    last_timestamp = timestamp
                    image = mp.Image(image_format=mp.ImageFormat.SRGB,
                                     data=np.frombuffer(frame.rgb_bytes(), dtype=np.uint8).reshape(
                                         frame.height, frame.width, 3))
                    result = detector.detect_for_video(image, timestamp)
                    packet = target_from_landmarks(result.hand_landmarks, frame.width,
                                                   frame.height, frame.receipt_ns)
                    sender.sendto(json.dumps(packet, allow_nan=False).encode(),
                                  (args.receiver, args.port))
    except KeyboardInterrupt:
        pass
    finally:
        source.close()


if __name__ == '__main__':
    main()
