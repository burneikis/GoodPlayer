import pytest

from src.playback_controller import PlaybackController

from .conftest import VIDEO_FPS, VIDEO_FRAMES


@pytest.fixture
def controller(video_file, qapp):
    ctrl = PlaybackController(video_file)
    yield ctrl
    ctrl.close()


def test_initial_state(controller):
    assert not controller.is_playing
    assert controller.current_time == 0.0
    assert controller.current_frame == 0
    assert controller.total_frames == VIDEO_FRAMES
    assert abs(controller.fps - VIDEO_FPS) < 0.1
    assert controller.num_audio_tracks == 1


def test_get_current_frame(controller):
    frame = controller.get_current_frame()
    assert frame is not None


def test_seek(controller):
    controller.seek(1.0)
    assert abs(controller.current_time - 1.0) < 1e-6
    assert controller.current_frame == VIDEO_FPS


def test_seek_clamped_to_duration(controller):
    controller.seek(9999.0)
    assert controller.current_time <= controller.duration


def test_seek_to_frame(controller):
    controller.seek_to_frame(15)
    assert controller.current_frame == 15


def test_step_forward(controller):
    controller.seek_to_frame(10)
    frame = controller.step_forward()
    assert frame is not None
    assert controller.current_frame == 11


def test_step_backward(controller):
    controller.seek_to_frame(10)
    controller.step_backward()
    assert controller.current_frame == 9


def test_step_backward_clamped(controller):
    controller.seek(0.0)
    controller.step_backward(5)
    assert controller.current_frame == 0


def test_get_frame_at_time(controller):
    frame = controller.get_frame_at_time(0.5)
    assert frame is not None
