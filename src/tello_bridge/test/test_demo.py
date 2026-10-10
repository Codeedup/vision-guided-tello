"""User-facing demo commands must actually exercise tracking and the chosen fault."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('scenario', ['static', 'loss'])
def test_complete_synthetic_demo(tmp_path, scenario):
    output = tmp_path / scenario
    result = subprocess.run([
        sys.executable, str(ROOT / 'tools/offline_demo.py'), '--scenario', scenario,
        '--duration', '6', '--output', str(output)], capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / 'report.json').read_text())
    assert report['metadata']['source_classification'] == 'synthetic'
    assert report['metadata']['configuration']['transport'] == 'fake'
    assert report['missing_topics'] == []
    assert report['nonneutral_messages_without_tracking'] == 0
    records = [row for row in report['message_records'] if row['topic'] == 'approved_command']
    assert any(row['flags'][1] for row in records)
    assert any(row['flags'][2] for row in records) == (scenario == 'loss')
    assert (output / 'summary.json').is_file()
