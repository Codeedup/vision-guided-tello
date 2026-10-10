"""Monitor logic/CLI tests that run without ROS or hardware."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest

import measure_pipeline
from measure_pipeline import Measurements, parse_args, stamp_ns
from summarize_report import summarize


class TestMonitorPolicies(measure_pipeline.MeasurementTests):
    """Register the draft's ten policy contracts in the normal test entry point."""


class TestAdditionalMeasurements(unittest.TestCase):
    def test_invalid_cli_and_existing_report(self):
        cases = [['--duration', v] for v in ('nan', 'inf', '0', '601')]
        cases += [['--namespace', '/bad name'], ['--sample-limit', '0'],
                  ['--configuration', '[]'], ['--configuration', '{"a":NaN}']]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'existing.json'
            path.write_text('preserve')
            cases.append(['--output', str(path)])
            for args in cases:
                with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as result:
                        parse_args(args)
                    self.assertEqual(result.exception.code, 2)
            self.assertEqual(path.read_text(), 'preserve')

    def test_raw_command_values_json_and_truncation(self):
        data = Measurements(100, 0, sample_limit=1, event_limit=1)
        header = NS(stamp=NS(sec=1, nanosec=0))
        message = NS(header=header, source_header=header, autonomy_enabled=False,
                     tracking_allowed=False, request_land=False, lateral=1, vertical=float('nan'))
        for ns in range(3):
            data.record_message('approved_command', message, ns)
            data.observe('approved_command', 1, 1, 101 + ns, ns,
                         False, (bool(ns % 2), False, False))
        report = data.report(10, '/test', 1, False, {'source_classification': 'synthetic'})
        json.dumps(report, allow_nan=False)
        self.assertEqual(report['message_records_omitted'], 2)
        self.assertEqual(report['nonneutral_messages_without_tracking'], 3)
        self.assertEqual(report['nonfinite_command_or_error_values'], 3)
        self.assertEqual(report['state_transitions_omitted'], 1)
        self.assertIsNone(report['message_records'][0]['values']['vertical'])
        self.assertTrue(summarize(report)['warnings'])

    def test_stamp_types_and_ros_bounds(self):
        for stamp in (NS(sec=True, nanosec=0), NS(sec=1, nanosec=0.5),
                      NS(sec=2147483648, nanosec=0), NS(), None):
            self.assertIsNone(stamp_ns(stamp))


if __name__ == '__main__':
    unittest.main()
