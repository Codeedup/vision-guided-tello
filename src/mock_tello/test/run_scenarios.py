"""Reproducible Python plant + actual C++ pure policies, without ROS transport.

The small driver schedules policy calls; ROS wrappers/watchdog threads/DDS are
exercised separately by test_closed_loop_launch.py, not by this experiment.
"""

import argparse
import csv
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
from mock_tello.model import Config, Decision, ImagePlane, scenario_input  # noqa: E402


class PolicyDriver:
    def __init__(self, executable):
        self.process = subprocess.Popen([str(executable)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, text=True, bufsize=1)

    def step(self, now, action=0, observation=None):
        values = (0, 0, 0, 0, 0) if observation is None else (
            observation.stamp_ns, int(observation.detected), observation.tracked_hands,
            observation.error_x, observation.error_y)
        self.process.stdin.write(' '.join(map(str, (now, action, *values))) + '\n')
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError('C++ policy driver exited unexpectedly')
        enabled, tracking, land, lateral, vertical, source = line.split()
        return Decision(round(now * 1e9), int(source), bool(int(enabled)),
                        bool(int(tracking)), bool(int(land)), float(lateral), float(vertical))

    def close(self):
        self.process.stdin.close()
        self.process.stdout.close()
        try:
            code = self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            raise
        if code:
            raise RuntimeError('C++ driver failed: ' + str(code))


def compile_driver(destination):
    sources = PACKAGE.parent
    subprocess.run([
        'g++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-O2',
        '-I' + str(sources / 'tracking_controller/include'),
        '-I' + str(sources / 'mission_manager/include'),
        str(PACKAGE / 'test/policy_driver.cpp'), '-o', str(destination),
    ], check=True)


def experiment(executable, name, config, scenario='static', duration=8,
               fault_start=1.0, fault_duration=0.6, supervisor_stall=False):
    model = ImagePlane(config)
    driver = PolicyDriver(executable)
    rows, tracking_entries, neutral_after_fault, landing_times = [], [], [], []
    was_tracking = False
    epoch = 100.0
    heartbeat_sequence = 0

    def accept(decision, now, elapsed):
        nonlocal heartbeat_sequence
        # Unique publication nanoseconds when events share a scheduler instant.
        heartbeat_sequence += 1
        now_ns = round(now * 1e9) + heartbeat_sequence
        decision = replace(decision, stamp_ns=now_ns)
        model.gate.accept(decision, now_ns, elapsed)

    try:
        for tick in range(round(duration / 0.01) + 1):
            elapsed = tick * 0.01
            now = round((epoch + elapsed) * 1e9) / 1e9
            now_ns = round(now * 1e9) + heartbeat_sequence
            vx, vy, mode = scenario_input(scenario, elapsed, fault_start, fault_duration)
            if tick:
                model.advance(0.01, now_ns, elapsed, vx, vy)
            if tick % 5 == 0:
                model.capture(round(now * 1e9), elapsed, mode)
            observations = model.deliver(elapsed)
            stalled = supervisor_stall and fault_start <= elapsed < fault_start + 1
            decision = None
            if not stalled:
                if tick == 0:
                    decision = driver.step(now, 2)
                    accept(decision, now, elapsed)
                for observation in observations:
                    decision = driver.step(now, 1, observation)
                    accept(decision, now, elapsed)
                if tick % 2 == 0:
                    decision = driver.step(now)
                    accept(decision, now, elapsed)
            if decision is not None:
                if decision.tracking_allowed and not was_tracking:
                    tracking_entries.append(elapsed)
                if not decision.tracking_allowed and elapsed >= fault_start:
                    neutral_after_fault.append(elapsed)
                if decision.request_land:
                    landing_times.append(elapsed)
                was_tracking = decision.tracking_allowed
            state = model.state(elapsed)
            rows.append(state)
    finally:
        driver.close()
    tail = rows[-100:]
    static_settled = None
    for index in range(len(rows) - 99):
        if all(abs(row['error_x']) <= 0.08 and abs(row['error_y']) <= 0.08
               for row in rows[index:]):
            static_settled = rows[index]['elapsed']
            break
    metrics = dict(
        name=name, scenario=scenario, config=vars(config), duration_seconds=duration,
        fault_start_seconds=fault_start, fault_duration_seconds=fault_duration,
        final_error_x=model.x, final_error_y=model.y,
        tail_max_abs_x=max(abs(row['error_x']) for row in tail),
        tail_max_abs_y=max(abs(row['error_y']) for row in tail),
        static_settled_seconds=static_settled, tracking_entries_seconds=tracking_entries,
        first_neutral_after_fault=next(iter(neutral_after_fault), None),
        first_landing_request=next(iter(landing_times), None), mock_terminal=model.gate.terminal,
        max_abs_scalar=max(max(abs(row['lateral']), abs(row['vertical'])) for row in rows),
        timeout_observed=any(row['consumer_mode'].endswith('TIMEOUT') for row in rows),
    )
    return metrics, rows


def suite(executable):
    cases = (
        ('horizontal', Config(initial_y=0), 'static', 0.6, False),
        ('vertical', Config(initial_x=0), 'static', 0.6, False),
        ('two_axes', Config(), 'static', 0.6, False),
        ('delay_noise', Config(sensor_delay=0.10, noise_stddev=0.004), 'static', 0.6, False),
        ('moving', Config(), 'moving', 0.6, False),
        ('dropout', Config(), 'dropout', 0.6, False),
        ('ambiguous', Config(), 'ambiguous', 0.6, False),
        ('silence', Config(), 'silence', 0.6, False),
        ('sustained_loss', Config(), 'loss', 2.5, False),
        ('source_stale', Config(sensor_delay=0.30), 'static', 0.6, False),
        ('supervisor_stall', Config(), 'static', 0.6, True),
    )
    return [experiment(executable, name, config, scenario, fault_duration=fault,
                       supervisor_stall=stall)
            for name, config, scenario, fault, stall in cases]


def verify(results):
    by_name = {metrics['name']: (metrics, rows) for metrics, rows in results}
    for name in ('horizontal', 'vertical', 'two_axes', 'delay_noise'):
        metrics, rows = by_name[name]
        assert metrics['tail_max_abs_x'] <= 0.09, metrics
        assert metrics['tail_max_abs_y'] <= 0.09, metrics
        assert metrics['static_settled_seconds'] is not None, metrics
        assert not metrics['mock_terminal'], metrics
        # No repeated crossings outside the dead zone after initial settling.
        for axis in ('error_x', 'error_y'):
            signs = [1 if row[axis] > 0.08 else -1 for row in rows[300:]
                     if abs(row[axis]) > 0.08]
            assert sum(a != b for a, b in zip(signs, signs[1:])) <= 1, metrics
    for name in ('dropout', 'ambiguous', 'silence'):
        metrics, _ = by_name[name]
        assert len(metrics['tracking_entries_seconds']) == 2, metrics
        assert metrics['tracking_entries_seconds'][1] >= 1.6, metrics
        assert metrics['first_neutral_after_fault'] is not None, metrics
        assert not metrics['mock_terminal'], metrics
    for name in ('sustained_loss', 'source_stale'):
        metrics, rows = by_name[name]
        assert metrics['mock_terminal'] and metrics['first_landing_request'] is not None, metrics
        if name == 'sustained_loss':
            assert 2.4 <= metrics['first_landing_request'] <= 2.8, metrics
        else:
            assert not metrics['tracking_entries_seconds'], metrics
        assert all(row['lateral'] == row['vertical'] == 0 for row in rows[-100:]), metrics
    moving, _ = by_name['moving']
    assert moving['tail_max_abs_x'] < 0.25 and moving['tail_max_abs_y'] < 0.25, moving
    assert not moving['mock_terminal'], moving
    stalled, _ = by_name['supervisor_stall']
    assert stalled['timeout_observed'] and not stalled['mock_terminal'], stalled
    assert all(metrics['max_abs_scalar'] <= 10 for metrics, _ in results)
    assert by_name['two_axes'][0]['max_abs_scalar'] == 10  # saturation exercised


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='New directory for metrics and CSV logs')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        executable = Path(temporary) / 'policy_driver'
        compile_driver(executable)
        results = suite(executable)
    verify(results)
    metrics = [metric for metric, _ in results]
    if args.output:
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / 'metrics.json').write_text(json.dumps(metrics, indent=2) + '\n')
        for metric, rows in results:
            with (args.output / (metric['name'] + '.csv')).open('w', newline='') as file:
                writer = csv.DictWriter(file, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
    print(json.dumps(metrics, indent=2))
    print('PASS: 11 deterministic policy/model scenarios (ROS transport not exercised).')


if __name__ == '__main__':
    main()
