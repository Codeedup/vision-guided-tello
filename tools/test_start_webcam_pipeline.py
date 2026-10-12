"""Exercise launcher argv without starting ROS or contacting any aircraft."""
import argparse
import unittest

import start_webcam_pipeline as launcher


class LauncherCommandTests(unittest.TestCase):
    def launch_command(self, **options):
        args = argparse.Namespace(namespace=launcher.canonical_namespace('webcam_test'),
                                  bind='127.0.0.1', port=5005, bridge_trace='', trace_label='')
        for name, value in options.items():
            setattr(args, name, value)
        command = launcher.build_launch_command(args)
        # Match the ROS CLI requirement that each launch assignment has a value.
        for argument in command[4:]:
            name, value = argument.split(':=', 1)
            self.assertTrue(name)
            self.assertTrue(value, argument)
        # Process isolation and revision propagation are checked at the Popen
        # boundary in tests/test_launcher_reliability.py, not by executing ROS here.
        return command

    def test_disabled_baseline_omits_empty_optional_arguments(self):
        command = self.launch_command(bind='0.0.0.0')
        self.assertEqual(command, [
            'ros2', 'launch', 'tello_bridge', 'webcam.launch.py',
            'namespace:=/webcam_test', 'bind:=0.0.0.0', 'port:=5005'])

    def test_trace_without_label(self):
        command = self.launch_command(bridge_trace='/tmp/bridge trace.json')
        self.assertIn('bridge_trace:=/tmp/bridge trace.json', command)
        self.assertFalse(any(arg.startswith('trace_label:=') for arg in command))

    def test_label_without_trace(self):
        command = self.launch_command(trace_label='baseline-01')
        self.assertIn('trace_label:=baseline-01', command)
        self.assertFalse(any(arg.startswith('bridge_trace:=') for arg in command))

    def test_trace_with_label(self):
        command = self.launch_command(bridge_trace='/tmp/bridge.json', trace_label='baseline-01')
        self.assertEqual(command[-2:], [
            'bridge_trace:=/tmp/bridge.json', 'trace_label:=baseline-01'])


if __name__ == '__main__':
    unittest.main()
