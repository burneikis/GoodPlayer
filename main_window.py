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
    QLabel, QPushButton, QSlider, QFileDialog, QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer, QElapsedTimer
from PyQt6.QtGui import QImage, QPixmap, QKeyEvent

from playback_controller import PlaybackController

logger = logging.getLogger(__name__)


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
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Video display
        self._video_widget = VideoWidget()
        layout.addWidget(self._video_widget, stretch=1)
        
        # Controls panel
        controls_panel = QWidget()
        controls_panel.setStyleSheet("background-color: #2b2b2b;")
        controls_layout = QVBoxLayout(controls_panel)
        controls_layout.setContentsMargins(10, 5, 10, 5)
        
        # Timeline slider
        self._timeline_slider = QSlider(Qt.Orientation.Horizontal)
        self._timeline_slider.setEnabled(False)
        self._timeline_slider.sliderPressed.connect(self._on_slider_pressed)
        self._timeline_slider.sliderReleased.connect(self._on_slider_released)
        self._timeline_slider.sliderMoved.connect(self._on_slider_moved)
        controls_layout.addWidget(self._timeline_slider)
        
        # Button row
        button_row = QHBoxLayout()
        
        # Open button
        self._open_btn = QPushButton("Open")
        self._open_btn.clicked.connect(self._open_file)
        button_row.addWidget(self._open_btn)
        
        # Play/Pause button
        self._play_btn = QPushButton("Play")
        self._play_btn.setEnabled(False)
        self._play_btn.clicked.connect(self._toggle_playback)
        button_row.addWidget(self._play_btn)
        
        # Frame step buttons
        self._prev_frame_btn = QPushButton("◀ Frame")
        self._prev_frame_btn.setEnabled(False)
        self._prev_frame_btn.clicked.connect(self._step_backward)
        button_row.addWidget(self._prev_frame_btn)
        
        self._next_frame_btn = QPushButton("Frame ▶")
        self._next_frame_btn.setEnabled(False)
        self._next_frame_btn.clicked.connect(self._step_forward)
        button_row.addWidget(self._next_frame_btn)
        
        button_row.addStretch()
        
        # Time display
        self._time_label = QLabel("00:00.000 / 00:00.000  |  Frame: 0 / 0")
        self._time_label.setStyleSheet("color: white; font-family: monospace;")
        button_row.addWidget(self._time_label)
        
        controls_layout.addLayout(button_row)
        layout.addWidget(controls_panel)
        
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
            
            # Enable controls
            self._play_btn.setEnabled(True)
            self._prev_frame_btn.setEnabled(True)
            self._next_frame_btn.setEnabled(True)
            self._timeline_slider.setEnabled(True)
            
            # Setup timeline
            self._timeline_slider.setRange(0, self._controller.total_frames - 1)
            self._timeline_slider.setValue(0)
            
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
            self._time_label.setText(f"Error: {e}")
    
    def _toggle_playback(self) -> None:
        """Toggle play/pause."""
        if not self._controller:
            return
        
        self._controller.toggle_playback()
        self._update_play_button()
    
    def _update_play_button(self) -> None:
        """Update play button text based on state."""
        if self._controller and self._controller.is_playing:
            self._play_btn.setText("Pause")
        else:
            self._play_btn.setText("Play")
    
    def _step_forward(self) -> None:
        """Step forward one frame."""
        if not self._controller:
            return
        
        self._controller.step_forward()
        self._display_current_frame()
        self._update_time_display()
        self._update_play_button()
    
    def _step_backward(self) -> None:
        """Step backward one frame."""
        if not self._controller:
            return
        
        self._controller.step_backward()
        self._display_current_frame()
        self._update_time_display()
        self._update_play_button()
    
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
        """Update the time label."""
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
        
        self._time_label.setText(
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
        
        self._update_play_button()
    
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
        """Handle slider release - resume if was playing."""
        if hasattr(self, '_was_playing_before_seek') and self._was_playing_before_seek:
            if self._controller:
                self._controller.play()
    
    def _on_slider_moved(self, value: int) -> None:
        """Handle slider movement - seek to frame."""
        if self._controller:
            self._controller.seek_to_frame(value)
            self._display_current_frame()
            self._update_time_display()
    
    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle keyboard input."""
        if not self._controller:
            super().keyPressEvent(event)
            return
        
        key = event.key()
        
        if key == Qt.Key.Key_Space:
            self._toggle_playback()
        elif key == Qt.Key.Key_Left:
            self._step_backward()
        elif key == Qt.Key.Key_Right:
            self._step_forward()
        elif key == Qt.Key.Key_Home:
            self._controller.seek(0)
            self._display_current_frame()
            self._update_time_display()
        elif key == Qt.Key.Key_End:
            self._controller.seek(self._controller.duration - 0.1)
            self._display_current_frame()
            self._update_time_display()
        elif key == Qt.Key.Key_S:
            # Manual stats dump
            self._log_stats()
        else:
            super().keyPressEvent(event)
    
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
