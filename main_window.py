"""
Stage 4 & 5: PyQt UI with Performance Monitoring
Main window for video playback with keyboard controls.
"""

import sys
import logging
import numpy as np
from typing import Optional

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSlider, QFileDialog, QSizePolicy, QCheckBox,
    QFrame, QScrollArea
)
from PyQt6.QtCore import Qt, QTimer, QElapsedTimer
from PyQt6.QtGui import QImage, QPixmap, QKeyEvent

from playback_controller import PlaybackController

logger = logging.getLogger(__name__)


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

        # Track label
        self._label = QLabel(f"Track {self.track_index + 1}")
        self._label.setStyleSheet("color: white; font-weight: bold;")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._label)

        # Volume slider (vertical)
        self._volume_slider = QSlider(Qt.Orientation.Vertical)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(100)
        self._volume_slider.setMinimumHeight(80)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        layout.addWidget(self._volume_slider, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Volume label
        self._volume_label = QLabel("100%")
        self._volume_label.setStyleSheet("color: #aaa;")
        self._volume_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._volume_label)

        # Mute checkbox
        self._mute_checkbox = QCheckBox("Mute")
        self._mute_checkbox.setStyleSheet("color: white;")
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

        # Header
        header = QLabel("Audio Mixer")
        header.setStyleSheet("color: white; font-weight: bold; font-size: 12px;")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        # Scroll area for tracks
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

        # Placeholder when no tracks
        self._no_tracks_label = QLabel("No audio\ntracks")
        self._no_tracks_label.setStyleSheet("color: #666;")
        self._no_tracks_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tracks_layout.insertWidget(0, self._no_tracks_label)

    def setup_tracks(self, num_tracks: int, volume_callback, mute_callback):
        """Setup track widgets for the given number of audio tracks."""
        # Clear existing widgets
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
        """Show a notification that fades after duration."""
        self.setText(text)
        self.adjustSize()
        # Center on parent
        if self.parent():
            parent_rect = self.parent().rect()
            self.move(
                (parent_rect.width() - self.width()) // 2,
                (parent_rect.height() - self.height()) // 2
            )
        self.show()
        self.raise_()
        self._timer.start(duration_ms)


class ClickableSlider(QSlider):
    """A slider that responds to mouse clicks anywhere on the track."""

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Calculate value from click position
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


