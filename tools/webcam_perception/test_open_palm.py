import math
from types import SimpleNamespace
import unittest

from open_palm import is_open_palm

WIDTH = 640
HEIGHT = 480

# Synthetic open hand in MediaPipe landmark order.
OPEN_HAND = (
    (0.50, 0.80),
    (0.45, 0.65), (0.35, 0.60), (0.25, 0.55), (0.15, 0.50),
    (0.38, 0.55), (0.37, 0.40), (0.36, 0.25), (0.35, 0.10),
    (0.50, 0.50), (0.50, 0.35), (0.50, 0.20), (0.50, 0.05),
    (0.62, 0.55), (0.63, 0.40), (0.64, 0.25), (0.65, 0.10),
    (0.74, 0.65), (0.76, 0.53), (0.78, 0.41), (0.80, 0.29),
)


def eligible(points):
    landmarks = [
        SimpleNamespace(x=x, y=y)
        for x, y in points
    ]
    return is_open_palm(landmarks, WIDTH, HEIGHT)


class TestOpenPalm(unittest.TestCase):
    def test_open_hand_is_eligible(self):
        self.assertTrue(eligible(OPEN_HAND))

    def test_each_curled_finger_is_rejected(self):
        for tip, base in (
            (4, 1), (8, 5), (12, 9), (16, 13), (20, 17)
        ):
            with self.subTest(tip=tip):
                points = list(OPEN_HAND)
                points[tip] = (points[base][0], 0.70)
                self.assertFalse(eligible(points))

    def test_straight_but_tucked_thumb_is_rejected(self):
        points = list(OPEN_HAND)
        points[1:5] = [
            (0.44, 0.66), (0.42, 0.56),
            (0.40, 0.46), (0.38, 0.36),
        ]
        self.assertFalse(eligible(points))

    def test_rotation_and_mirroring_preserve_eligibility(self):
        mirrored = [(1.0 - x, y) for x, y in OPEN_HAND]
        self.assertTrue(eligible(mirrored))

        for degrees in (-90, -45, 45, 90):
            with self.subTest(degrees=degrees):
                angle = math.radians(degrees)
                rotated = []

                for x, y in OPEN_HAND:
                    dx = (x - 0.5) * WIDTH
                    dy = (y - 0.5) * HEIGHT

                    rotated.append((
                        0.5 + (
                            dx * math.cos(angle)
                            - dy * math.sin(angle)
                        ) / WIDTH,
                        0.5 + (
                            dx * math.sin(angle)
                            + dy * math.cos(angle)
                        ) / HEIGHT,
                    ))

                self.assertTrue(eligible(rotated))

    def test_invalid_coordinates_are_rejected(self):
        for coordinate in (
            (float('nan'), 0.5),
            (0.5, float('inf')),
            (-0.01, 0.5),
            (0.5, 1.01),
        ):
            with self.subTest(coordinate=coordinate):
                points = list(OPEN_HAND)
                points[8] = coordinate
                self.assertFalse(eligible(points))

    def test_missing_or_collapsed_landmarks_are_rejected(self):
        self.assertFalse(eligible(OPEN_HAND[:-1]))
        self.assertFalse(eligible([(0.5, 0.5)] * 21))


if __name__ == '__main__':
    unittest.main()
