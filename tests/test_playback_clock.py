from src.playback_controller import PlaybackClock


class FakeAudioEngine:
    def __init__(self):
        self._time = 0.0

    def current_time(self):
        return self._time


def make_clock(fps=30.0):
    return FakeAudioEngine(), PlaybackClock.__new__(PlaybackClock)


def build(fps=30.0):
    engine = FakeAudioEngine()
    clock = PlaybackClock(engine, fps)
    return engine, clock


def test_starts_in_manual_mode_at_zero():
    _, clock = build()
    assert clock.current_time == 0.0
    assert clock.current_frame == 0


def test_set_manual_time():
    _, clock = build()
    clock.set_manual_time(2.5)
    assert clock.current_time == 2.5


def test_manual_time_clamped_to_zero():
    _, clock = build()
    clock.set_manual_time(-5.0)
    assert clock.current_time == 0.0


def test_sync_to_audio_uses_audio_time():
    engine, clock = build()
    engine._time = 7.0
    clock.sync_to_audio()
    assert clock.current_time == 7.0


def test_use_manual_captures_audio_time():
    engine, clock = build()
    engine._time = 3.0
    clock.sync_to_audio()
    clock.use_manual()
    engine._time = 9.0  # Audio keeps moving; manual time should not
    assert clock.current_time == 3.0


def test_step_by_frames():
    _, clock = build(fps=30.0)
    clock.set_manual_time(1.0)
    clock.step_by_frames(3)
    assert abs(clock.current_time - (1.0 + 3 / 30.0)) < 1e-9


def test_step_backward_clamped_to_zero():
    _, clock = build(fps=30.0)
    clock.set_manual_time(0.01)
    clock.step_by_frames(-10)
    assert clock.current_time == 0.0


def test_step_switches_to_manual():
    engine, clock = build()
    engine._time = 5.0
    clock.sync_to_audio()
    clock.step_by_frames(1)
    engine._time = 20.0
    assert clock.current_time < 10.0  # Not following audio anymore


def test_frame_duration_and_fps():
    _, clock = build(fps=25.0)
    assert clock.fps == 25.0
    assert abs(clock.frame_duration - 0.04) < 1e-9


def test_current_frame():
    _, clock = build(fps=30.0)
    clock.set_manual_time(1.0)
    assert clock.current_frame == 30
