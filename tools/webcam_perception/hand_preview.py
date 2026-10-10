import argparse
import hashlib
import json
from pathlib import Path
import socket
import time

import cv2
from frame_sources import WebcamSource
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
from model_asset import verified_model
from perception import target_from_landmarks
from perception_timing import FrameTiming


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--receiver', required=True)
    parser.add_argument('--port', type=int, default=5005)
    parser.add_argument('--timing-trace', help='New bounded sender timing JSON; no overwrite')
    parser.add_argument('--trace-label', default='')
    parser.add_argument('--revision', default='UNRECORDED')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('Invalid UDP port')
    timing = None
    if args.timing_trace:
        timing = FrameTiming(args.timing_trace, metadata={
            'label': args.trace_label, 'revision': args.revision,
            'hand_preview_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'opencv': getattr(cv2, '__version__', 'UNKNOWN'),
            'mediapipe': getattr(mp, '__version__', 'UNKNOWN'),
            'receiver': args.receiver, 'port': args.port})
    completion = 'error'
    try:
        run_preview(args, timing)
        completion = 'quit'
    except KeyboardInterrupt:
        completion = 'interrupt'
        raise
    finally:
        if timing:
            timing.finish(completion)


def run_preview(args, timing=None):
    options = vision.HandLandmarkerOptions(
        base_options=python.BaseOptions(
            model_asset_buffer=verified_model()
        ),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=2,
    )

    source = WebcamSource()
    last_timestamp_ms = -1
    last_report_time = 0.0
    receiver_address = (args.receiver, args.port)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        source.start()

        with vision.HandLandmarker.create_from_options(options) as detector:
            print('Click the preview window and press Q to quit.')

            while True:
                marks = {}
                if timing:
                    marks['read_start_ns'] = time.monotonic_ns()
                sample = source.read()
                if timing:
                    marks['read_done_ns'] = time.monotonic_ns()
                if sample is None:
                    raise RuntimeError('Could not read a camera frame.')
                capture_time_ns = sample.receipt_ns
                frame = sample.bgr_array().copy()
                if timing:
                    marks['copy_done_ns'] = time.monotonic_ns()

                timestamp_ms = max(
                    time.monotonic_ns() // 1_000_000,
                    last_timestamp_ms + 1,
                )
                last_timestamp_ms = timestamp_ms

                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = mp.Image(
                    image_format=mp.ImageFormat.SRGB,
                    data=rgb_frame,
                )
                if timing:
                    marks['preprocess_done_ns'] = time.monotonic_ns()

                result = detector.detect_for_video(image, timestamp_ms)
                if timing:
                    marks['inference_done_ns'] = time.monotonic_ns()

                height, width = frame.shape[:2]

                for hand in result.hand_landmarks:
                    for landmark in hand:
                        x = int(landmark.x * width)
                        y = int(landmark.y * height)
                        cv2.circle(frame, (x, y), 4, (0, 255, 0), -1)

                hand_count = len(result.hand_landmarks)
                error_x = 0.0
                error_y = 0.0

                image_center = (width // 2, height // 2)

                cv2.drawMarker(
                    frame,
                    image_center,
                    (255, 255, 255),
                    cv2.MARKER_CROSS,
                    25,
                    2,
                )

                if hand_count == 1:
                    hand = result.hand_landmarks[0]
                    palm_indices = (0, 5, 9, 13, 17)

                    palm_x = sum(hand[i].x for i in palm_indices) / 5
                    palm_y = sum(hand[i].y for i in palm_indices) / 5

                    error_x = 2.0 * palm_x - 1.0
                    error_y = 2.0 * palm_y - 1.0

                    palm_pixel = (
                        int(palm_x * width),
                        int(palm_y * height),
                    )

                    cv2.circle(frame, palm_pixel, 8, (0, 165, 255), -1)
                    cv2.line(
                        frame,
                        image_center,
                        palm_pixel,
                        (0, 165, 255),
                        2,
                    )

                    status = f'error_x={error_x:+.2f} error_y={error_y:+.2f}'

                elif hand_count == 0:
                    status = 'No target'

                else:
                    status = 'Ambiguous: multiple hands'

                cv2.putText(
                    frame,
                    status,
                    (20, 70),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 165, 255),
                    2,
                )
                cv2.putText(
                    frame,
                    f'Hands detected: {hand_count}',
                    (20, 35),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (0, 255, 0),
                    2,
                )

                if timing:
                    marks['annotation_done_ns'] = time.monotonic_ns()
                observation = target_from_landmarks(
                    result.hand_landmarks, width, height, capture_time_ns)
                if timing:
                    marks['mapping_done_ns'] = time.monotonic_ns()

                packet = json.dumps(
                    observation,
                    allow_nan=False,
                ).encode('utf-8')
                if timing:
                    marks['encode_done_ns'] = time.monotonic_ns()

                sender.sendto(packet, receiver_address)
                if timing:
                    marks['send_done_ns'] = time.monotonic_ns()

                report_time = time.monotonic()

                if report_time - last_report_time >= 0.5:
                    print(json.dumps(observation, allow_nan=False))
                    last_report_time = report_time
                if timing:
                    marks['console_done_ns'] = time.monotonic_ns()

                cv2.imshow('Hand detection', frame)

                key = cv2.waitKey(1) & 0xFF
                if timing:
                    marks['preview_done_ns'] = time.monotonic_ns()
                    timing.record_frame(sample, observation, marks)
                if key == ord('q'):
                    break

    finally:
        sender.close()
        source.close()
        cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