class VideoWidget(QLabel):
    """Widget for displaying video frames."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(640, 360)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background-color: black;")
        self._aspect_ratio: float = 16 / 9
    
    def display_frame(self, frame: np.ndarray) -> None:
        """Display a numpy RGB frame."""
        if frame is None:
            return
        
        height, width, channels = frame.shape
        self._aspect_ratio = width / height
        
        # Create QImage from numpy array
        bytes_per_line = channels * width
        qimage = QImage(
            frame.data, width, height, bytes_per_line,
            QImage.Format.Format_RGB888
        )
        
        # Scale to fit widget while maintaining aspect ratio
        pixmap = QPixmap.fromImage(qimage)
        scaled = pixmap.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        
        self.setPixmap(scaled)
    
    def clear_display(self) -> None:
        """Clear the display."""
        self.clear()
        self.setStyleSheet("background-color: black;")


class MainWindow(QMainWindow):
    """Main application window with performance monitoring."""
    
    REFRESH_INTERVAL_MS = 16
    STATS_INTERVAL_MS = 5000  # Log stats every 5 seconds
    
    def __init__(self):
        super().__init__()
        
        self._controller: Optional[PlaybackController] = None
        self._last_displayed_frame: int = -1
        self._master_volume: float = 1.0

        # Performance tracking
        self._frames_displayed = 0
        self._frames_dropped = 0
        self._frame_timer = QElapsedTimer()
        
        self._setup_ui()
        self._setup_timers()
    
    def _setup_ui(self) -> None:
        """Initialize the user interface."""
        self.setWindowTitle("GoodPlayer3")
        self.setMinimumSize(800, 600)
        
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Content area (video + mixer)
        content_area = QWidget()
        content_layout = QHBoxLayout(content_area)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Video display
        self._video_widget = VideoWidget()
        content_layout.addWidget(self._video_widget, stretch=1)

        # Audio mixer panel (right side, hidden by default)
        self._audio_mixer = AudioMixerPanel()
        self._audio_mixer.hide()
        content_layout.addWidget(self._audio_mixer)

        main_layout.addWidget(content_area, stretch=1)

        # Notification overlay (on top of video)
        self._notification = NotificationOverlay(self._video_widget)

        # Controls panel
        controls_panel = QWidget()
        controls_panel.setStyleSheet("background-color: #2b2b2b;")
        controls_layout = QVBoxLayout(controls_panel)
        controls_layout.setContentsMargins(10, 5, 10, 5)

        # Combined time and frame display above progress bar
        self._info_label = QLabel("00:00.000 / 00:00.000  |  Frame: 0 / 0")
        self._info_label.setStyleSheet("color: white; font-family: monospace;")
        self._info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        controls_layout.addWidget(self._info_label)

        # Timeline slider (clickable)
        self._timeline_slider = ClickableSlider(Qt.Orientation.Horizontal)
        self._timeline_slider.setEnabled(False)
        self._timeline_slider.sliderPressed.connect(self._on_slider_pressed)
        self._timeline_slider.sliderReleased.connect(self._on_slider_released)
        self._timeline_slider.valueChanged.connect(self._on_slider_value_changed)
        controls_layout.addWidget(self._timeline_slider)

        main_layout.addWidget(controls_panel)
        
        # Focus policy for keyboard events
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    
    def _setup_timers(self) -> None:
        """Setup refresh and stats timers."""
        self._refresh_timer = QTimer()
        self._refresh_timer.setInterval(self.REFRESH_INTERVAL_MS)
        self._refresh_timer.timeout.connect(self._refresh_display)
        
        self._stats_timer = QTimer()
        self._stats_timer.setInterval(self.STATS_INTERVAL_MS)
        self._stats_timer.timeout.connect(self._log_stats)
    
    def _open_file(self) -> None:
        """Open a video file."""
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "Open Video",
            "",
            "Video Files (*.mp4 *.mkv *.avi *.mov *.webm);;All Files (*)"
        )
        
        if filepath:
            self._load_file(filepath)
    
    def _load_file(self, filepath: str) -> None:
        """Load a video file."""
        # Close existing controller
        if self._controller:
            self._refresh_timer.stop()
            self._stats_timer.stop()
            self._controller.close()
        
        # Reset stats
        self._frames_displayed = 0
        self._frames_dropped = 0
        
        try:
            self._controller = PlaybackController(filepath)
            
            # Enable timeline slider
            self._timeline_slider.setEnabled(True)
            
            # Setup timeline
            self._timeline_slider.setRange(0, self._controller.total_frames - 1)
            self._timeline_slider.setValue(0)

            # Setup audio mixer
            self._audio_mixer.setup_tracks(
                self._controller.num_audio_tracks,
                self._on_track_volume_changed,
                self._on_track_mute_changed
            )

            # Display first frame
            self._display_current_frame()
            self._update_time_display()
            
            # Start refresh timer
            self._refresh_timer.start()
            self._stats_timer.start()
            self._frame_timer.start()
            
            self.setWindowTitle(f"GoodPlayer3 - {filepath}")
            
        except Exception as e:
            logger.error(f"Failed to load file: {e}")
            self._video_widget.clear_display()
            self._info_label.setText(f"Error: {e}")
    
    def _show_notification(self, text: str, duration_ms: int = 800) -> None:
        """Show a notification overlay."""
        self._notification.show_notification(text, duration_ms)

    def _toggle_playback(self) -> None:
        """Toggle play/pause."""
        if not self._controller:
            return

        self._controller.toggle_playback()
        self._show_notification("Play" if self._controller.is_playing else "Pause")

    def _step_forward(self) -> None:
        """Step forward one frame."""
        if not self._controller:
            return
        # Don't step past the last frame
        if self._controller.current_frame >= self._controller.total_frames - 1:
            self._show_notification("End", 500)
            return

        self._controller.step_forward()
        self._display_current_frame()
        self._update_time_display()
        self._show_notification("Step +1", 500)

    def _step_backward(self) -> None:
        """Step backward one frame."""
        if not self._controller:
            return
        # Don't step before the first frame
        if self._controller.current_frame <= 0:
            self._show_notification("Start", 500)
            return

        self._controller.step_backward()
        self._display_current_frame()
        self._update_time_display()
        self._show_notification("Step -1", 500)
    
    def _display_current_frame(self) -> None:
        """Display the current frame."""
        if not self._controller:
            return
        
        frame = self._controller.get_current_frame()
        if frame is not None:
            self._video_widget.display_frame(frame)
            self._last_displayed_frame = self._controller.current_frame
            self._frames_displayed += 1
    
    def _update_time_display(self) -> None:
        """Update the combined time and frame label."""
        if not self._controller:
            return

        current_time = self._controller.current_time
        duration = self._controller.duration
        current_frame = self._controller.current_frame
        total_frames = self._controller.total_frames

        def format_time(seconds: float) -> str:
            mins = int(seconds) // 60
            secs = seconds % 60
            return f"{mins:02d}:{secs:06.3f}"

        self._info_label.setText(
            f"{format_time(current_time)} / {format_time(duration)}  |  "
            f"Frame: {current_frame} / {total_frames}"
        )
        
        # Update slider (without triggering signals)
        if not self._timeline_slider.isSliderDown():
            self._timeline_slider.blockSignals(True)
            self._timeline_slider.setValue(current_frame)
            self._timeline_slider.blockSignals(False)
    
    def _refresh_display(self) -> None:
        """Called by timer to refresh the display during playback."""
        if not self._controller:
            return

        current_frame = self._controller.current_frame

        if self._controller.is_playing:
            # Check if we've reached the end of the video
            if current_frame >= self._controller.total_frames - 1:
                self._controller.pause()
                # Seek to last valid frame
                max_time = (self._controller.total_frames - 1) / self._controller.fps
                self._controller.seek(max_time)
                self._display_current_frame()
                self._update_time_display()
                self._show_notification("End", 600)
                return

            # Check for dropped frames
            if self._last_displayed_frame >= 0:
                expected_advance = 1
                actual_advance = current_frame - self._last_displayed_frame
                if actual_advance > expected_advance + 1:
                    dropped = actual_advance - expected_advance
                    self._frames_dropped += dropped
                    logger.debug(f"Dropped {dropped} frames")

            self._display_current_frame()
            self._update_time_display()
        elif current_frame != self._last_displayed_frame:
            self._display_current_frame()
            self._update_time_display()
    
    def _log_stats(self) -> None:
        """Log performance statistics periodically."""
        if not self._controller:
            return
        
        elapsed_seconds = self._frame_timer.elapsed() / 1000.0
        fps = self._frames_displayed / elapsed_seconds if elapsed_seconds > 0 else 0
        
        logger.info(
            f"UI stats: displayed={self._frames_displayed}, "
            f"dropped={self._frames_dropped}, fps={fps:.1f}"
        )
        
        # Log component stats
        self._controller.video_decoder.log_stats()
        self._controller.audio_engine.log_stats()
    
    def _on_slider_pressed(self) -> None:
        """Handle slider press - pause during seek."""
        if self._controller and self._controller.is_playing:
            self._was_playing_before_seek = True
            self._controller.pause()
        else:
            self._was_playing_before_seek = False
    
    def _on_slider_released(self) -> None:
        """Handle slider release - seek to final position and resume if was playing."""
        if self._controller:
            # Always seek to the current slider value on release
            self._controller.seek_to_frame(self._timeline_slider.value())
            self._display_current_frame()
            self._update_time_display()
            # Resume if was playing before seek
            if hasattr(self, '_was_playing_before_seek') and self._was_playing_before_seek:
                self._controller.play()

    def _on_slider_value_changed(self, value: int) -> None:
        """Handle slider value change - seek to frame during drag."""
        # Only update display during drag (actual seek happens on release)
        if self._controller and self._timeline_slider.isSliderDown():
            self._controller.seek_to_frame(value)
            self._display_current_frame()
            self._update_time_display()

    def _change_volume(self, delta: float) -> None:
        """Change master volume by delta amount."""
        if not self._controller:
            return
        self._master_volume = max(0.0, min(1.0, self._master_volume + delta))
        # Apply to all audio tracks
        for i in range(self._controller.num_audio_tracks):
            self._controller.set_track_volume(i, self._master_volume)
        self._show_notification(f"Volume: {int(self._master_volume * 100)}%", 600)

    def _skip_time(self, seconds: float) -> None:
        """Skip forward or backward by the given number of seconds."""
        if not self._controller:
            return
        # Calculate max valid time (last frame's start time)
        max_time = (self._controller.total_frames - 1) / self._controller.fps
        new_time = max(0.0, min(max_time, self._controller.current_time + seconds))
        self._controller.seek(new_time)
        self._display_current_frame()
        self._update_time_display()
        sign = "+" if seconds > 0 else ""
        self._show_notification(f"Skip {sign}{int(seconds)}s", 600)

    def _on_track_volume_changed(self, track_index: int, volume: float) -> None:
        """Handle track volume change from mixer UI."""
        if self._controller:
            self._controller.set_track_volume(track_index, volume)

    def _on_track_mute_changed(self, track_index: int, muted: bool) -> None:
        """Handle track mute change from mixer UI."""
        if self._controller:
            self._controller.set_track_muted(track_index, muted)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle keyboard input."""
        key = event.key()
        modifiers = event.modifiers()

        # Ctrl+O / Cmd+O to open file (works without controller)
        if key == Qt.Key.Key_O and (modifiers & Qt.KeyboardModifier.ControlModifier):
            self._open_file()
            return

        if not self._controller:
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
            self._skip_time(-5)  # [ = 5s back
        elif key == Qt.Key.Key_BracketRight:
            self._skip_time(10)  # ] = 10s forward
        elif key == Qt.Key.Key_BraceLeft:
            self._skip_time(-15)  # { = 15s back
        elif key == Qt.Key.Key_BraceRight:
            self._skip_time(30)  # } = 30s forward
        elif key == Qt.Key.Key_A:
            self._toggle_audio_mixer()
        else:
            super().keyPressEvent(event)

    def _toggle_audio_mixer(self) -> None:
        """Toggle audio mixer panel visibility."""
        if self._audio_mixer.isVisible():
            self._audio_mixer.hide()
            self._show_notification("Mixer: Off", 600)
        else:
            self._audio_mixer.show()
            self._show_notification("Mixer: On", 600)
    
    def closeEvent(self, event) -> None:
        """Handle window close."""
        self._refresh_timer.stop()
        self._stats_timer.stop()
        if self._controller:
            self._log_stats()
            self._controller.close()
        event.accept()


def main():
    """Application entry point."""
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    # Dark theme
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
    
    window = MainWindow()
    window.show()
    
    # Load file from command line if provided
    if len(sys.argv) > 1:
        window._load_file(sys.argv[1])
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
