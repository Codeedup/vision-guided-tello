"""Direct CLI startup must resolve sibling tools without a test PYTHONPATH."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('script', ['bench_hand_preview.py', 'replay_hand_preview.py'])
def test_direct_cli_help_in_clean_environment(tmp_path, script):
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    path = Path(__file__).resolve().parent / script
    result = subprocess.run([sys.executable, str(path), '--help'], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'usage:' in result.stdout
