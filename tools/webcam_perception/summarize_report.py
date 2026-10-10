#!/usr/bin/env python3
"""Summarize observer evidence; does not change policy or infer physical outcomes."""
import argparse
import json
from pathlib import Path


def summarize(report):
    warnings = []
    if report['missing_topics']:
        warnings.append('Missing topics: ' + ', '.join(report['missing_topics']))
    if report['source_age_clock_warning']:
        warnings.append('ROS/monotonic elapsed mismatch; Windows agreement still needs checking')
    for name, stats in report['topics'].items():
        for key in ('messages_with_negative_source_age',
                    'flagged_usable_messages_with_source_age_ge_250ms_at_monitor',
                    'older_source_stamps'):
            if stats[key]:
                warnings.append(f'{name}: {key}={stats[key]}')
        if stats['advancing_source_age_ms']['omitted']:
            warnings.append(name + ': first-sample age distribution truncated')
    for key in ('state_transitions_omitted', 'message_records_omitted',
                'nonfinite_command_or_error_values', 'nonneutral_messages_without_tracking'):
        if report.get(key):
            warnings.append(f'{key}={report[key]}')
    return {'metadata': report.get('metadata'),
            'observed_duration_s': report['observed_duration_s'],
            'warnings': warnings, 'topics': {name: {
                'received_hz': stats['observed_messages_per_second_over_full_window'],
                'advancing_source_hz':
                    stats['advancing_source_stamps_per_second_over_full_window'],
                'advancing_source_age_ms': stats['advancing_source_age_ms']}
        for name, stats in report['topics'].items()},
        'publisher_rate_hz': None, 'camera_throughput_fps': None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(json.loads(args.report.read_text())), indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
