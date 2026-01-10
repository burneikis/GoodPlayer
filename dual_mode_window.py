"""
Dual-Mode Main Window
Supports both native playback (Qt Multimedia) for smooth viewing
and frame-accurate mode (PyAV) for precise control.
"""

import sys
import logging
import numpy as np
from typing import Optional
from enum import Enum, auto

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSlider, QFileDialog, QSizePolicy, QCheckBox,
    QFrame, QScrollArea, QStackedWidget
)
from PyQt6.QtCore import Qt, QTimer, QElapsedTimer
from PyQt6.QtGui import QImage, QPixmap, QKeyEvent

from playback_controller import PlaybackController
from video_decoder import VideoDecoder

logger = logging.getLogger(__name__)

# Check for Qt Multimedia availability
try:
    from qt_native_player import QtNativePlayer, is_available as qt_native_available
    QT_NATIVE_AVAILABLE = qt_native_available()
except ImportError:
    QT_NATIVE_AVAILABLE = False
    QtNativePlayer = None


class PlaybackMode(Enum):
    """Current playback mode."""
    NATIVE = auto()        # Qt Multimedia for smooth playback
    FRAME_ACCURATE = auto()  # PyAV for frame-by-frame control


class AudioTrackWidget(QFrame):
    """Widget for controlling a single audio track."""

    def __init__(self, track_index: int, parent=None):
        super().__init__(parent)
        self.track_index = track_index
        self._setup_ui()

    def _setup_ui(self):
        self.setFrameStyle(QFrame.Shape.StyledPanel)
        self.setStyleSheet("background-color: #3a3a3a; border-radius: 4px; padding: 5px;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)

        self._label = QLabel(f"Track {self.track_index + 1}")
        self._label.setStyleSheet("color: white; font-weight: bold;")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._label)

        self._volume_slider = QSlider(Qt.Orientation.Vertical)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(100)
        self._volume_slider.setMinimumHeight(80)
        self._volume_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        layout.addWidget(self._volume_slider, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._volume_label = QLabel("100%")
        self._volume_label.setStyleSheet("color: #aaa;")
        self._volume_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._volume_label)

        self._mute_checkbox = QCheckBox("Mute")
        self._mute_checkbox.setStyleSheet("color: white;")
        self._mute_checkbox.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._mute_checkbox.stateChanged.connect(self._on_mute_changed)
        layout.addWidget(self._mute_checkbox, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._volume_callback = None
        self._mute_callback = None

    def set_volume_callback(self, callback):
        self._volume_callback = callback

    def set_mute_callback(self, callback):
        self._mute_callback = callback

    def _on_volume_changed(self, value: int):
        self._volume_label.setText(f"{value}%")
        if self._volume_callback:
            self._volume_callback(self.track_index, value / 100.0)

    def _on_mute_changed(self, state: int):
        if self._mute_callback:
            self._mute_callback(self.track_index, state == Qt.CheckState.Checked.value)

    def set_volume(self, volume: float):
        self._volume_slider.blockSignals(True)
        self._volume_slider.setValue(int(volume * 100))
        self._volume_label.setText(f"{int(volume * 100)}%")
        self._volume_slider.blockSignals(False)


class AudioMixerPanel(QFrame):
    """Panel for audio mixing controls."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._track_widgets = []
        self._setup_ui()

    def _setup_ui(self):
        self.setStyleSheet("background-color: #2b2b2b;")
        self.setFixedWidth(120)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        header = QLabel("Audio Mixer")
        header.setStyleSheet("color: white; font-weight: bold; font-size: 12px;")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none;")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._tracks_container = QWidget()
        self._tracks_layout = QVBoxLayout(self._tracks_container)
        self._tracks_layout.setContentsMargins(0, 0, 0, 0)
        self._tracks_layout.setSpacing(5)
        self._tracks_layout.addStretch()

        scroll.setWidget(self._tracks_container)
        layout.addWidget(scroll)

        self._no_tracks_label = QLabel("No audio\ntracks")
        self._no_tracks_label.setStyleSheet("color: #666;")
        self._no_tracks_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tracks_layout.insertWidget(0, self._no_tracks_label)

    def setup_tracks(self, num_tracks: int, volume_callback, mute_callback):
        for widget in self._track_widgets:
            self._tracks_layout.removeWidget(widget)
            widget.deleteLater()
        self._track_widgets.clear()

        self._no_tracks_label.setVisible(num_tracks == 0)

        for i in range(num_tracks):
            track_widget = AudioTrackWidget(i)
            track_widget.set_volume_callback(volume_callback)
            track_widget.set_mute_callback(mute_callback)
            self._track_widgets.append(track_widget)
            self._tracks_layout.insertWidget(i, track_widget)

    def set_track_volume(self, track_index: int, volume: float):
        if 0 <= track_index < len(self._track_widgets):
            self._track_widgets[track_index].set_volume(volume)


class NotificationOverlay(QLabel):
    """Overlay widget for showing action notifications."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("""
            background-color: rgba(0, 0, 0, 180);
            color: white;
            font-size: 18px;
            font-weight: bold;
            padding: 10px 20px;
            border-radius: 8px;
        """)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_notification(self, text: str, duration_ms: int = 1000) -> None:
        self.setText(text)
        self.adjustSize()
        if self.parent():
            parent_rect = self.parent().rect()
            margin = 10
            self.move(parent_rect.width() - self.width() - margin, margin)
        self.show()
        self.raise_()
        self._timer.start(duration_ms)


class WelcomeOverlay(QLabel):
    """Overlay widget prompting user to open a file."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setText("Click or drop a video here\nor press Ctrl/Cmd+O to Open")
        self.setStyleSheet("""
            QLabel {
                background-color: rgba(0, 0, 0, 200);
                color: #aaaaaa;
                font-size: 20px;
                padding: 40px;
                border: 2px dashed #555555;
                border-radius: 12px;
            }
            QLabel:hover {
                color: #ffffff;
                border-color: #888888;
                background-color: rgba(0, 0, 0, 220);
            }
        """)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._click_callback = None

    def set_click_callback(self, callback):
        self._click_callback = callback

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._click_callback:
            self._click_callback()
        super().mousePressEvent(event)


class ClickableSlider(QSlider):
    """A slider that responds to mouse clicks anywhere on the track."""

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.orientation() == Qt.Orientation.Horizontal:
                value = self.minimum() + (self.maximum() - self.minimum()) * event.position().x() / self.width()
            else:
                value = self.minimum() + (self.maximum() - self.minimum()) * (self.height() - event.position().y()) / self.height()
            self.setValue(int(value))
            self.sliderPressed.emit()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.sliderReleased.emit()
        super().mouseReleaseEvent(event)


class FrameAccurateVideoWidget(QLabel):
    """Widget for displaying video frames (frame-accurate mode)."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(640, 360)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background-color: black;")
        self._aspect_ratio: float = 16 / 9
    
    def display_frame(self, frame: np.ndarray) -> None:
        if frame is None:
            return
        
        height, width, channels = frame.shape
        self._aspect_ratio = width / height
        
        bytes_per_line = channels * width
        qimage = QImage(frame.data, width, height, bytes_per_line, QImage.Format.Format_RGB888)
        
        pixmap = QPixmap.fromImage(qimage)
        scaled = pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(scaled)
    
    def clear_display(self) -> None:
        self.clear()
        self.setStyleSheet("background-color: black;")


class ModeIndicator(QLabel):
    """Small indicator showing current playback mode."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            background-color: rgba(0, 0, 0, 150);
            color: white;
            font-size: 10px;
            padding: 2px 6px;
            border-radius: 3px;
        """)
        self.hide()
    
    def set_mode(self, mode: PlaybackMode) -> None:
        if mode == PlaybackMode.NATIVE:
            self.setText("🎬 Native")
            self.setStyleSheet("""
                background-color: rgba(0, 128, 0, 180);
                color: white;
                font-size: 10px;
                padding: 2px 6px;
                border-radius: 3px;
            """)
        else:
            self.setText("🎯 Frame")
            self.setStyleSheet("""
                background-color: rgba(0, 100, 200, 180);
                color: white;
                font-size: 10px;
                padding: 2px 6px;
                border-radius: 3px;
            """)
        self.adjustSize()
        self.show()


class DualModeMainWindow(QMainWindow):
    """
    Main window supporting dual playback modes:
    - Native mode: Hardware-accelerated playback via Qt Multimedia
    - Frame-accurate mode: PyAV-based frame-by-frame control
    """
    
    DEFAULT_REFRESH_INTERVAL_MS = 16
    STATS_INTERVAL_MS = 5000
    REFRESH_HEADROOM_FACTOR = 0.85
    
    def __init__(self):
        super().__init__()
        
        # Controllers
        self._frame_controller: Optional[PlaybackController] = None
        self._native_player: Optional[QtNativePlayer] = None
        
        # Current mode
        self._mode = PlaybackMode.FRAME_ACCURATE
        self._prefer_native = QT_NATIVE_AVAILABLE
        
        # State
        self._last_displayed_frame: int = -1
        self._master_volume: float = 1.0
        self._was_playing_before_seek: bool = False

        # Performance tracking
        self._frames_displayed = 0
        self._frames_dropped = 0
        self._frame_timer = QElapsedTimer()
        
        self._setup_ui()
        self._setup_timers()
        
        logger.info(f"DualModeMainWindow: Native player {'available' if QT_NATIVE_AVAILABLE else 'not available'}")
    
    def _setup_ui(self) -> None:
        self.setWindowTitle("GoodPlayer")
        self.setMinimumSize(800, 600)
        
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        content_area = QWidget()
        content_layout = QHBoxLayout(content_area)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Video container (stacked widget for switching between modes)
        self._video_container = QWidget()
        self._video_container.setStyleSheet("background-color: black;")
        self._video_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        video_layout = QVBoxLayout(self._video_container)
        video_layout.setContentsMargins(0, 0, 0, 0)
        
        # Stacked widget to switch between native and frame-accurate display
        self._video_stack = QStackedWidget()
        video_layout.addWidget(self._video_stack)
        
        # Frame-accurate video widget (index 0)
        self._frame_video_widget = FrameAccurateVideoWidget()
        self._video_stack.addWidget(self._frame_video_widget)
        
        # Native video widget placeholder (index 1) - created when needed
        self._native_widget_placeholder = QWidget()
        self._native_widget_placeholder.setStyleSheet("background-color: black;")
        self._video_stack.addWidget(self._native_widget_placeholder)
        
        content_layout.addWidget(self._video_container, stretch=1)

        # Audio mixer panel
        self._audio_mixer = AudioMixerPanel()
        self._audio_mixer.hide()
        content_layout.addWidget(self._audio_mixer)

        main_layout.addWidget(content_area, stretch=1)

        # Overlays (on top of video container)
        self._welcome_overlay = WelcomeOverlay(self._video_container)
        self._welcome_overlay.set_click_callback(self._open_file)

        self._notification = NotificationOverlay(self._video_container)
        
        # Mode indicator
        self._mode_indicator = ModeIndicator(self._video_container)

        # Time info overlay
        self._time_label = QLabel("00:00.000 / 00:00.000", self._video_container)
        self._time_label.setStyleSheet("color: white; font-family: monospace; background: transparent;")
        self._time_label.adjustSize()
        self._time_label.hide()

        # Frame info overlay
        self._frame_label = QLabel("Frame: 0 / 0", self._video_container)
        self._frame_label.setStyleSheet("color: white; font-family: monospace; background: transparent;")
        self._frame_label.adjustSize()
        self._frame_label.hide()

        # Controls panel
        controls_panel = QWidget()
        controls_panel.setStyleSheet("background-color: #2b2b2b;")
        controls_layout = QVBoxLayout(controls_panel)
        controls_layout.setContentsMargins(10, 5, 10, 5)

        # Timeline slider
        self._timeline_slider = ClickableSlider(Qt.Orientation.Horizontal)
        self._timeline_slider.setEnabled(False)
        self._timeline_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._timeline_slider.sliderPressed.connect(self._on_slider_pressed)
        self._timeline_slider.sliderReleased.connect(self._on_slider_released)
        self._timeline_slider.valueChanged.connect(self._on_slider_value_changed)
        controls_layout.addWidget(self._timeline_slider)

        main_layout.addWidget(controls_panel)
        
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    
    def _setup_timers(self) -> None:
        self._refresh_timer = QTimer()
        self._refresh_timer.setInterval(self.DEFAULT_REFRESH_INTERVAL_MS)
        self._refresh_timer.timeout.connect(self._refresh_display)
        
        self._stats_timer = QTimer()
        self._stats_timer.setInterval(self.STATS_INTERVAL_MS)
        self._stats_timer.timeout.connect(self._log_stats)
    
    def _init_native_player(self) -> bool:
        """Initialize native player if available."""
        if not QT_NATIVE_AVAILABLE:
            return False
        
        if self._native_player is not None:
            return True
        
        try:
            # Create native player in VIDEO-ONLY mode (audio handled by AudioEngine)
            self._native_player = QtNativePlayer(self._video_container, video_only=True)
            
            # Replace placeholder with native video widget
            self._video_stack.removeWidget(self._native_widget_placeholder)
            self._video_stack.insertWidget(1, self._native_player.video_widget)
            
            # Connect signals
            self._native_player.time_changed.connect(self._on_native_time_changed)
            self._native_player.playback_ended.connect(self._on_native_playback_ended)
            
            logger.info("Native player initialized (video-only, audio via AudioEngine)")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize native player: {e}")
            self._native_player = None
            return False
    
    def _switch_to_native_mode(self) -> bool:
        """Switch to native playback mode (video via Qt, audio via AudioEngine)."""
        if not self._prefer_native or not self._init_native_player():
            return False
        
        if self._mode == PlaybackMode.NATIVE:
            return True
        
        # Get current position from frame-accurate controller
        current_time = 0.0
        if self._frame_controller:
            current_time = self._frame_controller.current_time
        
        # Sync native player (video only)
        if self._native_player:
            self._native_player.seek(current_time)
            self._video_stack.setCurrentIndex(1)  # Show native widget
        
        self._mode = PlaybackMode.NATIVE
        self._mode_indicator.set_mode(PlaybackMode.NATIVE)
        logger.debug("Switched to NATIVE mode (video-only)")
        return True
    
    def _switch_to_frame_accurate_mode(self) -> None:
        """Switch to frame-accurate mode."""
        if self._mode == PlaybackMode.FRAME_ACCURATE:
            return
        
        # Get current position from native player
        current_time = 0.0
        if self._native_player:
            current_time = self._native_player.current_time
            self._native_player.pause()
        
        # Pause audio (it's shared between modes)
        if self._frame_controller:
            self._frame_controller.audio_engine.pause()
            self._frame_controller.seek(current_time)
            self._display_current_frame()
        
        self._video_stack.setCurrentIndex(0)  # Show frame-accurate widget
        self._mode = PlaybackMode.FRAME_ACCURATE
        self._mode_indicator.set_mode(PlaybackMode.FRAME_ACCURATE)
        logger.debug("Switched to FRAME_ACCURATE mode")
    
    def _on_native_time_changed(self, time_pos: float) -> None:
        """Handle time updates from native player - keep audio in sync."""
        if self._mode == PlaybackMode.NATIVE:
            self._update_time_display_from_time(time_pos)
            
            # Check for audio drift and resync if needed
            if self._frame_controller:
                audio_time = self._frame_controller.audio_engine.current_time()
                drift = abs(time_pos - audio_time)
                # Resync if drift exceeds 100ms
                if drift > 0.1:
                    logger.debug(f"Audio drift detected: {drift:.3f}s, resyncing")
                    self._frame_controller.audio_engine.seek(time_pos)
                    self._frame_controller.audio_engine.play()
    
    def _on_native_playback_ended(self) -> None:
        """Handle end of native playback."""
        # Stop audio engine
        if self._frame_controller:
            self._frame_controller.audio_engine.pause()
        self._switch_to_frame_accurate_mode()
        self._show_notification("End", 600)
    
    def _open_file(self) -> None:
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Open Video", "",
            "Video Files (*.mp4 *.mkv *.avi *.mov *.webm);;All Files (*)"
        )
        if filepath:
            self._load_file(filepath)
    
    def _load_file(self, filepath: str) -> None:
        # Close existing
        if self._frame_controller:
            self._refresh_timer.stop()
            self._stats_timer.stop()
            self._frame_controller.close()
        
        if self._native_player:
            self._native_player.close()
        
        # Reset stats
        self._frames_displayed = 0
        self._frames_dropped = 0
        
        try:
            # Initialize frame-accurate controller (always needed)
            self._frame_controller = PlaybackController(filepath)
            
            # Initialize native player if available
            if self._init_native_player():
                self._native_player.open(filepath)
                self._native_player.set_fps(self._frame_controller.fps)
            
            # Hide welcome overlay, show info labels
            self._welcome_overlay.hide()
            self._time_label.show()
            self._frame_label.show()
            self._mode_indicator.show()
            
            # Enable timeline
            self._timeline_slider.setEnabled(True)
            self._timeline_slider.setRange(0, self._frame_controller.total_frames - 1)
            self._timeline_slider.setValue(0)

            # Setup audio mixer
            self._audio_mixer.setup_tracks(
                self._frame_controller.num_audio_tracks,
                self._on_track_volume_changed,
                self._on_track_mute_changed
            )

            # Calculate refresh interval
            video_fps = self._frame_controller.fps
            if video_fps > 0:
                frame_time_ms = 1000.0 / video_fps
                refresh_interval = max(1, int(frame_time_ms * self.REFRESH_HEADROOM_FACTOR))
            else:
                refresh_interval = self.DEFAULT_REFRESH_INTERVAL_MS
            
            self._refresh_timer.setInterval(refresh_interval)
            logger.info(f"Video FPS: {video_fps:.2f}, refresh interval: {refresh_interval}ms")

            # Start in frame-accurate mode, display first frame
            self._mode = PlaybackMode.FRAME_ACCURATE
            self._mode_indicator.set_mode(PlaybackMode.FRAME_ACCURATE)
            self._video_stack.setCurrentIndex(0)
            self._display_current_frame()
            self._update_time_display()
            
            # Start timers
            self._refresh_timer.start()
            self._stats_timer.start()
            self._frame_timer.start()
            
            self.setWindowTitle(f"GoodPlayer - {filepath}")
            
        except Exception as e:
            logger.error(f"Failed to load file: {e}")
            self._frame_video_widget.clear_display()
            self._time_label.setText(f"Error: {e}")
    
    # === Playback Control ===
    
    def _is_playing(self) -> bool:
        """Check if currently playing."""
        if self._mode == PlaybackMode.NATIVE and self._native_player:
            return self._native_player.is_playing
        elif self._frame_controller:
            return self._frame_controller.is_playing
        return False
    
    def _toggle_playback(self) -> None:
        if not self._frame_controller:
            return

        if self._is_playing():
            self._pause()
            self._show_notification("Pause")
        else:
            self._play()
            self._show_notification("Play")
    
    def _play(self) -> None:
        """Start playback (prefers native mode for smooth video, AudioEngine for audio)."""
        if not self._frame_controller:
            return
        
        # Get current time for syncing
        current_time = self._frame_controller.current_time
        
        # Try to use native mode for video playback
        if self._switch_to_native_mode() and self._native_player:
            # Start native video player
            self._native_player.play()
            # Start our AudioEngine for multi-track audio (synced to same position)
            self._frame_controller.audio_engine.seek(current_time)
            self._frame_controller.audio_engine.play()
        else:
            # Fallback to frame-accurate (both video and audio)
            self._frame_controller.play()
    
    def _pause(self) -> None:
        """Pause playback (switches to frame-accurate mode for stepping)."""
        if self._mode == PlaybackMode.NATIVE and self._native_player:
            # Pause both native video and our audio engine
            self._native_player.pause()
            if self._frame_controller:
                self._frame_controller.audio_engine.pause()
        
        if self._frame_controller:
            # Sync position from native to frame-accurate
            if self._mode == PlaybackMode.NATIVE and self._native_player:
                self._frame_controller.seek(self._native_player.current_time)
            else:
                self._frame_controller.pause()
        
        # Switch to frame-accurate mode for potential stepping
        self._switch_to_frame_accurate_mode()
    
    def _step_forward(self) -> None:
        if not self._frame_controller:
            return
        if self._frame_controller.current_frame >= self._frame_controller.total_frames - 1:
            self._show_notification("End", 500)
            return

        # Ensure we're in frame-accurate mode
        self._switch_to_frame_accurate_mode()
        
        self._frame_controller.step_forward()
        self._display_current_frame()
        self._update_time_display()
        self._show_notification("Step +1", 500)

    def _step_backward(self) -> None:
        if not self._frame_controller:
            return
        if self._frame_controller.current_frame <= 0:
            self._show_notification("Start", 500)
            return

        # Ensure we're in frame-accurate mode
        self._switch_to_frame_accurate_mode()
        
        self._frame_controller.step_backward()
        self._display_current_frame()
        self._update_time_display()
        self._show_notification("Step -1", 500)
    
    def _display_current_frame(self) -> None:
        if not self._frame_controller:
            return
        
        frame = self._frame_controller.get_current_frame()
        if frame is not None:
            self._frame_video_widget.display_frame(frame)
            self._last_displayed_frame = self._frame_controller.current_frame
            self._frames_displayed += 1
    
    def _update_time_display(self) -> None:
        if not self._frame_controller:
            return
        self._update_time_display_from_time(self._frame_controller.current_time)
    
    def _update_time_display_from_time(self, current_time: float) -> None:
        if not self._frame_controller:
            return

        duration = self._frame_controller.duration
        fps = self._frame_controller.fps
        total_frames = self._frame_controller.total_frames
        current_frame = int(current_time * fps) if fps > 0 else 0
        last_frame = total_frames - 1

        if current_frame >= last_frame:
            current_frame = last_frame
            current_time = duration

        def format_time(seconds: float) -> str:
            mins = int(seconds) // 60
            secs = seconds % 60
            return f"{mins:02d}:{secs:06.3f}"

        self._time_label.setText(f"{format_time(current_time)} / {format_time(duration)}")
        self._frame_label.setText(f"Frame: {current_frame} / {total_frames}")
        self._update_info_label_positions()

        if not self._timeline_slider.isSliderDown():
            self._timeline_slider.blockSignals(True)
            self._timeline_slider.setValue(current_frame)
            self._timeline_slider.blockSignals(False)
    
    def _refresh_display(self) -> None:
        if not self._frame_controller:
            return

        if self._mode == PlaybackMode.NATIVE:
            # In native mode, just update time display from native player
            if self._native_player:
                self._update_time_display_from_time(self._native_player.current_time)
            return
        
        # Frame-accurate mode refresh
        current_frame = self._frame_controller.current_frame

        if self._frame_controller.is_playing:
            if current_frame >= self._frame_controller.total_frames - 1:
                self._frame_controller.pause()
                max_time = (self._frame_controller.total_frames - 1) / self._frame_controller.fps
                self._frame_controller.seek(max_time)
                self._display_current_frame()
                self._update_time_display()
                self._show_notification("End", 600)
                return

            if self._last_displayed_frame >= 0:
                expected_advance = 1
                actual_advance = current_frame - self._last_displayed_frame
                if actual_advance > expected_advance + 1:
                    dropped = actual_advance - expected_advance
                    self._frames_dropped += dropped

            self._display_current_frame()
            self._update_time_display()
        elif current_frame != self._last_displayed_frame:
            self._display_current_frame()
            self._update_time_display()
    
    def _log_stats(self) -> None:
        if not self._frame_controller:
            return
        
        elapsed_seconds = self._frame_timer.elapsed() / 1000.0
        fps = self._frames_displayed / elapsed_seconds if elapsed_seconds > 0 else 0
        
        mode_str = "NATIVE" if self._mode == PlaybackMode.NATIVE else "FRAME_ACCURATE"
        logger.info(
            f"UI stats: mode={mode_str}, displayed={self._frames_displayed}, "
            f"dropped={self._frames_dropped}, fps={fps:.1f}"
        )
        
        self._frame_controller.video_decoder.log_stats()
        self._frame_controller.audio_engine.log_stats()
    
    def _show_notification(self, text: str, duration_ms: int = 800) -> None:
        self._notification.show_notification(text, duration_ms)

    def _on_slider_pressed(self) -> None:
        self._was_playing_before_seek = self._is_playing()
        self._pause()
    
    def _on_slider_released(self) -> None:
        if self._frame_controller:
            self._frame_controller.seek_to_frame(self._timeline_slider.value())
            if self._native_player:
                # Sync native player position
                self._native_player.seek(self._frame_controller.current_time)
            self._display_current_frame()
            self._update_time_display()
            if self._was_playing_before_seek:
                self._play()

    def _on_slider_value_changed(self, value: int) -> None:
        if self._frame_controller and self._timeline_slider.isSliderDown():
            self._frame_controller.seek_to_frame(value)
            self._display_current_frame()
            self._update_time_display()

    def _change_volume(self, delta: float) -> None:
        if not self._frame_controller:
            return
        new_volume = self._master_volume + delta
        self._master_volume = max(0.0, min(1.0, round(new_volume * 20) / 20))
        
        # Volume is controlled through AudioEngine (works in both modes)
        for i in range(self._frame_controller.num_audio_tracks):
            self._frame_controller.set_track_volume(i, self._master_volume)
        
        self._show_notification(f"Volume: {int(self._master_volume * 100)}%", 600)

    def _skip_time(self, seconds: float) -> None:
        if not self._frame_controller:
            return
        
        current_time = self._frame_controller.current_time
        if self._mode == PlaybackMode.NATIVE and self._native_player:
            current_time = self._native_player.current_time
        
        max_time = (self._frame_controller.total_frames - 1) / self._frame_controller.fps
        new_time = max(0.0, min(max_time, current_time + seconds))
        
        was_playing = self._is_playing()
        
        # Seek both controllers and audio
        self._frame_controller.seek(new_time)
        if self._native_player:
            self._native_player.seek(new_time)
        
        # Resync audio if we were playing in native mode
        if was_playing and self._mode == PlaybackMode.NATIVE:
            self._frame_controller.audio_engine.seek(new_time)
            self._frame_controller.audio_engine.play()
        
        self._display_current_frame()
        self._update_time_display()
        sign = "+" if seconds > 0 else ""
        self._show_notification(f"Skip {sign}{int(seconds)}s", 600)

    def _on_track_volume_changed(self, track_index: int, volume: float) -> None:
        if self._frame_controller:
            self._frame_controller.set_track_volume(track_index, volume)

    def _on_track_mute_changed(self, track_index: int, muted: bool) -> None:
        if self._frame_controller:
            self._frame_controller.set_track_muted(track_index, muted)

    def _toggle_playback_mode(self) -> None:
        """Manually toggle between native and frame-accurate modes."""
        if not self._frame_controller:
            return
        
        was_playing = self._is_playing()
        if was_playing:
            self._pause()
        
        if self._mode == PlaybackMode.NATIVE:
            self._switch_to_frame_accurate_mode()
            self._display_current_frame()
            self._show_notification("Mode: Frame-Accurate", 800)
        else:
            if self._switch_to_native_mode():
                self._show_notification("Mode: Native", 800)
            else:
                self._show_notification("Native unavailable", 800)
        
        if was_playing:
            self._play()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        modifiers = event.modifiers()

        if key == Qt.Key.Key_O and (modifiers & Qt.KeyboardModifier.ControlModifier):
            self._open_file()
            return

        if not self._frame_controller:
            super().keyPressEvent(event)
            return

        if key == Qt.Key.Key_Space:
            self._toggle_playback()
        elif key == Qt.Key.Key_Left:
            self._step_backward()
        elif key == Qt.Key.Key_Right:
            self._step_forward()
        elif key == Qt.Key.Key_Up:
            self._change_volume(0.05)
        elif key == Qt.Key.Key_Down:
            self._change_volume(-0.05)
        elif key == Qt.Key.Key_BracketLeft:
            self._skip_time(-5)
        elif key == Qt.Key.Key_BracketRight:
            self._skip_time(10)
        elif key == Qt.Key.Key_BraceLeft:
            self._skip_time(-15)
        elif key == Qt.Key.Key_BraceRight:
            self._skip_time(30)
        elif key == Qt.Key.Key_A:
            self._toggle_audio_mixer()
        elif key == Qt.Key.Key_M:
            self._toggle_playback_mode()  # New: M to toggle mode
        else:
            super().keyPressEvent(event)

    def _toggle_audio_mixer(self) -> None:
        if self._audio_mixer.isVisible():
            self._audio_mixer.hide()
            self._show_notification("Mixer: Off", 600)
        else:
            self._audio_mixer.show()
            self._show_notification("Mixer: On", 600)
    
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_info_label_positions()

    def _update_info_label_positions(self) -> None:
        margin = 10
        container_rect = self._video_container.rect()

        self._time_label.adjustSize()
        self._time_label.move(margin, container_rect.height() - self._time_label.height() - margin)

        self._frame_label.adjustSize()
        self._frame_label.move(
            container_rect.width() - self._frame_label.width() - margin,
            container_rect.height() - self._frame_label.height() - margin
        )
        
        # Mode indicator in top-left
        self._mode_indicator.move(margin, margin)

    def closeEvent(self, event) -> None:
        self._refresh_timer.stop()
        self._stats_timer.stop()
        if self._frame_controller:
            self._log_stats()
            self._frame_controller.close()
        if self._native_player:
            self._native_player.close()
        event.accept()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    from PyQt6.QtGui import QPalette, QColor
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.Base, QColor(25, 25, 25))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.Text, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
    palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
    app.setPalette(palette)
    
    window = DualModeMainWindow()
    window.show()
    
    if len(sys.argv) > 1:
        window._load_file(sys.argv[1])
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
