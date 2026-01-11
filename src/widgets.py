"""
Shared UI widgets for GoodPlayer.
Contains common components used by both main_window and dual_mode_window.
"""

import numpy as np
from typing import Optional, Callable

from PyQt6.QtWidgets import (
    QLabel, QSlider, QFrame, QVBoxLayout, QWidget,
    QScrollArea, QCheckBox, QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QImage, QPixmap


class AudioTrackWidget(QFrame):
    """Widget for controlling a single audio track."""

    def __init__(self, track_index: int, parent=None):
        super().__init__(parent)
        self.track_index = track_index
        self._volume_callback: Optional[Callable] = None
        self._mute_callback: Optional[Callable] = None
        self._setup_ui()

    def _setup_ui(self):
        self.setFrameStyle(QFrame.Shape.StyledPanel)
        self.setStyleSheet("background-color: #3a3a3a; border-radius: 4px; padding: 5px;")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

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
        self._volume_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        self._mute_checkbox.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._mute_checkbox.stateChanged.connect(self._on_mute_changed)
        layout.addWidget(self._mute_checkbox, alignment=Qt.AlignmentFlag.AlignHCenter)

    def set_volume_callback(self, callback: Callable):
        self._volume_callback = callback

    def set_mute_callback(self, callback: Callable):
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
        self._track_widgets: list[AudioTrackWidget] = []
        self._setup_ui()
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

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
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._tracks_container = QWidget()
        self._tracks_container.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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

    def setup_tracks(self, num_tracks: int, volume_callback: Callable, mute_callback: Callable):
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
    DEFAULT_DURATION_MS = 1000
    NOTIFICATION_MARGIN = 10
    """Overlay widget for showing action notifications."""

    def __init__(self, parent=None, use_top_level: bool = False):
        """
        Initialize notification overlay.
        
        Args:
            parent: Parent widget
            use_top_level: If True, creates a top-level window that can render
                          over hardware video surfaces (QVideoWidget)
        """
        if use_top_level:
            super().__init__(None)  # No parent - top level window
            self._parent_widget = parent
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint |
                Qt.WindowType.Tool |
                Qt.WindowType.WindowStaysOnTopHint
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        else:
            super().__init__(parent)
            self._parent_widget = None
        
        self._use_top_level = use_top_level
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

    def show_notification(self, text: str, duration_ms: int = None) -> None:
        """Show a notification that fades after duration."""
        self.setText(text)
        self.adjustSize()
        
        if self._use_top_level and self._parent_widget:
            # Position relative to parent widget's global position
            parent_global = self._parent_widget.mapToGlobal(self._parent_widget.rect().topLeft())
            parent_rect = self._parent_widget.rect()
            x = parent_global.x() + parent_rect.width() - self.width() - self.NOTIFICATION_MARGIN
            y = parent_global.y() + self.NOTIFICATION_MARGIN
            self.move(x, y)
        elif self.parent():
            # Position in top right of parent
            parent_rect = self.parent().rect()
            self.move(
                parent_rect.width() - self.width() - self.NOTIFICATION_MARGIN,
                self.NOTIFICATION_MARGIN
            )
        
        self.show()
        if not self._use_top_level:
            self.raise_()
        if duration_ms is None:
            duration_ms = self.DEFAULT_DURATION_MS
        self._timer.start(duration_ms)


class WelcomeOverlay(QLabel):
    """Overlay widget prompting user to open a file."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._click_callback: Optional[Callable] = None
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

    def update_position(self) -> None:
        """Center the overlay in the parent widget."""
        if self.parent():
            parent_rect = self.parent().rect()
            self.adjustSize()
            x = (parent_rect.width() - self.width()) // 2
            y = (parent_rect.height() - self.height()) // 2
            self.move(x, y)
            self.raise_()

    def set_click_callback(self, callback: Callable):
        self._click_callback = callback

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._click_callback:
            self._click_callback()
        super().mousePressEvent(event)


class ClickableSlider(QSlider):
    """A slider that responds to mouse clicks anywhere on the track and supports dragging."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._dragging = False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._update_value_from_pos(event.position().x())
            self.sliderPressed.emit()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._update_value_from_pos(event.position().x())
            self.sliderMoved.emit(self.value())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._dragging:
                self._dragging = False
            self.sliderReleased.emit()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def isSliderDown(self) -> bool:
        """Override to use our custom dragging state."""
        return self._dragging or super().isSliderDown()

    def _update_value_from_pos(self, x_pos: float) -> None:
        """Update slider value based on x position."""
        if self.orientation() == Qt.Orientation.Horizontal:
            ratio = max(0.0, min(1.0, x_pos / self.width()))
            value = self.minimum() + (self.maximum() - self.minimum()) * ratio
        else:
            ratio = max(0.0, min(1.0, (self.height() - x_pos) / self.height()))
            value = self.minimum() + (self.maximum() - self.minimum()) * ratio
        self.setValue(int(value))


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


class TimeInfoOverlay(QLabel):
    """
    Overlay widget for showing time/frame information.
    Uses a frameless top-level window to render over video hardware overlays.
    """

    def __init__(self, parent=None, align_right: bool = False):
        super().__init__(None)  # No parent - top level window
        self._parent_widget = parent
        self._align_right = align_right
        self._margin = 10
        
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        
        self.setStyleSheet("color: white; font-family: monospace; background: transparent;")
        self.hide()

    def update_position(self) -> None:
        """Update position relative to parent widget."""
        if not self._parent_widget:
            return
        
        self.adjustSize()
        parent_global = self._parent_widget.mapToGlobal(self._parent_widget.rect().topLeft())
        parent_rect = self._parent_widget.rect()
        
        if self._align_right:
            x = parent_global.x() + parent_rect.width() - self.width() - self._margin
        else:
            x = parent_global.x() + self._margin
        y = parent_global.y() + parent_rect.height() - self.height() - self._margin
        
        self.move(x, y)

    def show_overlay(self) -> None:
        """Show the overlay and update position."""
        self.update_position()
        self.show()

    def hide_overlay(self) -> None:
        """Hide the overlay."""
        self.hide()


def format_time(seconds: float) -> str:
    """Format seconds as MM:SS.mmm"""
    mins = int(seconds) // 60
    secs = seconds % 60
    return f"{mins:02d}:{secs:06.3f}"# ...existing code from widgets.py will be moved here...
