#!/usr/bin/env python3
"""Bounded local recording replay with the same detector/gate and optional preview."""

import argparse
import hashlib
import json
from pathlib import Path
import time

from bench_hand_preview import HandPreview
from frame_sources import decode_av
from perception_timing import distribution, software_identity


def run_replay(frames, preview, max_frames, max_seconds, clock=time.monotonic_ns):
    """Process frames sequentially, bounding retained records and elapsed work."""
    started = clock()
    records = []
    completion = 'end_of_file'
    for frame in frames:
        if (clock() - started) / 1e9 >= max_seconds:
            completion = 'duration_limit'
            break
        before = clock()
        keep_going = preview(frame)
        after = clock()
        records.append({'sequence': frame.sequence, 'width': frame.width, 'height': frame.height,
                        'inference_ns': preview.last_inference_ns, 'processing_ns': after - before,
                        'detected': preview.last_observation['detected'],
                        'tracked_hands': preview.last_observation['tracked_hands']})
        if not keep_going or len(records) >= max_frames:
            completion = 'quit' if not keep_going else 'frame_limit'
            break
    return {'completion': completion, 'frames_processed': len(records), 'frames': records,
            'summary_ms': {name: distribution(r[name + '_ns'] for r in records)
                           for name in ('inference', 'processing')},
            'limitations': ['Sequential local-file replay is not a live video/SDK timing test.',
                            'Compare the same input hash, frame count, model and host load.',
                            'Elapsed stages include scheduling and measurement overhead.',
                            'The first frames include detector startup/warmup.',
                            'Duration is checked between local decoder/inference calls.']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='Local video, maximum 256 MiB')
    parser.add_argument('--output', type=Path, required=True, help='New report; refuses overwrite')
    parser.add_argument('--preview', action='store_true', help='Enable the same GUI annotations')
    parser.add_argument('--max-frames', type=int, default=300)
    parser.add_argument('--max-seconds', type=int, default=60)
    parser.add_argument('--label', default='')
    args = parser.parse_args(argv)
    if (not args.input.is_file() or args.input.stat().st_size > 256 * 1024 * 1024
            or not 1 <= args.max_frames <= 1000 or not 1 <= args.max_seconds <= 60):
        parser.error('Require a bounded local file, 1..1000 frames and 1..60 seconds')
    with args.output.open('x', encoding='utf-8') as stream:
        import cv2
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision
        from model_asset import verified_model
        options = vision.HandLandmarkerOptions(
            base_options=python.BaseOptions(model_asset_buffer=verified_model()),
            running_mode=vision.RunningMode.VIDEO, num_hands=2)
        result = {'completion': 'error'}
        try:
            with vision.HandLandmarker.create_from_options(options) as detector:
                preview = HandPreview(cv2, mp, detector, show_preview=args.preview)
                result = run_replay(decode_av(str(args.input)), preview,
                                    args.max_frames, args.max_seconds)
        finally:
            result['metadata'] = dict(
                software_identity(), label=args.label, preview_enabled=args.preview,
                input_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
                opencv=cv2.__version__, mediapipe=mp.__version__, num_hands=2,
                maximum_frames=args.max_frames, maximum_seconds=args.max_seconds)
            json.dump(result, stream, allow_nan=False)
            stream.write('\n')
            if args.preview:
                cv2.destroyAllWindows()
    return 0 if result.get('frames_processed', 0) > 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
