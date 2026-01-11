"""
Dual-Mode Main Window
Supports both native playback (Qt Multimedia) for smooth viewing
and frame-accurate mode (PyAV) for precise control.
"""

import sys
import logging
from enum import Enum, auto

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFileDialog, QSizePolicy, QStackedWidget
)
from PyQt6.QtCore import Qt, QTimer, QElapsedTimer
from PyQt6.QtGui import QKeyEvent

# Use absolute imports for local modules
from src.playback_controller import PlaybackController
from src.video_decoder import VideoDecoder
from .widgets import (
    AudioMixerPanel, NotificationOverlay, WelcomeOverlay,
    ClickableSlider, VideoWidget, TimeInfoOverlay, format_time
)
from .theme import apply_dark_theme

logger = logging.getLogger(__name__)

# Check for Qt Multimedia availability
try:
    from src.qt_native_player import QtNativePlayer, is_available as qt_native_available
    QT_NATIVE_AVAILABLE = qt_native_available()
except ImportError:
    QT_NATIVE_AVAILABLE = False
    QtNativePlayer = None


class PlaybackMode(Enum):
    """Current playback mode."""
    NATIVE = auto()        # Qt Multimedia for smooth playback
    FRAME_ACCURATE = auto()  # PyAV for frame-by-frame control




