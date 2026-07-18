import time

import pytest

from src.audio_engine import AudioEngine

from .conftest import AUDIO_RATE


@pytest.fixture
def engine(video_file):
    eng = AudioEngine(video_file)
    yield eng
    eng.close()


def test_opens_audio_track(engine):
    assert engine.num_tracks == 1
    assert engine.sample_rate == AudioEngine.TARGET_SAMPLE_RATE


def test_not_playing_initially(engine):
    assert not engine.is_playing
    assert engine.current_time() == 0.0


def test_decoder_fills_buffer(engine):
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if engine._tracks[0].buffer.available() > 0:
            break
        time.sleep(0.05)
    assert engine._tracks[0].buffer.available() > 0


def test_seek_updates_time(engine):
    engine.seek(1.0, blocking=True)
    assert abs(engine.current_time() - 1.0) < 0.05


def test_seek_negative_clamped(engine):
    engine.seek(-5.0, blocking=True)
    assert engine.current_time() >= 0.0


def test_nonblocking_seek_returns_quickly(engine):
    start = time.monotonic()
    engine.seek(0.5, blocking=False)
    assert time.monotonic() - start < 0.1


def test_track_volume_and_mute(engine):
    engine.set_track_volume(0, 0.5)
    assert engine._tracks[0].volume == 0.5
    engine.set_track_volume(0, 5.0)  # Clamped
    assert engine._tracks[0].volume == 1.0
    engine.set_track_muted(0, True)
    assert engine._tracks[0].muted

    # Out-of-range indices are ignored, not errors
    engine.set_track_volume(99, 0.5)
    engine.set_track_muted(99, True)


def test_stats_keys(engine):
    stats = engine.stats
    for key in ("callback_underruns", "track_buffer_underruns",
                "track_buffer_dropped", "total_callbacks", "underrun_rate"):
        assert key in stats


def test_close_stops_decoder(video_file):
    eng = AudioEngine(video_file)
    eng.close()
    assert not eng._decoder_running
    assert eng._container is None
