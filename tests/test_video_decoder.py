import numpy as np
import pytest

from src.video_decoder import VideoDecoder

from .conftest import VIDEO_FPS, VIDEO_FRAMES, VIDEO_SIZE


@pytest.fixture
def decoder(video_file, qapp):
    dec = VideoDecoder(video_file)
    yield dec
    dec.close()


def test_metadata(decoder):
    assert decoder.width == VIDEO_SIZE
    assert decoder.height == VIDEO_SIZE
    assert abs(decoder.fps - VIDEO_FPS) < 0.1
    assert decoder.total_frames == VIDEO_FRAMES
    assert abs(decoder.duration - VIDEO_FRAMES / VIDEO_FPS) < 0.2


def test_keyframe_index_built(decoder):
    assert len(decoder._keyframe_index) >= 1
    # First keyframe must be frame 0
    assert decoder._keyframe_index[0][0] == 0


def test_get_first_frame(decoder):
    frame = decoder.get_frame(0)
    assert frame is not None
    assert frame.shape == (VIDEO_SIZE, VIDEO_SIZE, 3)


def test_get_frame_content(decoder):
    # Frames are solid color (i*4); allow lossy-codec tolerance
    frame0 = decoder.get_frame(0)
    frame30 = decoder.get_frame(30)
    assert abs(int(frame0.mean()) - 0) < 20
    assert abs(int(frame30.mean()) - 120) < 20


def test_get_frame_out_of_range_clamped(decoder):
    assert decoder.get_frame(-5) is not None
    assert decoder.get_frame(10_000) is not None


def test_backward_then_forward_access(decoder):
    assert decoder.get_frame(40) is not None
    assert decoder.get_frame(5) is not None
    assert decoder.get_frame(41) is not None


def test_frame_caching(decoder):
    decoder.get_frame(10)
    assert 10 in decoder._cache
    hits_before = decoder._cache.stats["hits"]
    decoder.get_frame(10)
    assert decoder._cache.stats["hits"] > hits_before


def test_get_frame_async_multiple_pending(decoder):
    """Regression: two queued requests with equal priority used to raise
    TypeError inside PriorityQueue (FrameRequest has no ordering)."""
    decoder._cache.clear()
    reqs = [decoder.get_frame_async(i) for i in (50, 51, 52, 53)]
    for req in reqs:
        assert req.event.wait(timeout=5.0), "async frame request timed out"
        assert req.result is not None


def test_sequential_access_never_returns_none(decoder):
    """Regression: abandoning PyAV decode() generators flushed the codec,
    making every other sequential get_frame() return None."""
    for i in range(0, 20):
        decoder._cache.clear()  # Force a real decode each iteration
        assert decoder.get_frame(i) is not None, f"frame {i} was None"


def test_seek_to_non_keyframe_does_not_deadlock(decoder):
    """Regression: seek_to_frame() called decode_next_frame() while already
    holding the non-reentrant decoder lock -> deadlock on non-keyframe seeks."""
    decoder.seek_to_frame(37)  # gop=10, so 37 is not a keyframe
    frame = decoder.decode_next_frame()
    assert frame is not None


def test_time_frame_conversion(decoder):
    assert decoder.time_to_frame(1.0) == VIDEO_FPS
    assert abs(decoder.frame_to_time(VIDEO_FPS) - 1.0) < 1e-6
    assert abs(decoder.frame_duration - 1.0 / VIDEO_FPS) < 1e-6


def test_thumbnail(decoder):
    thumb = decoder.get_thumbnail_at_position(1.0)
    assert thumb is not None
    h, w, c = thumb.shape
    assert c == 3
    assert w <= 320 and h <= 180


def test_thumbnail_cached(decoder):
    t1 = decoder.get_thumbnail_at_position(0.5)
    t2 = decoder.get_thumbnail_at_position(0.5)
    assert t1 is t2  # Same cached object


def test_seek_to_frame(decoder):
    decoder.seek_to_frame(20)
    frame = decoder.decode_next_frame()
    assert frame is not None


def test_close_idempotent_workers(video_file, qapp):
    dec = VideoDecoder(video_file)
    dec.close()
    assert not dec._worker_running
    assert not dec._prefetch_running
    assert dec._container is None
