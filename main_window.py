"""
Stage 4 & 5: PyQt UI with Performance Monitoring
Main window for video playback with keyboard controls (legacy mode).
"""

import sys
import logging
from typing import Optional

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFileDialog
)
from PyQt6.QtCore import Qt, QTimer, QElapsedTimer
from PyQt6.QtGui import QKeyEvent

from playback_controller import PlaybackController
from widgets import (
    AudioMixerPanel, NotificationOverlay, WelcomeOverlay,
    ClickableSlider, VideoWidget, format_time
)
from theme import apply_dark_theme

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Main application window with performance monitoring."""
    
    DEFAULT_REFRESH_INTERVAL_MS = 16  # Fallback for ~60fps
    STATS_INTERVAL_MS = 5000  # Log stats every 5 seconds
    REFRESH_HEADROOM_FACTOR = 0.85  # Target 85% of frame time for headroom
    
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
        self.setWindowTitle("GoodPlayer")
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

        # Welcome overlay (prompts user to open file)
        self._welcome_overlay = WelcomeOverlay(self._video_widget)
        self._welcome_overlay.set_click_callback(self._open_file)

        # Notification overlay (on top of video)
        self._notification = NotificationOverlay(self._video_widget)

        # Time info overlay (bottom left of video, no background)
        self._time_label = QLabel("00:00.000 / 00:00.000", self._video_widget)
        self._time_label.setStyleSheet("color: white; font-family: monospace; background: transparent;")
        self._time_label.adjustSize()
        self._time_label.hide()  # Hide until file is loaded

        # Frame info overlay (bottom right of video, no background)
        self._frame_label = QLabel("Frame: 0 / 0", self._video_widget)
        self._frame_label.setStyleSheet("color: white; font-family: monospace; background: transparent;")
        self._frame_label.adjustSize()
        self._frame_label.hide()  # Hide until file is loaded

        # Controls panel
        controls_panel = QWidget()
        controls_panel.setStyleSheet("background-color: #2b2b2b;")
        controls_layout = QVBoxLayout(controls_panel)
        controls_layout.setContentsMargins(10, 5, 10, 5)

        # Timeline slider (clickable)
        self._timeline_slider = ClickableSlider(Qt.Orientation.Horizontal)
        self._timeline_slider.setEnabled(False)
        self._timeline_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        self._refresh_timer.setInterval(self.DEFAULT_REFRESH_INTERVAL_MS)
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
            
            # Hide welcome overlay, show info labels
            self._welcome_overlay.hide()
            self._time_label.show()
            self._frame_label.show()
            
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

            # Calculate optimal refresh interval based on video FPS
            video_fps = self._controller.fps
            if video_fps > 0:
                # Calculate interval with headroom for processing
                frame_time_ms = 1000.0 / video_fps
                refresh_interval = max(1, int(frame_time_ms * self.REFRESH_HEADROOM_FACTOR))
            else:
                refresh_interval = self.DEFAULT_REFRESH_INTERVAL_MS
            
            self._refresh_timer.setInterval(refresh_interval)
            logger.info(f"Video FPS: {video_fps:.2f}, refresh interval: {refresh_interval}ms")

            # Display first frame
            self._display_current_frame()
            self._update_time_display()
            
            # Start refresh timer
            self._refresh_timer.start()
            self._stats_timer.start()
            self._frame_timer.start()
            
            self.setWindowTitle(f"GoodPlayer - {filepath}")
            
        except Exception as e:
            logger.error(f"Failed to load file: {e}")
            self._video_widget.clear_display()
            self._time_label.setText(f"Error: {e}")
    
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
        last_frame = total_frames - 1

        # Clamp to final frame when at or past the end
        if current_frame >= last_frame:
            current_frame = last_frame
            current_time = duration

        self._time_label.setText(f"{format_time(current_time)} / {format_time(duration)}")
        self._frame_label.setText(f"Frame: {current_frame} / {total_frames}")
        self._update_info_label_positions()

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
        new_volume = self._master_volume + delta
        # Round to nearest 5% to avoid floating-point precision issues
        self._master_volume = max(0.0, min(1.0, round(new_volume * 20) / 20))
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
    
    def resizeEvent(self, event) -> None:
        """Handle window resize - reposition info label overlays."""
        super().resizeEvent(event)
        self._update_info_label_positions()

    def _update_info_label_positions(self) -> None:
        """Position the time label at bottom left and frame label at bottom right."""
        margin = 10
        video_rect = self._video_widget.rect()

        # Time label - bottom left
        self._time_label.adjustSize()
        self._time_label.move(
            margin,
            video_rect.height() - self._time_label.height() - margin
        )

        # Frame label - bottom right
        self._frame_label.adjustSize()
        self._frame_label.move(
            video_rect.width() - self._frame_label.width() - margin,
            video_rect.height() - self._frame_label.height() - margin
        )

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
    apply_dark_theme(app)
    
    window = MainWindow()
    window.show()
    
    # Load file from command line if provided
    if len(sys.argv) > 1:
        window._load_file(sys.argv[1])
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
