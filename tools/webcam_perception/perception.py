"""Shared webcam/recorded/Tello observation mapping; no detector dependency."""
from open_palm import is_open_palm


def target_from_landmarks(hands, width, height, capture_ns):
    count = len(hands)
    valid = count == 1 and is_open_palm(hands[0], width, height)
    x = y = 0.0
    if valid:
        x = 2 * sum(hands[0][i].x for i in (0, 5, 9, 13, 17)) / 5 - 1
        y = 2 * sum(hands[0][i].y for i in (0, 5, 9, 13, 17)) / 5 - 1
    return {'capture_time_ns': capture_ns, 'detected': valid, 'error_x': x, 'error_y': y,
            'tracked_hands': count}
