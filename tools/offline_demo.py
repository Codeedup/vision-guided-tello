#!/usr/bin/env python3
"""
Repeatable synthetic ROS demonstration with the existing plant and FAKE bridge.

Only this explicitly invoked synthetic runner requests software autonomy. Normal
launches start disabled. No webcam, aircraft or physical transport is used.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=('static', 'loss',
                        'dropout', 'stale'), default='static')
    parser.add_argument('--duration', type=float, default=8)
    parser.add_argument('--output', type=Path, required=True, help='New artifact directory')
    args = parser.parse_args()
    if not 6 <= args.duration <= 120:
        parser.error('--duration must be in [6,120]')
    if args.output.exists():
        parser.error('--output already exists')
    args.output.mkdir(parents=True)
    namespace = '/offline_' + uuid.uuid4().hex[:10]
    children = []
    handles = []
    operator = None

    def start(command, log):
        handle = (args.output / log).open('x')
        handles.append(handle)
        child = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        children.append(child)
        return child

    def command(action, timeout=10):
        result = subprocess.run(['ros2', 'run', 'tello_bridge', 'tello_operator', action,
                                 '--namespace', namespace], capture_output=True, text=True,
                                timeout=timeout)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result.stdout

    try:
        launch = start(['ros2', 'launch', 'tello_bridge', 'offline.launch.py',
                        'namespace:=' + namespace, 'scenario:=' + args.scenario], 'nodes.log')
        deadline = time.monotonic() + 12
        while True:
            try:
                command('prepare-fake')
                break
            except RuntimeError:
                if launch.poll() is not None or time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        operator = start(['ros2', 'run', 'tello_bridge', 'tello_operator', 'enable',
                          '--namespace', namespace, '--duration', str(args.duration + 5),
                          '--reset-mock'],
                         'operator.log')
        ready_deadline = time.monotonic() + 20
        operator_log = args.output / 'operator.log'
        while 'Software session enabled;' not in operator_log.read_text():
            if operator.poll() is not None or time.monotonic() >= ready_deadline:
                raise RuntimeError('Operator did not enable; see operator.log')
            time.sleep(0.02)
        monitor = start([sys.executable, str(ROOT / 'tools/webcam_perception/measure_pipeline.py'),
                         '--namespace', namespace, '--duration', str(args.duration),
                         '--source', 'synthetic', '--run-kind',
                         'baseline' if args.scenario == 'static' else 'fault',
                         '--configuration', json.dumps({'scenario': args.scenario,
                                                        'transport': 'fake',
                                                        'plant': 'existing_image_plane_mock',
                                                        'monitor_start': 'after_enabled_marker'}),
                         '--output', str(args.output / 'report.json')], 'monitor.log')
        # Bounded polling keeps the parent responsive to Ctrl+C.
        deadline = time.monotonic() + args.duration + 8
        while monitor.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if monitor.poll() is None:
            raise RuntimeError('Monitor exceeded bounded run deadline')
        if monitor.returncode:
            raise RuntimeError('Monitor failed; see monitor.log')
        if operator.poll() not in (None, 0):
            raise RuntimeError('Operator session failed; see operator.log')
        report = json.loads((args.output / 'report.json').read_text())
        approved = [row for row in report['message_records'] if row['topic'] == 'approved_command']
        if args.scenario != 'stale' and not any(row['flags'][1] for row in approved):
            raise RuntimeError('Demo did not observe tracking; preserve logs and retry diagnosis')
        if args.scenario in ('loss', 'stale') and not any(row['flags'][2] for row in approved):
            raise RuntimeError('Demo did not observe expected landing intent; preserve logs')
        print(command('status'))
        summary = subprocess.run([
            sys.executable, str(ROOT / 'tools/webcam_perception/summarize_report.py'),
            str(args.output / 'report.json')], capture_output=True, text=True, check=True)
        (args.output / 'summary.json').write_text(summary.stdout)
        print('Synthetic ROS evidence written to ' + str(args.output))
    except KeyboardInterrupt:
        return 130
    finally:
        if operator is not None and operator.poll() is None:
            os.killpg(operator.pid, signal.SIGINT)
            try:
                operator.wait(timeout=4)
            except subprocess.TimeoutExpired:
                os.killpg(operator.pid, signal.SIGTERM)
        for child in reversed(children):
            if child.poll() is None:
                child.send_signal(signal.SIGINT)
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=2)
        for handle in handles:
            handle.close()
    return 0


if __name__ == '__main__':
    from domain_coordinator import domain_id
    with domain_id() as isolated_domain:
        os.environ['ROS_DOMAIN_ID'] = str(isolated_domain)
        os.environ['ROS_AUTOMATIC_DISCOVERY_RANGE'] = 'LOCALHOST'
        raise SystemExit(main())
