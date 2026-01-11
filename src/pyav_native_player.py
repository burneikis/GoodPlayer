"""
PyAV Native Video Player for GoodPlayer

A high-performance video player using PyAV for decoding and Qt for display.
Integrates DoesPlayer's timer-based frame display system into GoodPlayer.
Supports smooth playback with multi-track audio via AudioEngine.
"""

import time
import queue
import threading
import logging
from dataclasses import dataclass
from typing import Optional, Callable

import av
import numpy as np
from PyQt6.QtCore import QTimer, pyqtSignal, QObject
from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

# Constants
FRAME_QUEUE_SIZE = 60
PAUSE_POLL_INTERVAL = 0.05  # seconds
QUEUE_PUT_TIMEOUT = 0.02  # seconds
DEFAULT_FPS = 30.0


@dataclass
class VideoFrame:
    """Container for decoded video frame data."""
    image: np.ndarray  # RGB frame data
    pts: float  # Presentation timestamp in seconds
    frame_number: int


class VideoDecoderThread(threading.Thread):
    """
    Decodes video frames from a media file using PyAV.
    Runs in a separate thread and feeds frames to a queue for display.
    """

    def __init__(
        self,
        file_path: str,
        frame_queue: queue.Queue,
        on_duration: Optional[Callable[[float], None]] = None,
        on_fps: Optional[Callable[[float], None]] = None,
    ):
        super().__init__(daemon=True)
        self.file_path = file_path
        self.frame_queue = frame_queue
        self.on_duration = on_duration
        self.on_fps = on_fps

        self._running = False
        self._paused = False
        self._finished = False
        self._seek_requested = False
        self._seek_target = 0.0
        self._lock = threading.Lock()

        self.container: Optional[av.container.InputContainer] = None
        self.video_stream = None
        self.duration: float = 0.0
        self.fps: float = DEFAULT_FPS
        self.width: int = 0
        self.height: int = 0

    def open(self) -> bool:
        """Open the video file and extract metadata."""
        try:
            self.container = av.open(self.file_path)

            # Find video stream
            for stream in self.container.streams:
                if stream.type == 'video':
                    self.video_stream = stream
                    break

            if self.video_stream is None:
                logger.error("No video stream found")
                return False

            # Extract metadata
            if self.container.duration:
                self.duration = float(self.container.duration / av.time_base)
            else:
                self.duration = 0.0

            if self.video_stream.average_rate:
                self.fps = float(self.video_stream.average_rate)
            else:
                self.fps = DEFAULT_FPS

            self.width = self.video_stream.width
            self.height = self.video_stream.height

            # Enable multi-threaded decoding
            self.video_stream.thread_type = "AUTO"

            if self.on_duration:
                self.on_duration(self.duration)
            if self.on_fps:
                self.on_fps(self.fps)

            return True

        except Exception as e:
            logger.error(f"Error opening video: {e}")
            return False

    def run(self):
        """Main decoding loop."""
        if not self.container or not self.video_stream:
            return

        self._running = True
        self._finished = False
        frame_number = 0

        try:
            while self._running:
                # Handle pause
                while self._paused and self._running:
                    time.sleep(PAUSE_POLL_INTERVAL)

                if not self._running:
                    break

                # Handle seek
                with self._lock:
                    if self._seek_requested:
                        self._perform_seek()
                        self._seek_requested = False
                        frame_number = int(self._seek_target * self.fps)
                        # Clear the queue after seeking
                        while not self.frame_queue.empty():
                            try:
                                self.frame_queue.get_nowait()
                            except queue.Empty:
                                break

                # Decode frames
                try:
                    for frame in self.container.decode(video=0):
                        if not self._running:
                            break

                        with self._lock:
                            if self._seek_requested:
                                break

                        while self._paused and self._running:
                            time.sleep(PAUSE_POLL_INTERVAL)

                        if not self._running:
                            break

                        # Convert to RGB
                        rgb_frame = frame.to_ndarray(format='rgb24')

                        # Calculate PTS
                        if frame.pts is not None:
                            pts = float(frame.pts * self.video_stream.time_base)
                        else:
                            pts = frame_number / self.fps

                        video_frame = VideoFrame(
                            image=rgb_frame,
                            pts=pts,
                            frame_number=frame_number
                        )

                        # Put frame in queue
                        while self._running and not self._seek_requested and not self._paused:
                            try:
                                self.frame_queue.put(video_frame, timeout=QUEUE_PUT_TIMEOUT)
                                break
                            except queue.Full:
                                continue

                        frame_number += 1
                    else:
                        # End of stream
                        self._finished = True
                        self._running = False
                        break

                except av.error.EOFError:
                    self._finished = True
                    self._running = False
                    break
                except Exception as e:
                    logger.error(f"Decode error: {e}")
                    continue

        finally:
            self._finished = True

    def _perform_seek(self):
        """Perform the actual seek operation."""
        if self.container and self.video_stream:
            target_ts = int(self._seek_target / self.video_stream.time_base)
            try:
                self.container.seek(target_ts, stream=self.video_stream)
            except Exception as e:
                logger.error(f"Seek error: {e}")

    def seek(self, position: float):
        """Request a seek to the specified position in seconds."""
        with self._lock:
            self._seek_target = max(0.0, min(position, self.duration))
            self._seek_requested = True
            self._finished = False

    @property
    def is_finished(self) -> bool:
        """Check if decoder has reached end of stream."""
        return self._finished and not self._running

    def pause(self):
        """Pause video decoding."""
        self._paused = True

    def resume(self):
        """Resume video decoding."""
        if not self._running:
            return
        self._paused = False

    def stop(self):
        """Stop the decoder thread."""
        self._running = False
        self._paused = False
        if self.container:
            self.container.close()


