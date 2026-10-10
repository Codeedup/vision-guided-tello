"""PyAV boundary checks with synthetic decoder events; no aircraft access."""
from contextlib import nullcontext
from queue import Queue
import socket
from threading import Event
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from frame_sources import decode_av, decoder_worker, DecoderSource, Frame
import pytest


class SyntheticInvalidDataError(Exception):
    pass


def synthetic_av(codec):
    return SimpleNamespace(
        CodecContext=SimpleNamespace(create=Mock(return_value=codec)),
        open=Mock(side_effect=AssertionError('UDP must not call av.open')),
        InvalidDataError=SyntheticInvalidDataError,
        logging=SimpleNamespace(ERROR=16, set_level=Mock(),
                                Capture=lambda **kwargs: nullcontext([])))


def test_udp_fragments_share_parser_and_only_decoded_images_create_frames():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    image = SimpleNamespace(width=1, height=1, pts=42,
                            to_ndarray=lambda **kwargs: SimpleNamespace(tobytes=lambda: b'RGB'))

    class SyntheticCodec:
        def __init__(self):
            self.data = b''

        def parse(self, payload):
            self.data += payload
            return [self.data] if self.data == b'firstsecond' else []

        def decode(self, packet):
            assert packet == b'firstsecond'
            return [image]

    codec = SyntheticCodec()
    av = synthetic_av(codec)
    stages = []

    def status(stage):
        stages.append(stage)
        if stage == 'video_socket_bound':
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(b'first', ('127.0.0.1', port))
                sender.sendto(b'second', ('127.0.0.1', port))

    frames = decode_av(f'udp://127.0.0.1:{port}', report_status=status)
    try:
        with patch.dict('sys.modules', {'av': av}):
            frame = next(frames)
        assert (frame.sequence, frame.pixels, frame.decoder_pts) == (0, b'RGB', 42)
        assert stages == ['importing_av', 'creating_h264_decoder', 'video_socket_bound',
                          'first_video_packet', 'first_frame_decoded']
        av.CodecContext.create.assert_called_once_with('h264', 'r')
    finally:
        frames.close()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as released:
        released.bind(('127.0.0.1', port))


def test_udp_silence_reports_timeout_and_closes_socket():
    receiver = Mock()
    receiver.__enter__ = Mock(return_value=receiver)
    receiver.__exit__ = Mock(return_value=False)
    receiver.recvfrom.side_effect = socket.timeout('Synthetic silence')
    av = synthetic_av(Mock())
    with patch.dict('sys.modules', {'av': av}), \
            patch('frame_sources.socket.socket', return_value=receiver), \
            pytest.raises(TimeoutError, match='packet receipt timed out'):
        next(decode_av('udp://127.0.0.1:11111'))
    receiver.settimeout.assert_called_once_with(2.0)
    receiver.__exit__.assert_called_once()


def test_invalid_h264_packet_recovers_without_creating_a_frame_for_the_error():
    image = SimpleNamespace(width=1, height=1, pts=None,
                            to_ndarray=lambda **kwargs: SimpleNamespace(tobytes=lambda: b'RGB'))
    codec = SimpleNamespace(
        parse=Mock(side_effect=[[b'\0\0\1\x67bad'], [b'good']]),
        decode=Mock(side_effect=[SyntheticInvalidDataError('no frame'), [image]]))
    av = synthetic_av(codec)
    av.logging.Capture = lambda **kwargs: nullcontext([(16, 'h264', 'synthetic no frame')])
    receiver = Mock()
    receiver.__enter__ = Mock(return_value=receiver)
    receiver.__exit__ = Mock(return_value=False)
    receiver.recvfrom.side_effect = [(b'first', ('127.0.0.1', 1)),
                                     (b'second', ('127.0.0.1', 1))]
    progress = []

    def status(stage, **details):
        progress.append({'stage': stage, **details})

    frames = decode_av('udp://127.0.0.1:11111', report_status=status)
    try:
        with patch.dict('sys.modules', {'av': av}), \
                patch('frame_sources.socket.socket', return_value=receiver):
            frame = next(frames)
        assert frame.sequence == 0 and frame.pixels == b'RGB'
        invalid = next(e for e in progress if e['stage'] == 'invalid_h264_packet')
        assert invalid['nal_unit_types'] == [7]
        assert invalid['packet_bytes'] == 7
        assert 'synthetic no frame' in invalid['ffmpeg_logs'][0]
        recovered = next(e for e in progress if e['stage'] == 'h264_recovered')
        assert recovered['invalid_packets'] == 1
        assert codec.decode.call_count == 2
    finally:
        frames.close()
    receiver.__exit__.assert_called_once()


