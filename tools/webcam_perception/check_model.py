from pathlib import Path

from mediapipe.tasks import python
from mediapipe.tasks.python import vision


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

with vision.HandLandmarker.create_from_options(options):
    print("Hand model loaded successfully.")