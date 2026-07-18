"""
Native Video Player using Qt6's QMediaPlayer for efficient playback.
Falls back gracefully if multimedia support is unavailable.
"""

import logging
from typing import Optional, Callable
from pathlib import Path

from PyQt6.QtCore import QUrl, QTimer, pyqtSignal, QObject, Qt, QRect, QSize
from PyQt6.QtGui import QPainter, QImage
from PyQt6.QtWidgets import QWidget, QSizePolicy

logger = logging.getLogger(__name__)

try:
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink, QVideoFrame
    QT_MULTIMEDIA_AVAILABLE = True
except ImportError:
    QT_MULTIMEDIA_AVAILABLE = False
    logger.warning("PyQt6-Multimedia not installed. Install with: pip install PyQt6-Multimedia")
    QMediaPlayer = None
    QAudioOutput = None
    QVideoSink = None
    QVideoFrame = None


class VideoSinkWidget(QWidget):
    """
    Widget that displays video frames via QVideoSink, painting them itself.

    Unlike QVideoWidget, this is a normal widget surface (no hardware video
    overlay plane). That means child-widget overlays (notifications, time
    info, etc.) composite correctly on every platform/WM — in particular,
    they no longer need to be separate top-level windows to render over the
    video. This fixes the i3/X11 fullscreen overlay issue.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # We paint the whole surface every frame, so let Qt skip the
        # background fill.
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(1, 1)
        self.setStyleSheet("background-color: black;")

        self._sink = QVideoSink(self) if QVideoSink is not None else None
        if self._sink is not None:
            self._sink.videoFrameChanged.connect(self._on_frame)
        self._image: "QImage | None" = None

    def videoSink(self):
        """Return the QVideoSink to wire into QMediaPlayer."""
        return self._sink

    def _on_frame(self, frame) -> None:
        if frame is None or not frame.isValid():
            return
        img = frame.toImage()
        if img.isNull():
            return
        self._image = img
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        if self._image is None or self._image.isNull():
            return
        target = self.rect()
        img_size = self._image.size()
        scaled = img_size.scaled(target.size(), Qt.AspectRatioMode.KeepAspectRatio)
        x = (target.width() - scaled.width()) // 2
        y = (target.height() - scaled.height()) // 2
        painter.drawImage(
            QRect(x, y, scaled.width(), scaled.height()),
            self._image,
        )

    def clear(self) -> None:
        self._image = None
        self.update()


class QtNativePlayer(QObject):
    """
    Native video player using Qt6's QMediaPlayer.
    
    Provides hardware-accelerated playback through Qt's multimedia framework.
    Can run in video-only mode to allow external audio handling (e.g., multi-track).
    """
    
    # Signals
    time_changed = pyqtSignal(float)
    duration_changed = pyqtSignal(float)
    playback_ended = pyqtSignal()
    error_occurred = pyqtSignal(str)
    
    def __init__(self, parent: Optional[QWidget] = None, video_only: bool = False):
        """
        Initialize the Qt native player.
        
        Args:
            parent: Parent widget to embed the video into.
            video_only: If True, mute audio so external audio engine can be used.
        """
        super().__init__(parent)
        
        if not QT_MULTIMEDIA_AVAILABLE:
            raise RuntimeError(
                "Qt Multimedia not available. Install with:\n"
                "  pip install PyQt6-Multimedia"
            )
        
        self._parent = parent
        self._filepath: Optional[str] = None
        self._video_only = video_only

        # Create video widget for display.
        # Use a QVideoSink-backed widget so overlays can be normal child
        # widgets (no hardware overlay plane to fight with).
        self._video_widget = VideoSinkWidget(parent)

        # Create audio output (muted if video_only mode)
        self._audio_output = QAudioOutput()
        if video_only:
            self._audio_output.setVolume(0.0)  # Mute - using external audio
            self._audio_output.setMuted(True)
        else:
            self._audio_output.setVolume(1.0)

        # Create media player
        self._player = QMediaPlayer()
        self._player.setVideoSink(self._video_widget.videoSink())
        self._player.setAudioOutput(self._audio_output)
        
        # Connect signals
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)
        self._player.errorOccurred.connect(self._on_error)
        
        # State
        self._duration: float = 0.0
        self._current_time: float = 0.0
        self._fps: float = 30.0  # Will be updated from video metadata
        
        logger.info("QtNativePlayer: Initialized")
    
    @property
    def video_widget(self) -> QWidget:
        """Get the video display widget for embedding."""
        return self._video_widget
    
    def _on_position_changed(self, position: int) -> None:
        """Handle position change (position is in milliseconds)."""
        self._current_time = position / 1000.0
        self.time_changed.emit(self._current_time)
    
    def _on_duration_changed(self, duration: int) -> None:
        """Handle duration change (duration is in milliseconds)."""
        self._duration = duration / 1000.0
        self.duration_changed.emit(self._duration)
    
    def _on_media_status_changed(self, status) -> None:
        """Handle media status changes."""
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.playback_ended.emit()
        elif status == QMediaPlayer.MediaStatus.LoadedMedia:
            logger.info(f"QtNativePlayer: Media loaded, duration={self._duration:.2f}s")
    
    def _on_error(self, error, error_string: str) -> None:
        """Handle player errors."""
        logger.error(f"QtNativePlayer error: {error_string}")
        self.error_occurred.emit(error_string)
    
    def open(self, filepath: str) -> bool:
        """
        Open a video file.
        
        Returns:
            True if file was opened successfully.
        """
        try:
            self._filepath = filepath
            url = QUrl.fromLocalFile(filepath)
            self._player.setSource(url)
            logger.info(f"QtNativePlayer: Opened {Path(filepath).name}")
            return True
        except Exception as e:
            logger.error(f"QtNativePlayer: Failed to open file: {e}")
            return False
    
    def play(self) -> None:
        """Start playback."""
        self._player.play()
    
    def pause(self) -> None:
        """Pause playback."""
        self._player.pause()
    
    def stop(self) -> None:
        """Stop playback."""
        self._player.stop()
    
    def toggle_playback(self) -> None:
        """Toggle play/pause."""
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.pause()
        else:
            self.play()
    
    def seek(self, time_seconds: float) -> None:
        """Seek to a specific time in seconds."""
        position_ms = int(time_seconds * 1000)
        self._player.setPosition(position_ms)
    
    def seek_ms(self, position_ms: int) -> None:
        """Seek to a specific position in milliseconds."""
        self._player.setPosition(position_ms)
    
    def step_forward(self) -> None:
        """Step forward one frame (approximate)."""
        frame_time_ms = int(1000 / self._fps)
        new_pos = self._player.position() + frame_time_ms
        self._player.setPosition(new_pos)
    
    def step_backward(self) -> None:
        """Step backward one frame (approximate)."""
        frame_time_ms = int(1000 / self._fps)
        new_pos = max(0, self._player.position() - frame_time_ms)
        self._player.setPosition(new_pos)
    
    def set_volume(self, volume: float) -> None:
        """Set volume (0.0 to 1.0)."""
        self._audio_output.setVolume(max(0.0, min(1.0, volume)))
    
    def set_muted(self, muted: bool) -> None:
        """Set mute state."""
        self._audio_output.setMuted(muted)
    
    def set_playback_rate(self, rate: float) -> None:
        """Set playback speed (1.0 = normal)."""
        self._player.setPlaybackRate(rate)
    
    def set_fps(self, fps: float) -> None:
        """Set the FPS for frame stepping calculations."""
        self._fps = fps if fps > 0 else 30.0
    
    @property
    def current_time(self) -> float:
        """Current playback time in seconds."""
        return self._player.position() / 1000.0
    
    @property
    def duration(self) -> float:
        """Total duration in seconds."""
        return self._duration
    
    @property
    def is_playing(self) -> bool:
        """Whether playback is active."""
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
    
    @property
    def filepath(self) -> Optional[str]:
        """Currently loaded file path."""
        return self._filepath
    
    def close(self) -> None:
        """Release resources."""
        self._player.stop()
        self._player.setSource(QUrl())
        self._filepath = None
    
    def show(self) -> None:
        """Show the video widget."""
        self._video_widget.show()
    
    def hide(self) -> None:
        """Hide the video widget."""
        self._video_widget.hide()
    
    def resize(self, width: int, height: int) -> None:
        """Resize the video widget."""
        self._video_widget.resize(width, height)


def is_available() -> bool:
    """Check if Qt native player is available."""
    return QT_MULTIMEDIA_AVAILABLE
