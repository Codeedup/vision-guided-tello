#!/usr/bin/env python3
"""Check/fetch the expected model bytes without importing MediaPipe."""
import argparse
import hashlib
from pathlib import Path
import urllib.request

MODEL_SHA256 = 'fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1'
MODEL_URL = ('https://storage.googleapis.com/mediapipe-models/hand_landmarker/'
             'hand_landmarker/float16/latest/hand_landmarker.task')
MODEL_PATH = Path(__file__).resolve().parent / 'models/hand_landmarker.task'


def verified_model(path=MODEL_PATH):
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
        raise ValueError('Model hash differs from reviewed checkpoint')
    return data


def fetch_model(path=MODEL_PATH, opener=urllib.request.urlopen):
    path = Path(path)
    if path.exists():
        verified_model(path)
        return
    with opener(MODEL_URL, timeout=30) as response:
        data = response.read(16 * 1024 * 1024 + 1)
    if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
        raise ValueError('Downloaded model hash mismatch; no file written')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--path', type=Path, default=MODEL_PATH)
    args = parser.parse_args()
    if args.fetch:
        fetch_model(args.path)
    data = verified_model(args.path)
    print(f'Verified SHA-256 {MODEL_SHA256}, {len(data)} bytes: {args.path}')


if __name__ == '__main__':
    main()
