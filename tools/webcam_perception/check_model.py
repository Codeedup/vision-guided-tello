"""Explicit model runtime check; no camera or network access."""
from model_asset import verified_model


def main():
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    options = vision.HandLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_buffer=verified_model()),
        running_mode=vision.RunningMode.VIDEO, num_hands=2)
    with vision.HandLandmarker.create_from_options(options):
        print('Hand model loaded successfully.')


if __name__ == '__main__':
    main()
