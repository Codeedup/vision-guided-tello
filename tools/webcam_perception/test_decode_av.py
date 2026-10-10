"""PyAV boundary checks with synthetic decoder events; no aircraft access."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

from frame_sources import decode_av
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
