"""Clock evidence uses measured bounds and fails on malformed or absent probes."""

from io import StringIO
import queue
from types import SimpleNamespace
from unittest.mock import Mock, patch

from check_clocks import main, measure
import pytest


def test_tightest_clock_interval_and_ten_samples():
    lines = queue.Queue()
    lines.put('READY')
    for _ in range(10):
        lines.put('1000000')
    clocks = iter([0, 2000000] * 9 + [900000, 1100000])
    process = SimpleNamespace(stdin=StringIO())
    result = measure(process, lines, clock=lambda: next(clocks))
    assert result == {'lower_ms': -0.1, 'upper_ms': 0.1, 'round_trip_ms': 0.2}
    assert process.stdin.getvalue() == 'sample\n' * 10


def test_clock_evidence_collision_prevents_wsl_start(tmp_path):
    path = tmp_path / 'clock.json'
    path.write_text('original')
    with patch('check_clocks.subprocess.Popen') as popen, pytest.raises(FileExistsError):
        main(['--output', str(path)])
    popen.assert_not_called()
    assert path.read_text() == 'original'


def test_probe_timeout_is_bounded():
    lines = Mock()
    lines.get.side_effect = queue.Empty
    with pytest.raises(TimeoutError, match='timed out'):
        measure(None, lines)
    lines.get.assert_called_once_with(timeout=10)
