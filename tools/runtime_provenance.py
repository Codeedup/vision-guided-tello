"""Record the installed files actually used by a running FAKE ROS launch."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


NODES = (
    ('tello_bridge', 'ros_hand_receiver.py', True),
    ('tracking_controller', 'tracking_controller_node', False),
    ('mission_manager', 'mission_manager_node', False),
    ('tello_bridge', 'tello_bridge_node', True),
)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=5)
    if result.returncode:
        raise RuntimeError('Failed provenance command: ' + ' '.join(args))
    return result.stdout.strip()


def process_snapshot(pid, proc_root=Path('/proc')):
    directory = proc_root / str(pid)
    command_line = [part.decode('utf-8', errors='replace') for part in
                    directory.joinpath('cmdline').read_bytes().split(b'\0') if part]
    parent = parent_pid(pid, proc_root)
    executable = os.readlink(directory / 'exe')
    return {'pid': pid, 'ppid': parent, 'argv': command_line,
            'process_exe': executable, 'process_exe_sha256': sha256(directory / 'exe')}


def parent_pid(pid, proc_root=Path('/proc')):
    status = (proc_root / str(pid) / 'status').read_text()
    return next(int(line.split(':', 1)[1].strip()) for line in status.splitlines()
                if line.startswith('PPid:'))


def match_nodes(children, expected):
    """Require one direct launch child at each expected installed node path."""
    errors, matched = [], []
    for package, name, python_script in NODES:
        path = expected[(package, name)]
        if python_script:
            candidates = [child for child in children
                          if len(child['argv']) >= 2 and child['argv'][1] == str(path)]
        else:
            candidates = [child for child in children
                          if child['argv'] and child['argv'][0] == str(path)]
        if len(candidates) != 1:
            errors.append(f'Expected one running installed {package}/{name}; '
                          f'found {len(candidates)}')
            continue
        child = candidates[0]
        if not python_script and child['process_exe'] != str(path.resolve()):
            errors.append(f'Running executable differs from installed {package}/{name}')
            continue
        child = dict(child, package=package, node=name,
                     installed_path=str(path), installed_sha256=sha256(path))
        if not python_script and child['process_exe_sha256'] != child['installed_sha256']:
            errors.append(f'Running executable hash differs: {package}/{name}')
            continue
        matched.append(child)
    return matched, errors


def capture(launch_pid, workspace):
    workspace = Path(workspace).resolve()
    errors = []
    revision = command('git', '-C', str(workspace), 'rev-parse', 'HEAD')
    dirty = bool(command('git', '-C', str(workspace), 'status', '--porcelain'))
    if dirty:
        errors.append('Checkout has uncommitted changes')
    prefixes = {}
    expected = {}
    for package, name, _ in NODES:
        if package not in prefixes:
            prefix = Path(command('ros2', 'pkg', 'prefix', package)).resolve()
            intended = (workspace / 'install' / package).resolve()
            prefixes[package] = str(prefix)
            if prefix != intended:
                errors.append(f'ROS package prefix differs from this build: {package}')
        expected[(package, name)] = Path(prefixes[package]) / 'lib' / package / name
    children = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            if parent_pid(int(entry.name)) != launch_pid:
                continue
            child = process_snapshot(int(entry.name))
        except (OSError, StopIteration, ValueError):
            continue  # Process exited or is not inspectable; required nodes fail below.
        children.append(child)
    matched, node_errors = match_nodes(children, expected)
    errors.extend(node_errors)
    return {'schema': 'fake_ros_runtime_provenance', 'revision': revision,
            'working_tree_dirty': dirty, 'workspace': str(workspace),
            'captured_at_ns': time.time_ns(), 'launch_pid': launch_pid,
            'package_prefixes': prefixes, 'nodes': matched,
            'passed': not errors, 'errors': errors,
            'limitations': ['Python script bytes are hashed on disk at capture time; '
                            'the running interpreter cannot prove previously loaded bytes.']}


def write_new(path, report):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
