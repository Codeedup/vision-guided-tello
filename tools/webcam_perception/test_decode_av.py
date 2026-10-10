"""PyAV boundary checks with synthetic decoder events; no aircraft access."""
from queue import Queue
from threading import Event
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from frame_sources import decode_av, decoder_worker, DecoderSource, Frame
import pytest


@pytest.mark.parametrize('uri', ['udp://0.0.0.0:11111', 'udp://127.0.0.1:11111'])
def test_tello_udp_selects_h264_with_small_startup_probe(uri):
    stream = Mock()
    stream.__enter__ = Mock(return_value=stream)
    stream.__exit__ = Mock(return_value=False)
    stream.decode.return_value = iter(())
    av = SimpleNamespace(open=Mock(return_value=stream))
    with patch.dict('sys.modules', {'av': av}):
        assert list(decode_av(uri)) == []
    options = av.open.call_args.kwargs
    assert options['format'] == 'h264'
    assert 32 <= int(options['options']['probesize']) <= 32768
    assert 0 < int(options['options']['analyzeduration']) <= 1_000_000
    assert options['timeout'] == (2.0, 0.2)
    stream.__exit__.assert_called_once()


def test_recorded_file_keeps_format_autodetection():
    stream = Mock()
    stream.__enter__ = Mock(return_value=stream)
    stream.__exit__ = Mock(return_value=False)
    stream.decode.return_value = iter(())
    av = SimpleNamespace(open=Mock(return_value=stream))
    with patch.dict('sys.modules', {'av': av}):
        assert list(decode_av('synthetic.mp4')) == []
    assert 'format' not in av.open.call_args.kwargs
    assert 'options' not in av.open.call_args.kwargs


def test_decode_events_keep_pixels_pts_and_separate_receipt_stamps():
    pixels = SimpleNamespace(tobytes=lambda: b'\x01\x02\x03')
    images = [SimpleNamespace(width=1, height=1, pts=pts,
                              to_ndarray=Mock(return_value=pixels))
              for pts in (100, 200)]
    stream = Mock()
    stream.__enter__ = Mock(return_value=stream)
    stream.__exit__ = Mock(return_value=False)
    stream.decode.return_value = iter(images)
    av = SimpleNamespace(open=Mock(return_value=stream))
    with patch.dict('sys.modules', {'av': av}), \
            patch('frame_sources.time.time_ns', side_effect=[1000, 2000]), \
            patch('frame_sources.time.monotonic_ns', side_effect=[10, 20]):
        frames = list(decode_av('udp://127.0.0.1:11111'))
    assert [(f.sequence, f.receipt_ns, f.monotonic_ns, f.decoder_pts) for f in frames] == [
        (0, 1000, 10, 100), (1, 2000, 20, 200)]
    assert all(f.pixels == b'\x01\x02\x03' and not f.mirrored for f in frames)
    for image in images:
        image.to_ndarray.assert_called_once_with(format='bgr24')


def test_worker_progress_survives_independently_of_frame_slot():
    image = SimpleNamespace(width=1, height=1, pts=None,
                            to_ndarray=lambda **kwargs: SimpleNamespace(tobytes=lambda: b'RGB'))
    stream = Mock()
    stream.__enter__ = Mock(return_value=stream)
    stream.__exit__ = Mock(return_value=False)
    stream.decode.return_value = iter([image])
    av = SimpleNamespace(open=Mock(return_value=stream))
    output, progress = Queue(maxsize=1), Queue(maxsize=8)
    with patch.dict('sys.modules', {'av': av}):
        decoder_worker('udp://127.0.0.1:11111', output, Event(), decode_av, progress)
    stages = [progress.get_nowait()['stage'] for _ in range(progress.qsize())]
    assert stages == ['worker_started', 'importing_av', 'opening_video', 'video_opened',
                      'first_frame_decoded', 'first_frame_queued', 'worker_stopped']
    assert isinstance(output.get_nowait(), Frame)
    assert output.empty()


def test_worker_open_failure_reports_last_stage_and_original_error():
    av = SimpleNamespace(open=Mock(side_effect=TimeoutError('Synthetic open failure')))
    output, progress = Queue(maxsize=1), Queue(maxsize=8)
    with patch.dict('sys.modules', {'av': av}):
        decoder_worker('udp://127.0.0.1:11111', output, Event(), decode_av, progress)
    stages = [progress.get_nowait()['stage'] for _ in range(progress.qsize())]
    assert stages == ['worker_started', 'importing_av', 'opening_video', 'worker_error']
    assert output.get_nowait() == ('decoder_error', 'TimeoutError', 'Synthetic open failure')


def synthetic_decoder(uri):
    yield Frame(0, time.time_ns(), time.monotonic_ns(), 1, 1, b'RGB')


def test_spawned_worker_delivers_frames_and_progress_and_closes(tmp_path):
    path = tmp_path / 'synthetic.fixture'
    path.write_text('No aircraft or camera data')
    source = DecoderSource(str(path), decoder=synthetic_decoder, timeout_ns=5_000_000_000)
    source.start()
    try:
        deadline = time.monotonic() + 5
        frame = None
        while time.monotonic() < deadline:
            new_frame = source.read()
            if new_frame is not None:
                frame = new_frame
            if source.progress_events and source.progress_events[-1]['stage'] == 'worker_stopped':
                break
            time.sleep(0.01)
        assert frame is not None
        assert frame.pixels == b'RGB'
        assert [e['stage'] for e in source.read_progress()] == [
            'worker_started', 'first_frame_queued', 'worker_stopped']
        assert source.read() is None
    finally:
        process = source.process
        source.close()
    assert not process.is_alive()
