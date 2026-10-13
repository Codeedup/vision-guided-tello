"""Windows-side bounded clock probe; run with pipeline nodes stopped."""

import argparse
import json
from pathlib import Path
import queue
import subprocess
import threading
import time

PROBE_CODE = """
import sys
import time
print('READY', flush=True)
for line in sys.stdin:
    print(time.time_ns(), flush=True)
"""


def measure(process, lines, clock=time.time_ns):
    """Return the narrowest observed offset interval; never assume an old offset."""
    def read():
        try:
            line = lines.get(timeout=10)
        except queue.Empty:
            raise TimeoutError('WSL clock probe response timed out') from None
        if not line:
            raise RuntimeError('WSL clock probe closed its output')
        return line.strip()

    if read() != 'READY':
        raise RuntimeError('WSL clock probe did not start')
    samples = []
    for _ in range(10):
        before = clock()
        process.stdin.write('sample\n')
        process.stdin.flush()
        remote = int(read())
        after = clock()
        if after < before:
            raise RuntimeError('Windows clock moved backwards during probe')
        samples.append({'lower_ms': (remote - after) / 1e6,
                        'upper_ms': (remote - before) / 1e6,
                        'round_trip_ms': (after - before) / 1e6})
    return min(samples, key=lambda item: item['round_trip_ms'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='New clock evidence JSON; refuses overwrite')
    parser.add_argument('--label', default='', help='Shared run ID')
    args = parser.parse_args(argv)
    stream = None if args.output is None else args.output.open('x', encoding='utf-8')
    process = None
    try:
        process = subprocess.Popen(
            ['wsl.exe', '-d', 'Ubuntu', '--', 'python3', '-u', '-c', PROBE_CODE],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        lines = queue.Queue(maxsize=32)

        def collect():
            for line in process.stdout:
                try:
                    lines.put_nowait(line)
                except queue.Full:
                    return
            try:
                lines.put_nowait('')
            except queue.Full:
                pass

        reader = threading.Thread(target=collect, daemon=True)
        reader.start()
        result = dict(measure(process, lines), schema='windows_wsl_clock_bounds',
                      label=args.label, checked_at_ns=time.time_ns(), samples=10)
        print('WSL clock minus Windows clock is between '
              f"{result['lower_ms']:+.3f} and {result['upper_ms']:+.3f} ms")
        if stream:
            json.dump(result, stream, allow_nan=False)
            stream.write('\n')
    finally:
        try:
            if process is not None:
                process.stdin.close()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                process.stdout.close()
        finally:
            if stream:
                stream.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
