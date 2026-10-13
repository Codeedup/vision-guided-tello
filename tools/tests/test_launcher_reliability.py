"""Non-ROS regression tests. Live Jazzy acceptance is documented separately."""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import pipeline_diagnostics as diagnostics  # noqa: E402
import start_webcam_pipeline as launcher  # noqa: E402


class PreflightTests(unittest.TestCase):
    def test_namespace_aliases_share_identity(self):
        self.assertEqual(launcher.canonical_namespace('webcam_test/'), '/webcam_test')
        self.assertEqual(launcher.canonical_namespace('/webcam_test'), '/webcam_test')
        for value in ('/', 'a//b', '1bad', '../other', 'a b'):
            with self.assertRaises(ValueError):
                launcher.canonical_namespace(value)

    def test_live_udp_owner_is_rejected_without_disrupting_it(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as owner:
            owner.bind(('127.0.0.1', 0))
            port = owner.getsockname()[1]
            with self.assertRaises(OSError):
                launcher.check_port('0.0.0.0', port)
            self.assertEqual(owner.getsockname()[1], port)
        launcher.check_port('127.0.0.1', port)

    def test_namespace_lock_is_exclusive_and_reusable(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(launcher.tempfile, 'gettempdir', return_value=directory):
            with launcher.acquire_lock('/webcam_test'):
                with self.assertRaises(RuntimeError):
                    launcher.acquire_lock('/webcam_test')
                with launcher.acquire_lock('/another_namespace'):
                    pass
            with launcher.acquire_lock('/webcam_test'):
                pass

    def test_inherited_lock_survives_wrapper_handle_close(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(launcher.tempfile, 'gettempdir', return_value=directory):
            lock = launcher.acquire_lock('/webcam_test')
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                     pass_fds=(lock.fileno(),))
            lock.close()
            try:
                with self.assertRaises(RuntimeError):
                    launcher.acquire_lock('/webcam_test')
            finally:
                child.terminate()
                child.wait(timeout=3)
            with launcher.acquire_lock('/webcam_test'):
                pass

    def test_launch_omits_empty_options_and_preserves_spaced_path(self):
        args = argparse.Namespace(namespace='/webcam_test', bind='0.0.0.0', port=5005,
                                  bridge_trace='', trace_label='')
        command = launcher.build_launch_command(args)
        self.assertNotIn('bridge_trace:=', command)
        self.assertNotIn('trace_label:=', command)
        args.bridge_trace = '/tmp/trace with spaces.json'
        self.assertIn('bridge_trace:=/tmp/trace with spaces.json',
                      launcher.build_launch_command(args))

    def test_trace_requires_parseable_matching_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'trace.json'
            with patch('builtins.print'):
                self.assertFalse(launcher.check_trace(path, '/webcam_test'))
                path.write_text('')
                self.assertFalse(launcher.check_trace(path, '/webcam_test'))
                valid = {'completion': 'shutdown', 'events_retained': 1,
                         'metadata': {'role': 'bridge', 'fake': True, 'namespace': '/webcam_test'}}
                path.write_text(json.dumps(valid))
                self.assertTrue(launcher.check_trace(path, '/webcam_test'))
                self.assertFalse(launcher.check_trace(path, '/another'))
                valid['completion'] = 'initialization_failed'
                path.write_text(json.dumps(valid))
                self.assertFalse(launcher.check_trace(path, '/webcam_test'))


class GraphTests(unittest.TestCase):
    def graph(self):
        ns = '/webcam_test'
        return {'namespace': ns, 'node_counts': {ns + '/' + n: 1 for n in diagnostics.NODES},
                'use_sim_time': {ns + '/' + n: False for n in diagnostics.NODES},
                'topics': {ns + '/' + t: {'publishers': [{'node': ns + '/' + n, 'type': typ}]}
                           for t, (n, typ) in diagnostics.TOPICS.items()}}

    def test_complete_graph_passes(self):
        self.assertEqual(diagnostics.graph_errors(self.graph()), [])

    def test_missing_and_duplicate_nodes_fail_even_with_single_publishers(self):
        for count in (0, 2):
            graph = self.graph()
            graph['node_counts']['/webcam_test/tello_bridge'] = count
            self.assertTrue(diagnostics.graph_errors(graph))

    def test_wrong_publisher_or_message_type_fails(self):
        for field in ('node', 'type'):
            graph = self.graph()
            graph['topics']['/webcam_test/approved_command']['publishers'][0][field] = 'wrong'
            self.assertTrue(diagnostics.graph_errors(graph))

    def test_missing_or_duplicate_publisher_fails(self):
        for count in (0, 2):
            graph = self.graph()
            publishers = graph['topics']['/webcam_test/approved_command']['publishers']
            graph['topics']['/webcam_test/approved_command']['publishers'] = publishers * count
            self.assertTrue(diagnostics.graph_errors(graph))

    def test_unknown_or_simulated_clocks_fail(self):
        for value in ('UNAVAILABLE', True, None, 0):
            graph = self.graph()
            graph['use_sim_time']['/webcam_test/mission_manager'] = value
            self.assertTrue(diagnostics.graph_errors(graph))

    def test_disabled_fresh_output_and_fake_unarmed_status_required(self):
        approved = {'receipt': 10.0, 'flags': [False, False, False], 'values': [0.0, 0.0]}
        bridge = {'fake': True, 'armed': False, 'awaiting_enabled': False,
                  'upstream_disabled': True, 'landing_latched': False}
        self.assertEqual(diagnostics.disabled_errors(approved, bridge, 10.1), [])
        self.assertTrue(diagnostics.disabled_errors(approved, bridge, 10.6))
        for key in bridge:
            altered = dict(bridge)
            altered[key] = not altered[key]
            self.assertTrue(diagnostics.disabled_errors(approved, altered, 10.1))
        for flags, values in (([True, False, False], [0, 0]),
                              ([False, False, True], [0, 0]),
                              ([False, False, False], [1, 0]),
                              ([False, False, False], [float('nan'), 0])):
            altered = dict(approved, flags=flags, values=values)
            self.assertTrue(diagnostics.disabled_errors(altered, bridge, 10.1))


class WrapperTests(unittest.TestCase):
    def harness(self, checks, *, poll=None, port_error=None, stop_on_poll=False):
        events = []
        launch = MagicMock()
        takeover = MagicMock()
        takeover.wait.side_effect = lambda **kw: events.append('takeover') or 0
        handlers = {}

        def install_signal(sig, handler):
            handlers[sig] = handler
            return signal.SIG_DFL

        polls = 0

        def poll_launch():
            nonlocal polls
            polls += 1
            if stop_on_poll and polls >= int(stop_on_poll):
                handlers[signal.SIGINT](signal.SIGINT, None)
            return poll

        launch.poll.side_effect = poll_launch
        with ExitStack() as stack:
            stack.enter_context(patch.object(
                sys, 'argv', ['launcher', '--namespace', 'webcam_test']))
            stack.enter_context(patch.object(
                launcher.signal, 'signal', side_effect=install_signal))
            stack.enter_context(patch.object(launcher, 'acquire_lock', return_value=MagicMock()))
            stack.enter_context(patch.object(launcher, 'check_port', side_effect=port_error))
            stack.enter_context(patch.object(launcher, 'run_check', side_effect=checks))
            stack.enter_context(patch.object(
                launcher.subprocess, 'run',
                return_value=MagicMock(returncode=0, stdout='revision')))
            popen = stack.enter_context(patch.object(launcher.subprocess, 'Popen',
                                                     side_effect=[launch, takeover]))
            stack.enter_context(patch.object(
                launcher, 'group_alive', side_effect=lambda p: p is launch))
            stack.enter_context(patch.object(
                launcher, 'stop_group', side_effect=lambda p, **kw: events.append('stop') or True))
            stack.enter_context(patch.object(launcher, 'check_trace', return_value=True))
            output = stack.enter_context(patch('builtins.print'))
            code = launcher.main()
            if popen.call_count:
                launch_call = popen.call_args_list[0]
                self.assertEqual(launch_call.args[0][:4],
                                 ['ros2', 'launch', 'tello_bridge', 'webcam.launch.py'])
                self.assertIn('namespace:=/webcam_test', launch_call.args[0])
                self.assertIs(launch_call.kwargs['start_new_session'], True)
                self.assertEqual(launch_call.kwargs['env']['TELLO_REVISION'], 'revision')
                self.assertEqual(len(launch_call.kwargs['pass_fds']), 1)
            messages = [str(call.args[0]) for call in output.call_args_list]
            return code, messages, events, popen.call_count

    def test_busy_port_never_spawns_or_takes_over_existing_pipeline(self):
        code, messages, events, spawned = self.harness([], port_error=OSError('busy'))
        self.assertEqual(code, 1)
        self.assertEqual(spawned, 0)
        self.assertEqual(events, [])

    def test_existing_graph_never_spawns_or_takes_over(self):
        code, messages, events, spawned = self.harness([False])
        self.assertEqual(code, 1)
        self.assertEqual(spawned, 0)
        self.assertEqual(events, [])

    def test_readiness_failure_cleans_up_without_ready_message(self):
        code, messages, events, spawned = self.harness([True, False])
        self.assertEqual(code, 1)
        self.assertFalse(any(m.startswith('READY:') for m in messages))
        self.assertEqual(events, ['takeover', 'stop'])

    def test_unexpected_clean_launch_exit_is_failure(self):
        code, messages, events, spawned = self.harness([True, True], poll=0)
        self.assertEqual(code, 1)
        self.assertFalse(any(m.startswith('READY:') for m in messages))
        self.assertEqual(events, ['takeover', 'stop'])

    def test_normal_stop_revokes_before_group_shutdown(self):
        code, messages, events, spawned = self.harness([True, True], stop_on_poll=2)
        self.assertEqual(code, 0)
        self.assertTrue(any(m.startswith('READY:') for m in messages))
        self.assertEqual(events, ['takeover', 'stop'])

    def test_stop_request_during_startup_still_cleans_owned_group(self):
        code, messages, events, spawned = self.harness([True, True], stop_on_poll=True)
        self.assertEqual(code, 1)
        self.assertFalse(any(m.startswith('READY:') for m in messages))
        self.assertEqual(events, ['takeover', 'stop'])


class ProcessGroupTests(unittest.TestCase):
    def test_noninteractive_launch_forwards_exactly_one_child_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / 'forwarded_child.py'
            child.write_text("""import signal, sys, time
from pathlib import Path
count = 0
def stop(sig, frame):
    global count
    count += 1
signal.signal(signal.SIGINT, stop)
print('ready', flush=True)
while not count: time.sleep(.01)
time.sleep(.1)
Path(sys.argv[1]).write_text(str(count))
""")
            parent = root / 'forwarding_launch.py'
            parent.write_text("""import signal, subprocess, sys, time
child = subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]],
                         stdout=subprocess.PIPE, text=True)
assert child.stdout.readline().strip() == 'ready'
def stop(sig, frame):
    child.send_signal(signal.SIGINT)
    child.wait(timeout=3)
    raise SystemExit(0)
signal.signal(signal.SIGINT, stop)
print('ready', flush=True)
while True: time.sleep(.01)
""")
            count = root / 'signal-count'
            process = subprocess.Popen(
                [sys.executable, str(parent), str(child), str(count)],
                start_new_session=True, stdout=subprocess.PIPE, text=True)
            try:
                import selectors
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    self.assertTrue(selector.select(timeout=5), 'Fixture failed to start')
                self.assertEqual(process.stdout.readline().strip(), 'ready')
                with patch('builtins.print'):
                    self.assertTrue(launcher.stop_group(process, launch_noninteractive=True))
                self.assertEqual(count.read_text(), '1')
                self.assertEqual(process.returncode, 0)
            finally:
                launcher.signal_group(process, signal.SIGKILL)
                process.wait(timeout=3)
                process.stdout.close()

    def test_stop_signals_children_and_waits_for_trace_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child_code = """import signal, sys, time
from pathlib import Path
root = Path(sys.argv[1])
def stop(sig, frame):
    (root / 'child-stopped').write_text(str(sig))
    raise SystemExit(0)
signal.signal(signal.SIGINT, stop)
(root / 'child-ready').write_text('ready')
while True: time.sleep(.01)
"""
            child_path = root / 'child.py'
            child_path.write_text(child_code)
            parent_path = root / 'parent.py'
            parent_path.write_text("""import signal, subprocess, sys, time
from pathlib import Path
root = Path(sys.argv[1])
child = subprocess.Popen([sys.executable, str(root / 'child.py'), str(root)])
def stop(sig, frame):
    child.wait(timeout=3)
    (root / 'parent-stopped').write_text(str(sig))
    raise SystemExit(0)
signal.signal(signal.SIGINT, stop)
while not (root / 'child-ready').exists(): time.sleep(.01)
print('ready', flush=True)
while True: time.sleep(.01)
""")
            process = subprocess.Popen([sys.executable, str(parent_path), str(root)],
                                       start_new_session=True, stdout=subprocess.PIPE, text=True)
            try:
                import selectors
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    self.assertTrue(selector.select(timeout=5), 'Fixture failed to start')
                self.assertEqual(process.stdout.readline().strip(), 'ready')
                with patch('builtins.print'):
                    self.assertTrue(launcher.stop_group(process))
                self.assertEqual((root / 'child-stopped').read_text(), str(signal.SIGINT))
                self.assertEqual((root / 'parent-stopped').read_text(), str(signal.SIGINT))
                self.assertFalse(launcher.group_alive(process))
            finally:
                launcher.signal_group(process, signal.SIGKILL)
                process.wait(timeout=3)
                process.stdout.close()


if __name__ == '__main__':
    unittest.main()
