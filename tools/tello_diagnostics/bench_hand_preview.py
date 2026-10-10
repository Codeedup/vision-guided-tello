#!/usr/bin/env python3
"""Propeller-off Tello hand preview using only the diagnostic SDK allowlist.

Displays actual decoded camera frames with the shared open-palm gate. No ROS or
observation UDP output, flight commands, or laptop webcam access.
"""
import argparse
import json
import math
import time

from diagnose import DecoderSource, run_diagnostic, StateReceiver, UdpSdkTransport
from perception import target_from_landmarks


class HandPreview:
    def __init__(self, cv2, mp, detector):
        self.cv2, self.mp, self.detector = cv2, mp, detector
        self.last_timestamp = -1
        self.last_report = 0.0
        self.frames = self.eligible_frames = 0
        self.last_observation = None

    def __call__(self, sample):
        cv2 = self.cv2
        frame = sample.bgr_array().copy()
        timestamp = max(sample.monotonic_ns // 1_000_000, self.last_timestamp + 1)
        self.last_timestamp = timestamp
        image = self.mp.Image(image_format=self.mp.ImageFormat.SRGB,
                              data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        result = self.detector.detect_for_video(image, timestamp)
        observation = target_from_landmarks(result.hand_landmarks, sample.width,
                                            sample.height, sample.receipt_ns)
        self.last_observation = observation
        self.frames += 1
        self.eligible_frames += int(observation['detected'])
        for hand in result.hand_landmarks:
            for point in hand:
                cv2.circle(frame, (int(point.x * sample.width), int(point.y * sample.height)),
                           4, (0, 255, 0), -1)
        centre = (sample.width // 2, sample.height // 2)
        cv2.drawMarker(frame, centre, (255, 255, 255), cv2.MARKER_CROSS, 25, 2)
        if observation['detected']:
            palm = (int((observation['error_x'] + 1) * sample.width / 2),
                    int((observation['error_y'] + 1) * sample.height / 2))
            cv2.circle(frame, palm, 8, (0, 165, 255), -1)
            cv2.line(frame, centre, palm, (0, 165, 255), 2)
        label = 'ELIGIBLE OPEN PALM' if observation['detected'] else 'NO ELIGIBLE TARGET'
        lines = [f"Hands: {observation['tracked_hands']}  {label}",
                 f"error_x={observation['error_x']:+.2f} error_y={observation['error_y']:+.2f}",
                 'Propellers removed | Q to quit']
        for index, text in enumerate(lines):
            cv2.putText(frame, text, (15, 30 + 30 * index), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (0, 255, 0), 2)
        now = time.monotonic()
        if now - self.last_report >= 0.5:
            print(json.dumps(observation, allow_nan=False))
            self.last_report = now
        cv2.imshow('Tello hand detection (propellers removed)', frame)
        return cv2.waitKey(1) & 0xFF != ord('q')

    def summary(self):
        return {'frames': self.frames, 'eligible_frames': self.eligible_frames,
                'ineligible_frames': self.frames - self.eligible_frames}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hardware', action='store_true')
    parser.add_argument('--host', default='192.168.10.1')
    parser.add_argument('--bind', default='0.0.0.0')
    parser.add_argument('--local-port', type=int, default=8889)
    parser.add_argument('--duration', type=float, default=30)
    args = parser.parse_args(argv)
    if not args.hardware:
        parser.error('Physical preview requires explicit --hardware')
    if not math.isfinite(args.duration) or not 0 < args.duration <= 60:
        parser.error('duration must be in (0,60]')
    import cv2
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    from model_asset import verified_model

    transport = UdpSdkTransport(args.host, bind=args.bind, local_port=args.local_port)
    state = StateReceiver(args.host, args.bind)
    options = vision.HandLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_buffer=verified_model()),
        running_mode=vision.RunningMode.VIDEO, num_hands=2)
    try:
        with vision.HandLandmarker.create_from_options(options) as detector:
            preview = HandPreview(cv2, mp, detector)
            print('Propeller-off camera/hand check. Click the preview and press Q to finish.')
            result = run_diagnostic(
                transport, duration=args.duration, video=True,
                decoder_factory=lambda: DecoderSource(f'udp://{args.bind}:11111', hardware=True),
                state_source=state, on_frame=preview)
            result['preview_summary'] = preview.summary()
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (OSError, RuntimeError, TimeoutError, KeyboardInterrupt) as error:
        print(json.dumps({'error': str(error), 'fake': False,
                          'events': list(transport.events)}, indent=2))
        return 2
    finally:
        cv2.destroyAllWindows()


if __name__ == '__main__':
    raise SystemExit(main())
