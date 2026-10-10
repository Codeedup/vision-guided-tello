"""Trace bounds and original decoded-frame provenance without camera access."""
import json
from types import SimpleNamespace

from decoded_timing import DecodedTiming
import pytest


def record(trace, sequence=0, stamp=123):
    frame = SimpleNamespace(sequence=sequence, receipt_ns=stamp, monotonic_ns=100,
                            width=64, height=48)
    observation = {'capture_time_ns': stamp, 'detected': False, 'tracked_hands': 0}
    marks = {'processing_start_ns': 120, 'inference_start_ns': 130,
             'inference_done_ns': 150, 'mapping_done_ns': 160, 'send_done_ns': 200}
    trace.record(frame, observation, marks, 123)


def test_decoded_timing_preserves_identity_and_bounds_retained_history(tmp_path):
    path = tmp_path / 'decoded.json'
    trace = DecodedTiming(path, capacity=1)
    record(trace, sequence=0)
    record(trace, sequence=1, stamp=456)
    trace.finish()
    trace.finish()  # Finishing twice must not append or overwrite a completed trace.
    result = json.loads(path.read_text())
    assert result['frames_total'] == 2
    assert result['frames_retained'] == 1
    assert result['frames_omitted'] == 1
    assert result['frames'][0]['capture_time_ns'] == 456
    assert result['frames'][0]['frame_sequence'] == 1
    assert result['frames'][0]['durations_ns'] == {
        'decode_to_processing': 20, 'inference': 20, 'decode_to_send': 100}
    assert result['summary_ms']['decode_to_send']['max'] == 0.0001
    with pytest.raises(FileExistsError):
        DecodedTiming(path)
    assert json.loads(path.read_text()) == result


def test_decoded_timing_rejects_restamped_or_backwards_measurements(tmp_path):
    trace = DecodedTiming(tmp_path / 'decoded.json')
    frame = SimpleNamespace(sequence=0, receipt_ns=123, monotonic_ns=100, width=64, height=48)
    marks = {'processing_start_ns': 120, 'inference_start_ns': 130,
             'inference_done_ns': 150, 'mapping_done_ns': 160, 'send_done_ns': 200}
    try:
        with pytest.raises(ValueError, match='preserve'):
            trace.record(frame, {'capture_time_ns': 456}, marks, 10)
        with pytest.raises(ValueError, match='monotonically'):
            trace.record(frame, {'capture_time_ns': 123}, dict(marks, send_done_ns=110), 10)
    finally:
        trace.finish('error')
    result = json.loads((tmp_path / 'decoded.json').read_text())
    assert result['frames_total'] == 0
    assert result['completion'] == 'error'