class PyAVNativePlayer(QObject):
    """
    PyAV-based native video player for GoodPlayer.

    Uses PyAV for video decoding with timer-based frame display.
    Audio is handled by the external AudioEngine (multi-track support).

    This replaces QtNativePlayer while providing similar API compatibility.
    """

    # Signals
    time_changed = pyqtSignal(float)
    duration_changed = pyqtSignal(float)
    playback_ended = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, video_widget: QWidget):
        """
        Initialize the PyAV native player.

        Args:
            video_widget: Widget to display video frames on (VideoWidget instance)
        """
        super().__init__()

        self._video_widget = video_widget
        self._filepath: Optional[str] = None

        self._frame_queue: queue.Queue = queue.Queue(maxsize=FRAME_QUEUE_SIZE)
        self._decoder: Optional[VideoDecoderThread] = None

        self._is_playing = False
        self._duration = 0.0
        self._fps = DEFAULT_FPS

        # Playback timing
        self._playback_start_time = 0.0
        self._playback_start_pts = 0.0
        self._current_pts = 0.0
        self._pending_frame: Optional[VideoFrame] = None

        # Frame display timer
        self._display_timer = QTimer()
        self._display_timer.timeout.connect(self._on_display_tick)

        # Position update timer
        self._position_timer = QTimer()
        self._position_timer.timeout.connect(self._update_position)
        self._position_timer.setInterval(100)  # 100ms updates

    def _on_duration_received(self, duration: float):
        """Handle duration info from decoder."""
        self._duration = duration
        self.duration_changed.emit(duration)

    def _on_fps_received(self, fps: float):
        """Handle FPS info from decoder."""
        self._fps = fps
        frame_interval = max(1, int(1000 / fps / 2))
        self._display_timer.setInterval(frame_interval)

    def open(self, filepath: str) -> bool:
        """Open a video file."""
        try:
            self.close()

            self._filepath = filepath
            self._frame_queue = queue.Queue(maxsize=FRAME_QUEUE_SIZE)

            self._decoder = VideoDecoderThread(
                file_path=filepath,
                frame_queue=self._frame_queue,
                on_duration=self._on_duration_received,
                on_fps=self._on_fps_received,
            )

            if not self._decoder.open():
                logger.error("Failed to open video file")
                return False

            # Set timer interval based on FPS
            frame_interval = max(1, int(1000 / self._decoder.fps / 2))
            self._display_timer.setInterval(frame_interval)

            logger.info(f"PyAVNativePlayer: Opened {filepath}")
            logger.info(f"  Resolution: {self._decoder.width}x{self._decoder.height}")
            logger.info(f"  Duration: {self._decoder.duration:.2f}s, FPS: {self._decoder.fps:.2f}")

            return True

        except Exception as e:
            logger.error(f"Failed to open file: {e}")
            self.error_occurred.emit(str(e))
            return False

    def play(self) -> None:
        """Start or resume playback."""
        if not self._decoder:
            return

        # Don't play if at end of stream
        if self._decoder.is_finished and self._current_pts >= self._duration - 0.01:
            return

        if not self._is_playing:
            self._is_playing = True

            # Start decoder if not running
            if not self._decoder.is_alive():
                self._decoder.start()
                # Give decoder time to buffer frames
                QTimer.singleShot(100, self._start_playback)
            else:
                self._decoder.resume()
                self._start_playback()

    def _start_playback(self):
        """Actually start playback after decoder is ready."""
        if not self._is_playing:
            return

        self._playback_start_time = time.perf_counter()
        self._playback_start_pts = self._current_pts

        self._display_timer.start()
        self._position_timer.start()

    def pause(self) -> None:
        """Pause playback."""
        if self._is_playing:
            self._is_playing = False

            self._display_timer.stop()
            self._position_timer.stop()

            self._current_pts = self._get_playback_time()

            if self._decoder:
                self._decoder.pause()

    def _get_playback_time(self) -> float:
        """Get current playback time based on system clock."""
        if not self._is_playing:
            return self._current_pts
        elapsed = time.perf_counter() - self._playback_start_time
        return self._playback_start_pts + elapsed

    def seek(self, time_seconds: float) -> None:
        """Seek to a specific time in seconds."""
        self._current_pts = time_seconds
        self._playback_start_pts = time_seconds
        self._playback_start_time = time.perf_counter()
        self._pending_frame = None

        # Clear frame queue
        while not self._frame_queue.empty():
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                break

        if self._decoder:
            if self._decoder.is_finished or not self._decoder.is_alive():
                # Need to recreate decoder
                file_path = self._decoder.file_path
                self._decoder.stop()

                self._frame_queue = queue.Queue(maxsize=FRAME_QUEUE_SIZE)
                self._decoder = VideoDecoderThread(
                    file_path=file_path,
                    frame_queue=self._frame_queue,
                    on_duration=self._on_duration_received,
                    on_fps=self._on_fps_received,
                )
                self._decoder.open()
                self._decoder.seek(time_seconds)

                if self._is_playing:
                    self._decoder.start()
            else:
                self._decoder.seek(time_seconds)

        self.time_changed.emit(time_seconds)

    def _on_display_tick(self):
        """Called by timer to display next frame."""
        if not self._is_playing:
            return

        current_time = self._get_playback_time()
        frame_to_display = None

        # Check pending frame
        if self._pending_frame is not None:
            if self._pending_frame.pts <= current_time:
                frame_to_display = self._pending_frame
                self._pending_frame = None
            else:
                return

        # Get frames from queue
        while True:
            try:
                frame = self._frame_queue.get_nowait()
                if frame.pts <= current_time:
                    frame_to_display = frame
                else:
                    self._pending_frame = frame
                    break
            except queue.Empty:
                break

        # Display the frame
        if frame_to_display is not None:
            self._video_widget.display_frame(frame_to_display.image)
            self._current_pts = frame_to_display.pts

    def _update_position(self):
        """Update the position display."""
        if self._is_playing:
            current_time = self._get_playback_time()

            # Check for end of video
            if current_time >= self._duration:
                current_time = self._duration
                self.pause()
                self.playback_ended.emit()
                return

            self.time_changed.emit(current_time)

    def stop(self) -> None:
        """Stop playback completely."""
        self._is_playing = False
        self._display_timer.stop()
        self._position_timer.stop()

        if self._decoder:
            self._decoder.stop()
            self._decoder = None

        # Clear queue
        while not self._frame_queue.empty():
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                break

        self._current_pts = 0.0
        self._pending_frame = None

    def close(self) -> None:
        """Release all resources."""
        self.stop()
        self._filepath = None

    def set_fps(self, fps: float) -> None:
        """Set the FPS (used for frame stepping calculations)."""
        self._fps = fps if fps > 0 else DEFAULT_FPS

    @property
    def current_time(self) -> float:
        """Current playback time in seconds."""
        return self._get_playback_time()

    @property
    def duration(self) -> float:
        """Total duration in seconds."""
        return self._duration

    @property
    def is_playing(self) -> bool:
        """Whether playback is active."""
        return self._is_playing

    @property
    def filepath(self) -> Optional[str]:
        """Currently loaded file path."""
        return self._filepath

    @property
    def video_widget(self) -> QWidget:
        """Get the video display widget."""
        return self._video_widget


def is_available() -> bool:
    """Check if PyAV native player is available."""
    try:
        import av
        return True
    except ImportError:
        return False
