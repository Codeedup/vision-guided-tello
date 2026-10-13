#!/usr/bin/env python3
"""Validate one FAKE pipeline, own its process group, and preserve shutdown evidence."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def canonical_namespace(value):
    parts = value.strip('/').split('/')
    if not all(part and part.replace('_', 'a').isalnum() and
               not part[0].isdigit() and part.isascii() for part in parts):
        raise ValueError('Use a nonempty ROS namespace with letters, digits and underscores')
    return '/' + '/'.join(parts)


def acquire_lock(namespace):
    # Do not unlink: a second inode could otherwise permit concurrent owners.
    directory = Path(tempfile.gettempdir()) / ('tello-launcher-' + str(os.getuid()))
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid():
        raise RuntimeError('Unsafe launcher lock directory')
    path = directory / (hashlib.sha256(namespace.encode()).hexdigest() + '.lock')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    lock = os.fdopen(fd, 'r+')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError('Another launcher owns ' + namespace) from None
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()) + '\n')
    lock.flush()
    return lock


def check_port(bind, port):
    # This is a preflight probe, not a reservation. A later bind race is handled
    # by the launch file's required-process exit handlers and readiness check.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind((bind, port))


def build_launch_command(args):
    command = ['ros2', 'launch', 'tello_bridge', 'webcam.launch.py',
               'namespace:=' + args.namespace, 'bind:=' + args.bind,
               'port:=' + str(args.port)]
    for name, value in (('bridge_trace', args.bridge_trace), ('trace_label', args.trace_label)):
        if value:
            command.append(name + ':=' + value)
    return command


def group_alive(process):
    process.poll()  # Reap the group leader when it has exited.
    try:
        os.killpg(process.pid, 0)
        return True
    except ProcessLookupError:
        return False


def signal_group(process, signum):
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass


def stop_group(process, *, launch_noninteractive=False):
    # Jazzy launch forwards SIGINT itself in noninteractive mode. Sending it to
    # the group as well can interrupt child destruction with a second signal.
    # Interactive launch assumes terminal-wide delivery; generic helpers also
    # need the group signal. Escalation always reaches the entire owned group.
    for signum, grace in ((signal.SIGINT, 12), (signal.SIGTERM, 5), (signal.SIGKILL, 2)):
        if not group_alive(process):
            return True
        print('Stopping owned process group with ' + signal.Signals(signum).name, flush=True)
        if (launch_noninteractive and signum == signal.SIGINT
                and process.poll() is None):
            try:
                process.send_signal(signum)
            except ProcessLookupError:
                signal_group(process, signum)
        else:
            signal_group(process, signum)
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if not group_alive(process):
                return True
            time.sleep(0.05)
    return not group_alive(process)


def check_trace(path, namespace):
    if not path:
        return True
    try:
        with open(path, encoding='utf-8') as stream:
            data = json.load(stream)
        metadata = data.get('metadata', {})
        valid = (data.get('completion') == 'shutdown' and
                 metadata.get('role') == 'bridge' and metadata.get('fake') is True and
                 metadata.get('namespace') == namespace and data.get('events_retained', 0) > 0)
        if not valid:
            raise ValueError('missing matching FAKE bridge shutdown evidence')
    except (OSError, ValueError, TypeError, AttributeError) as error:
        print('TRACE NOT FINALIZED: ' + str(path) + ': ' + str(error), flush=True)
        return False
    print('Bridge trace finalized: ' + str(path), flush=True)
    return True


def run_check(namespace, mode, stopping, launch=None):
    check = subprocess.Popen([
        sys.executable, str(ROOT / 'tools/pipeline_diagnostics.py'),
        '--namespace', namespace, mode, '--skip-packages'], start_new_session=True)
    deadline = time.monotonic() + 25
    try:
        while check.poll() is None:
            if stopping() or (launch is not None and launch.poll() is not None):
                return False
            if time.monotonic() >= deadline:
                print('Pipeline diagnostic timed out.', flush=True)
                return False
            time.sleep(0.05)
        return check.returncode == 0
    finally:
        if group_alive(check) and not stop_group(check):
            raise RuntimeError('Diagnostic process group did not stop')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace', default=os.environ.get('TELLO_NAMESPACE', 'webcam_test'))
    parser.add_argument('--bind', default=os.environ.get('TELLO_BIND', '127.0.0.1'))
    parser.add_argument('--port', type=int,
                        default=int(os.environ.get('TELLO_OBSERVATION_PORT', '5005')))
    parser.add_argument('--bridge-trace', default='', help='New FAKE server timing report')
    parser.add_argument('--trace-label', default='')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Observation UDP port must be 1..65535 (port 0 is not reproducible)')
    try:
        args.namespace = canonical_namespace(args.namespace)
    except ValueError as error:
        parser.error(str(error))
    if args.bridge_trace:
        args.bridge_trace = str(Path(args.bridge_trace).expanduser().absolute())
        if os.path.lexists(args.bridge_trace):
            parser.error('Trace path already exists; choose a new filename')
        if not Path(args.bridge_trace).parent.is_dir():
            parser.error('Trace parent directory does not exist')

    stopping = False
    launch = None
    lock = None
    failed = False

    def request_stop(signum, frame):
        nonlocal stopping
        stopping = True

    previous_handlers = {sig: signal.signal(sig, request_stop)
                         for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        lock = acquire_lock(args.namespace)
        check_port(args.bind, args.port)
        if not run_check(args.namespace, '--expect-empty', lambda: stopping):
            raise RuntimeError('Preflight failed or interrupted; no pipeline started')
        if stopping:
            return 130
        revision = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                                  capture_output=True, text=True, timeout=5)
        launch_env = dict(os.environ, TELLO_REVISION=(
            revision.stdout.strip() if revision.returncode == 0 else 'UNRECORDED'))
        # The launch process retains the flock if this wrapper is killed.
        launch = subprocess.Popen(build_launch_command(args), start_new_session=True,
                                  env=launch_env, pass_fds=(lock.fileno(),))
        print('STARTING: waiting for one complete, disabled FAKE pipeline. Do not enable.',
              flush=True)
        if not run_check(args.namespace, '--require-disabled', lambda: stopping, launch):
            raise RuntimeError('Readiness failed or interrupted; stopping owned pipeline')
        if launch.poll() is not None or stopping:
            raise RuntimeError('Launch exited or stop requested before readiness')
        print('READY: one publisher per topic, required nodes reachable, clocks real, '
              'approved output disabled/zero, bridge FAKE/unarmed. '
              'This is software readiness, not flight clearance. '
              'Ctrl+C requests takeover before stopping ROS.', flush=True)
        while launch.poll() is None and not stopping:
            time.sleep(0.05)
        if not stopping:
            raise RuntimeError('Required pipeline process exited; preserve launch output')
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print('PIPELINE ERROR: ' + str(error), file=sys.stderr, flush=True)
        failed = True
    finally:
        if launch is not None:
            if group_alive(launch):
                # Preserve the existing takeover-before-shutdown ordering.
                takeover = None
                try:
                    takeover = subprocess.Popen([
                        sys.executable, str(ROOT / 'src/tello_bridge/scripts/tello_operator'),
                        'takeover', '--namespace', args.namespace], start_new_session=True)
                    if takeover.wait(timeout=12):
                        print('Takeover cleanup incomplete; preserve logs.', flush=True)
                        failed = True
                except (OSError, subprocess.TimeoutExpired):
                    print('Takeover cleanup failed/timed out; stopping owned pipeline.',
                          flush=True)
                    failed = True
                finally:
                    if takeover is not None and group_alive(takeover):
                        if not stop_group(takeover):
                            failed = True
                if not stop_group(launch, launch_noninteractive=not sys.stdin.isatty()):
                    print('Owned process group still present; do not restart.', flush=True)
                    failed = True
            if not check_trace(args.bridge_trace, args.namespace):
                failed = True
        if lock is not None:
            lock.close()
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
