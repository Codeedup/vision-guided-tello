"""Exercise launcher argv without starting ROS or contacting any aircraft."""
import contextlib
import io
import unittest
from unittest.mock import Mock, patch

import start_webcam_pipeline as launcher


class LauncherCommandTests(unittest.TestCase):
    def launch_command(self, options):
        process = Mock(returncode=0)
        process.poll.return_value = 0
        revision = Mock(returncode=0, stdout='test-revision\n')
        with patch.object(launcher.sys, 'argv', ['launcher', *options]), \
                patch.dict(launcher.os.environ, {}, clear=True), \
                patch.object(launcher.signal, 'signal'), \
                patch.object(launcher.subprocess, 'run', return_value=revision), \
                patch.object(launcher.subprocess, 'Popen', return_value=process) as popen, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(launcher.main(), 0)
        command = popen.call_args.args[0]
        # Match the ROS CLI requirement that each launch assignment has a value.
        for argument in command[4:]:
            name, value = argument.split(':=', 1)
            self.assertTrue(name)
            self.assertTrue(value, argument)
        self.assertTrue(popen.call_args.kwargs['start_new_session'])
        self.assertEqual(popen.call_args.kwargs['env']['TELLO_REVISION'], 'test-revision')
        return command

    def test_disabled_baseline_omits_empty_optional_arguments(self):
        command = self.launch_command(['--bind', '0.0.0.0'])
        self.assertEqual(command, [
            'ros2', 'launch', 'tello_bridge', 'webcam.launch.py',
            'namespace:=webcam_test', 'bind:=0.0.0.0', 'port:=5005'])

    def test_trace_without_label(self):
        command = self.launch_command(['--bridge-trace', '/tmp/bridge trace.json'])
        self.assertIn('bridge_trace:=/tmp/bridge trace.json', command)
        self.assertFalse(any(arg.startswith('trace_label:=') for arg in command))

    def test_label_without_trace(self):
        command = self.launch_command(['--trace-label', 'baseline-01'])
        self.assertIn('trace_label:=baseline-01', command)
        self.assertFalse(any(arg.startswith('bridge_trace:=') for arg in command))

    def test_trace_with_label(self):
        command = self.launch_command([
            '--bridge-trace', '/tmp/bridge.json', '--trace-label', 'baseline-01'])
        self.assertEqual(command[-2:], [
            'bridge_trace:=/tmp/bridge.json', 'trace_label:=baseline-01'])


if __name__ == '__main__':
    unittest.main()