def test_continuous_invalid_packets_have_a_bounded_recovery_window():
    codec = SimpleNamespace(parse=Mock(return_value=[b'bad']),
                            decode=Mock(side_effect=SyntheticInvalidDataError('no frame')))
    av = synthetic_av(codec)
    receiver = Mock()
    receiver.__enter__ = Mock(return_value=receiver)
    receiver.__exit__ = Mock(return_value=False)
    receiver.recvfrom.return_value = (b'bytes', ('127.0.0.1', 1))
    with patch.dict('sys.modules', {'av': av}), \
            patch('frame_sources.socket.socket', return_value=receiver), \
            patch('frame_sources.time.monotonic', side_effect=[0, 0.5, 2.01]), \
            pytest.raises(RuntimeError, match='No decodable H.264 frame for 2 seconds'):
        next(decode_av('udp://127.0.0.1:11111'))
    assert codec.decode.call_count == 2
    receiver.__exit__.assert_called_once()


def test_other_decode_errors_remain_terminal():
    codec = SimpleNamespace(parse=Mock(return_value=[b'bad']),
                            decode=Mock(side_effect=MemoryError('Synthetic allocation failure')))
    av = synthetic_av(codec)
    receiver = Mock()
    receiver.__enter__ = Mock(return_value=receiver)
    receiver.__exit__ = Mock(return_value=False)
    receiver.recvfrom.return_value = (b'bytes', ('127.0.0.1', 1))
    with patch.dict('sys.modules', {'av': av}), \
            patch('frame_sources.socket.socket', return_value=receiver), \
            pytest.raises(MemoryError, match='Synthetic allocation failure'):
        next(decode_av('udp://127.0.0.1:11111'))
    assert codec.decode.call_count == 1
    receiver.__exit__.assert_called_once()


def test_video_socket_handshake_timeout_is_bounded_and_does_not_make_frames():
    source = DecoderSource('udp://127.0.0.1:11111')
    source.output = Queue(maxsize=1)
    source.process = SimpleNamespace(exitcode=None)
    with patch('frame_sources.time.monotonic', side_effect=[0, 0, 0.02]), \
            patch('frame_sources.time.sleep'), \
            pytest.raises(TimeoutError, match='socket setup timed out'):
        source.wait_for_video_socket(timeout=0.01)
    assert source.slot.frame is None


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
        frames = list(decode_av('synthetic.mp4'))
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
        decoder_worker('synthetic.mp4', output, Event(), decode_av, progress)
    stages = [progress.get_nowait()['stage'] for _ in range(progress.qsize())]
    assert stages == ['worker_started', 'importing_av', 'opening_video', 'video_opened',
                      'first_frame_decoded', 'first_frame_queued', 'worker_stopped']
    assert isinstance(output.get_nowait(), Frame)
    assert output.empty()


def test_worker_open_failure_reports_last_stage_and_original_error():
    av = SimpleNamespace(open=Mock(side_effect=TimeoutError('Synthetic open failure')))
    output, progress = Queue(maxsize=1), Queue(maxsize=8)
    with patch.dict('sys.modules', {'av': av}):
        decoder_worker('synthetic.mp4', output, Event(), decode_av, progress)
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
