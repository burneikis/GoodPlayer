import os

# Must be set before any PyQt6 import so tests run headless.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import av
import numpy as np
import pytest

VIDEO_FPS = 30
VIDEO_FRAMES = 60  # 2 seconds
VIDEO_SIZE = 64
AUDIO_RATE = 48000


@pytest.fixture(scope="session")
def qapp():
    """Session-wide QApplication (offscreen)."""
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="session")
def video_file(tmp_path_factory) -> str:
    """Generate a small test video (2s, 30fps, 64x64) with a stereo audio track."""
    path = tmp_path_factory.mktemp("media") / "test.mp4"

    with av.open(str(path), "w") as container:
        vstream = container.add_stream("mpeg4", rate=VIDEO_FPS)
        vstream.width = VIDEO_SIZE
        vstream.height = VIDEO_SIZE
        vstream.pix_fmt = "yuv420p"
        # Force frequent keyframes so seeking is testable
        vstream.gop_size = 10

        astream = container.add_stream("aac", rate=AUDIO_RATE)

        # Video: solid color ramp so frames are distinguishable
        for i in range(VIDEO_FRAMES):
            img = np.full(
                (VIDEO_SIZE, VIDEO_SIZE, 3),
                (i * 4) % 256,
                dtype=np.uint8,
            )
            frame = av.VideoFrame.from_ndarray(img, format="rgb24")
            for packet in vstream.encode(frame):
                container.mux(packet)
        for packet in vstream.encode():
            container.mux(packet)

        # Audio: 2 seconds of 440 Hz sine, stereo s16
        total_samples = AUDIO_RATE * VIDEO_FRAMES // VIDEO_FPS
        t = np.arange(total_samples) / AUDIO_RATE
        tone = (np.sin(2 * np.pi * 440 * t) * 0.3 * 32767).astype(np.int16)
        chunk = 1024
        for start in range(0, total_samples, chunk):
            samples = tone[start:start + chunk]
            # Interleaved stereo, shape (1, samples*channels) for packed s16
            interleaved = np.repeat(samples, 2).reshape(1, -1)
            aframe = av.AudioFrame.from_ndarray(interleaved, format="s16", layout="stereo")
            aframe.sample_rate = AUDIO_RATE
            for packet in astream.encode(aframe):
                container.mux(packet)
        for packet in astream.encode():
            container.mux(packet)

    return str(path)
