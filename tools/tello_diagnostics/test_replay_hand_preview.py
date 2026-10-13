"""Replay is bounded, retains inference evidence, and opens no aircraft endpoint."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from replay_hand_preview import main, run_replay


def test_replay_bounds_and_retained_durations():
    preview = Mock(return_value=True)
    preview.last_inference_ns = 20_000_000
    preview.last_observation = {'detected': True, 'tracked_hands': 1}
    frames = [SimpleNamespace(sequence=n, width=64, height=48) for n in range(10)]
    marks = iter(range(100))
    with patch('socket.socket', side_effect=AssertionError('Unexpected networking')):
        result = run_replay(frames, preview, 3, 60, clock=lambda: next(marks))
    assert preview.call_count == 3
    assert result['completion'] == 'frame_limit'
    assert result['frames_processed'] == 3
    assert result['summary_ms']['inference']['p50'] == 20


def test_replay_duration_limit_before_processing():
    preview = Mock()
    marks = iter([0, 2_000_000_000])
    result = run_replay([None], preview, 10, 1, clock=lambda: next(marks))
    preview.assert_not_called()
    assert result['completion'] == 'duration_limit'


def test_replay_rejects_network_uri_without_importing_detector(tmp_path):
    with pytest.raises(SystemExit):
        main(['--input', 'udp://0.0.0.0:11111', '--output', str(tmp_path / 'report.json')])
