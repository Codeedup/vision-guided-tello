from pathlib import Path
import time

import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import json
import math
import argparse
import socket



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--receiver", required=True)
    args = parser.parse_args()
    model_path = (
        Path(__file__).resolve().parent
        / "models"
        / "hand_landmarker.task"
    )

    options = vision.HandLandmarkerOptions(
        base_options=python.BaseOptions(
            model_asset_buffer=model_path.read_bytes()
        ),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=2,
    )

    camera = cv2.VideoCapture(0)
    last_timestamp_ms = -1
    last_report_time = 0.0
    receiver_address = (args.receiver, 5005)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    

    try:
        if not camera.isOpened():
            raise RuntimeError("Could not open the webcam.")

        with vision.HandLandmarker.create_from_options(options) as detector:
            print("Click the preview window and press Q to quit.")

            while True:
                success, frame = camera.read()

                if not success:
                    raise RuntimeError("Could not read a camera frame.")

                capture_time_ns = time.time_ns()

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

                result = detector.detect_for_video(image, timestamp_ms)

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

                    status = f"error_x={error_x:+.2f} error_y={error_y:+.2f}"

                elif hand_count == 0:
                    status = "No target"

                else:
                    status = "Ambiguous: multiple hands"

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
                    f"Hands detected: {hand_count}",
                    (20, 35),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (0, 255, 0),
                    2,
                )
                
                target_valid = (
                    hand_count == 1
                    and math.isfinite(error_x)
                    and math.isfinite(error_y)
                    and abs(error_x) <= 1.0
                    and abs(error_y) <= 1.0
                )

                observation = {
                    "capture_time_ns": capture_time_ns,
                    "detected": target_valid,
                    "error_x": error_x if target_valid else 0.0,
                    "error_y": error_y if target_valid else 0.0,
                    "tracked_hands": hand_count,
                }
                
                
                packet = json.dumps(
                    observation,
                    allow_nan=False,
                ).encode("utf-8")

                sender.sendto(packet, receiver_address)

                report_time = time.monotonic()

                if report_time - last_report_time >= 0.5:
                    print(json.dumps(observation, allow_nan=False))
                    last_report_time = report_time

                cv2.imshow("Hand detection", frame)

                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

    finally:
        sender.close()
        camera.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()