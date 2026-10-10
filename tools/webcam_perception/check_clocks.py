import subprocess
import time

probe_code = """
import sys
import time

print("READY", flush=True)

for line in sys.stdin:
    print(time.time_ns(), flush=True)
"""

process = subprocess.Popen(
    ['wsl.exe', '-d', 'Ubuntu', '--', 'python3', '-u', '-c', probe_code],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    text=True,
)

try:
    if process.stdout.readline().strip() != 'READY':
        raise RuntimeError('WSL clock probe did not start.')

    samples = []

    for _ in range(10):
        windows_before = time.time_ns()

        process.stdin.write('sample\n')
        process.stdin.flush()

        wsl_time = int(process.stdout.readline().strip())
        windows_after = time.time_ns()

        lower_ms = (wsl_time - windows_after) / 1_000_000
        upper_ms = (wsl_time - windows_before) / 1_000_000

        samples.append((upper_ms - lower_ms, lower_ms, upper_ms))

    _, lower_ms, upper_ms = min(samples)

    print(
        'WSL clock minus Windows clock is between '
        f'{lower_ms:+.3f} and {upper_ms:+.3f} ms'
    )

finally:
    process.stdin.close()
    process.wait(timeout=5)
