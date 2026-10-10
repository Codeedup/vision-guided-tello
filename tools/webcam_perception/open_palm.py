import math

# Provisional thresholds: validate on recorded and live examples.
MIN_JOINT_ANGLE_DEG = 150.0
MIN_THUMB_SPREAD_RATIO = 0.5

# Each chain runs from its base toward its fingertip.
FINGER_CHAINS = (
    (1, 2, 3, 4),       # Thumb
    (5, 6, 7, 8),       # Index
    (9, 10, 11, 12),    # Middle
    (13, 14, 15, 16),   # Ring
    (17, 18, 19, 20),   # Little finger
)


def joint_angle_deg(a, b, c):
    """Angle at b: a straight joint is approximately 180 degrees."""
    u = (a[0] - b[0], a[1] - b[1])
    v = (c[0] - b[0], c[1] - b[1])

    length_u = math.hypot(*u)
    length_v = math.hypot(*v)

    if length_u <= 1e-6 or length_v <= 1e-6:
        return 0.0

    cosine = (
        (u[0] * v[0] + u[1] * v[1])
        / (length_u * length_v)
    )
    cosine = max(-1.0, min(1.0, cosine))
    return math.degrees(math.acos(cosine))


def is_open_palm(landmarks, width, height):
    """Conservative 2D open-hand gate, not palm-facing recognition."""
    if len(landmarks) != 21 or width <= 0 or height <= 0:
        return False

    for landmark in landmarks:
        if not (
            math.isfinite(landmark.x)
            and math.isfinite(landmark.y)
            and 0.0 <= landmark.x <= 1.0
            and 0.0 <= landmark.y <= 1.0
        ):
            return False

    # Use pixel coordinates so image aspect ratio does not distort angles.
    points = [
        (landmark.x * width, landmark.y * height)
        for landmark in landmarks
    ]

    palm_width = math.dist(points[5], points[17])
    if palm_width <= 1e-6:
        return False

    for base, joint1, joint2, tip in FINGER_CHAINS:
        first_angle = joint_angle_deg(
            points[base], points[joint1], points[joint2]
        )
        second_angle = joint_angle_deg(
            points[joint1], points[joint2], points[tip]
        )

        if min(first_angle, second_angle) < MIN_JOINT_ANGLE_DEG:
            return False

        # Straight segments alone can also occur in a folded finger.
        if math.dist(points[tip], points[0]) <= math.dist(
            points[joint1], points[0]
        ):
            return False

    thumb_spread = math.dist(points[4], points[5]) / palm_width
    return thumb_spread >= MIN_THUMB_SPREAD_RATIO