class DualModeMainWindow(QMainWindow):
    """
    Main window supporting dual playback modes:
    - Native mode: Hardware-accelerated playback via Qt Multimedia
    - Frame-accurate mode: PyAV-based frame-by-frame control
    """

    DEFAULT_REFRESH_INTERVAL_MS = 16
    STATS_INTERVAL_MS = 5000
    REFRESH_HEADROOM_FACTOR = 0.85
    MIN_STEP_INTERVAL_MS = 30  # Minimum time between step operations

    def __init__(self):
        super().__init__()

        # Controllers
        self._frame_controller = None
        self._native_player = None  # type: ignore

        # Current mode
        self._mode = PlaybackMode.FRAME_ACCURATE
        self._prefer_native = QT_NATIVE_AVAILABLE

        # State
        self._last_displayed_frame = -1
        self._master_volume = 1.0
        self._was_playing_before_seek = False
        self._playing = False  # Explicit playing state to avoid race conditions
        self._last_step_time = 0  # Timestamp of last step operation (ms)

        # Performance tracking
        self._frames_displayed = 0
        self._frames_dropped = 0
        self._frame_timer = QElapsedTimer()
        self._frame_timer.start()  # Start immediately for step throttling

        self._setup_ui()
        self._setup_timers()

        logger.info(f"DualModeMainWindow: Native player {'available' if QT_NATIVE_AVAILABLE else 'not available'}")

    def focusOutEvent(self, event):
        # Hide overlays when window loses focus
        self._time_label.hide_overlay()
        self._frame_label.hide_overlay()
        self._notification.hide()
        super().focusOutEvent(event)

    def focusInEvent(self, event):
        # Show overlays when window regains focus
        self._time_label.show_overlay()
        self._frame_label.show_overlay()
        # Do not show notification overlay unless it was already visible before losing focus
        super().focusInEvent(event)

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

        # Video container with overlay support
        self._video_container = QWidget()
        self._video_container.setStyleSheet("background-color: black;")
        self._video_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        video_layout = QVBoxLayout(self._video_container)
        video_layout.setContentsMargins(0, 0, 0, 0)
        
        # Stacked widget to switch between native and frame-accurate display
        self._video_stack = QStackedWidget()
        video_layout.addWidget(self._video_stack)
        
        # Frame-accurate video widget (index 0) - reuse VideoWidget from widgets module
        self._frame_video_widget = VideoWidget()
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

        # Transparent overlay container (sits on top of video_container)
        self._overlay_container = QWidget(self._video_container)
        self._overlay_container.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._overlay_container.setStyleSheet("background: transparent;")
        self._overlay_container.show()
        
        # Overlays (parented to overlay container for proper z-order)
        self._welcome_overlay = WelcomeOverlay(self._overlay_container)
        self._welcome_overlay.set_click_callback(self._open_file)

        # Notification uses video_container as reference for positioning
        # It's a top-level window so it can render over QVideoWidget
        self._notification = NotificationOverlay(self._video_container, use_top_level=True)
        
        # Mode indicator

        # Time info overlay (top-level window to render over QVideoWidget)
        self._time_label = TimeInfoOverlay(self._video_container, align_right=False)
        self._time_label.setText("00:00.000 / 00:00.000")

        # Frame info overlay (top-level window to render over QVideoWidget)
        self._frame_label = TimeInfoOverlay(self._video_container, align_right=True)
        self._frame_label.setText("Frame: 0 / 0")

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
            # Only pause if actually playing
            if self._native_player.is_playing:
                self._native_player.pause()
        
        # Pause audio if playing
        if self._frame_controller and self._playing:
            self._frame_controller.audio_engine.pause()
        
        # Sync position and display frame
        if self._frame_controller:
            self._frame_controller.seek(current_time)
            # Display the frame BEFORE switching widgets to avoid black flash
            self._display_current_frame()
            # Force the widget to repaint immediately
            self._frame_video_widget.repaint()
        
        # Now switch to show the frame-accurate widget (which already has the frame)
        self._video_stack.setCurrentIndex(0)
        self._mode = PlaybackMode.FRAME_ACCURATE
        logger.debug("Switched to FRAME_ACCURATE mode")
    
    def _on_native_time_changed(self, time_pos: float) -> None:
        """Handle time updates from native player - keep audio in sync."""
        if self._mode == PlaybackMode.NATIVE and self._playing:
            self._update_time_display_from_time(time_pos)
            
            # Check for audio drift and resync if needed (only while actually playing)
            if self._frame_controller and self._playing:
                audio_time = self._frame_controller.audio_engine.current_time()
                drift = abs(time_pos - audio_time)
                # Resync if drift exceeds 200ms (increased threshold to reduce stuttering)
                if drift > 0.2:
                    logger.debug(f"Audio drift detected: {drift:.3f}s, resyncing")
                    self._frame_controller.audio_engine.seek(time_pos)
                    self._frame_controller.audio_engine.play()
    
    def _on_native_playback_ended(self) -> None:
        """Handle end of native playback."""
        self._playing = False
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
            self._time_label.show_overlay()
            self._frame_label.show_overlay()
            
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
            self._video_stack.setCurrentIndex(0)
            self._display_current_frame()
            self._update_time_display()
            self._update_overlay_geometry()  # Ensure overlays are properly positioned
            
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
        return self._playing
    
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
        if not self._frame_controller or self._playing:
            return
        
        # Get current time for syncing BEFORE setting _playing (avoid race)
        current_time = self._frame_controller.current_time
        
        # Ensure audio is stopped and reset before resuming (clear any bad state)
        self._frame_controller.audio_engine.pause()
        
        self._playing = True
        
        # If already in native mode (paused), just resume
        if self._mode == PlaybackMode.NATIVE and self._native_player:
            self._native_player.seek(current_time)
            self._frame_controller.audio_engine.seek(current_time)
            self._native_player.play()
            self._frame_controller.audio_engine.play()
        # Try to switch to native mode for video playback
        elif self._switch_to_native_mode() and self._native_player:
            # _switch_to_native_mode already seeked the native player
            self._frame_controller.audio_engine.seek(current_time)
            self._native_player.play()
            self._frame_controller.audio_engine.play()
        else:
            # Fallback to frame-accurate (both video and audio)
            self._frame_controller.play()
    
    def _pause(self) -> None:
        """Pause playback - stays in current mode, only switches when stepping."""
        if not self._playing:
            return
            
        self._playing = False
        
        if self._mode == PlaybackMode.NATIVE and self._native_player:
            # Pause both native video and our audio engine
            self._native_player.pause()
            if self._frame_controller:
                self._frame_controller.audio_engine.pause()
                # Sync position from native to frame-accurate (for later stepping)
                self._frame_controller.seek(self._native_player.current_time)
        else:
            if self._frame_controller:
                self._frame_controller.pause()
        
        # DON'T switch to frame-accurate mode here - stay in native mode
        # to avoid black flash. We'll switch when user actually steps.
    
    def _step_forward(self) -> None:
        if not self._frame_controller:
            return
        if self._frame_controller.current_frame >= self._frame_controller.total_frames - 1:
            self._show_notification("End", 500)
            return

        # Throttle step operations to prevent overwhelming the system
        current_time_ms = self._frame_timer.elapsed()
        if current_time_ms - self._last_step_time < self.MIN_STEP_INTERVAL_MS:
            return
        self._last_step_time = current_time_ms

        # Stop audio if we were playing (do this BEFORE setting _playing = False)
        if self._playing:
            self._frame_controller.audio_engine.pause()
            if self._native_player and self._native_player.is_playing:
                self._native_player.pause()

        # Stepping always means we're paused
        self._playing = False
        
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

        # Throttle step operations to prevent overwhelming the system
        current_time_ms = self._frame_timer.elapsed()
        if current_time_ms - self._last_step_time < self.MIN_STEP_INTERVAL_MS:
            return
        self._last_step_time = current_time_ms

        # Stop audio if we were playing (do this BEFORE setting _playing = False)
        if self._playing:
            self._frame_controller.audio_engine.pause()
            if self._native_player and self._native_player.is_playing:
                self._native_player.pause()

        # Stepping always means we're paused
        self._playing = False
        
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

        self._time_label.setText(f"{format_time(current_time)} / {format_time(duration)}")
        self._frame_label.setText(f"Frame: {current_frame} / {total_frames}")
        self._update_overlay_geometry()

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
            # Always sync native player position (it may be used when play resumes)
            if self._native_player:
                self._native_player.seek(self._frame_controller.current_time)
            self._display_current_frame()
            self._update_time_display()
            if self._was_playing_before_seek:
                self._play()

    def _on_slider_value_changed(self, value: int) -> None:
        if self._frame_controller and self._timeline_slider.isSliderDown():
            self._frame_controller.seek_to_frame(value)
            # Always sync native player during drag for visual feedback
            if self._native_player:
                self._native_player.seek(self._frame_controller.current_time)
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
        self._update_overlay_geometry()
    
    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._update_overlay_geometry()
    
    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._update_overlay_geometry()

    def _update_overlay_geometry(self) -> None:
        """Update overlay container size and position all overlays."""
        # Resize overlay container to match video container
        container_rect = self._video_container.rect()
        self._overlay_container.setGeometry(container_rect)
        self._overlay_container.raise_()
        
        margin = 10

        # Update time info overlays (top-level windows)
        if self._time_label.isVisible():
            self._time_label.update_position()
        if self._frame_label.isVisible():
            self._frame_label.update_position()
        
        # Mode indicator in top-left
        
        # Welcome overlay centered
        if self._welcome_overlay.isVisible():
            self._welcome_overlay.update_position()

    def closeEvent(self, event) -> None:
        self._refresh_timer.stop()
        self._stats_timer.stop()
        # Hide top-level overlay windows
        self._time_label.hide()
        self._frame_label.hide()
        self._notification.hide()
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
    apply_dark_theme(app)
    
    window = DualModeMainWindow()
    window.show()
    
    if len(sys.argv) > 1:
        window._load_file(sys.argv[1])
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
