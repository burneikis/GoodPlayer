"""
Native Video Player using mpv for efficient hardware-accelerated playback.
Swaps to frame-by-frame mode when precise control is needed.
"""

import logging
import threading
from typing import Optional, Callable
from pathlib import Path

logger = logging.getLogger(__name__)

# Try to import mpv, provide fallback info if not available
try:
    import mpv
    MPV_AVAILABLE = True
except ImportError:
    MPV_AVAILABLE = False
    logger.warning("python-mpv not installed. Install with: pip install python-mpv")
    logger.warning("Also ensure mpv/libmpv is installed on your system.")


class NativePlayer:
    """
    Native video player wrapper using mpv for hardware-accelerated playback.
    
    This provides smooth playback for normal viewing, while allowing
    the application to switch to frame-accurate mode when needed.
    """
    
    def __init__(self, wid: Optional[int] = None):
        """
        Initialize the native player.
        
        Args:
            wid: Window ID to embed the player into (platform-specific).
                 On Windows, this is the HWND.
        """
        if not MPV_AVAILABLE:
            raise RuntimeError(
                "mpv library not available. Install with:\n"
                "  pip install python-mpv\n"
                "And ensure libmpv is installed on your system."
            )
        
        self._wid = wid
        self._player: Optional[mpv.MPV] = None
        self._filepath: Optional[str] = None
        self._lock = threading.Lock()
        
        # Callbacks
        self._on_time_pos: Optional[Callable[[float], None]] = None
        self._on_end_file: Optional[Callable[[], None]] = None
        self._on_duration: Optional[Callable[[float], None]] = None
        
        # State
        self._duration: float = 0.0
        self._current_time: float = 0.0
        self._is_playing: bool = False
        self._volume: float = 100.0
        
    def _create_player(self) -> None:
        """Create the mpv player instance with optimal settings."""
        # Create player with window embedding
        if self._wid:
            self._player = mpv.MPV(
                wid=str(self._wid),
                log_handler=self._log_handler,
                loglevel='warn'
            )
        else:
            self._player = mpv.MPV(
                log_handler=self._log_handler,
                loglevel='warn'
            )
        
        # Hardware acceleration
        self._player['hwdec'] = 'auto-safe'
        
        # Video output settings for smooth playback
        self._player['vo'] = 'gpu'  # Use GPU video output
        self._player['gpu-api'] = 'auto'  # Auto-select best GPU API
        
        # Sync settings for smooth playback
        self._player['video-sync'] = 'display-resample'  # Resample to display refresh
        self._player['interpolation'] = 'yes'  # Smooth frame interpolation
        self._player['tscale'] = 'oversample'  # Temporal scaling for interpolation
        
        # Audio settings
        self._player['audio-display'] = 'no'  # Don't show audio visualizations
        
        # Keep open for seeking at end
        self._player['keep-open'] = 'yes'
        
        # Observe properties
        self._player.observe_property('time-pos', self._on_time_pos_changed)
        self._player.observe_property('duration', self._on_duration_changed)
        self._player.observe_property('pause', self._on_pause_changed)
        
        # Register end-file event
        @self._player.event_callback('end-file')
        def end_file_handler(event):
            if self._on_end_file:
                self._on_end_file()
    
    def _log_handler(self, loglevel: str, component: str, message: str) -> None:
        """Handle mpv log messages."""
        if loglevel in ('error', 'fatal'):
            logger.error(f"mpv [{component}]: {message}")
        elif loglevel == 'warn':
            logger.warning(f"mpv [{component}]: {message}")
        elif loglevel in ('info', 'v'):
            logger.debug(f"mpv [{component}]: {message}")
    
    def _on_time_pos_changed(self, name: str, value) -> None:
        """Handle time position updates."""
        if value is not None:
            self._current_time = float(value)
            if self._on_time_pos:
                self._on_time_pos(self._current_time)
    
    def _on_duration_changed(self, name: str, value) -> None:
        """Handle duration updates."""
        if value is not None:
            self._duration = float(value)
            if self._on_duration:
                self._on_duration(self._duration)
    
    def _on_pause_changed(self, name: str, value) -> None:
        """Handle pause state changes."""
        if value is not None:
            self._is_playing = not value
    
    def open(self, filepath: str) -> bool:
        """
        Open a video file.
        
        Returns:
            True if successful, False otherwise.
        """
        with self._lock:
            try:
                self._filepath = filepath
                
                # Create player if not exists
                if self._player is None:
                    self._create_player()
                
                # Load file (start paused)
                self._player.pause = True
                self._player.loadfile(filepath)
                
                # Wait for the file to load (not for playback to end --
                # wait_for_playback() would block until EOF/idle, which with
                # keep-open=yes and pause=True can hang indefinitely).
                # Poll for duration becoming available with a short timeout.
                import time as _time
                deadline = _time.monotonic() + 5.0
                while _time.monotonic() < deadline:
                    if self._player.duration is not None:
                        break
                    _time.sleep(0.02)
                
                logger.info(f"NativePlayer: Opened {Path(filepath).name}")
                return True
                
            except Exception as e:
                logger.error(f"NativePlayer: Failed to open file: {e}")
                return False
    
    def play(self) -> None:
        """Start playback."""
        with self._lock:
            if self._player:
                self._player.pause = False
                self._is_playing = True
    
    def pause(self) -> None:
        """Pause playback."""
        with self._lock:
            if self._player:
                self._player.pause = True
                self._is_playing = False
    
    def toggle_playback(self) -> None:
        """Toggle play/pause."""
        if self._is_playing:
            self.pause()
        else:
            self.play()
    
    def seek(self, time_seconds: float, precise: bool = False) -> None:
        """
        Seek to a specific time.
        
        Args:
            time_seconds: Target time in seconds.
            precise: If True, seek to exact position (slower).
                    If False, seek to nearest keyframe (faster).
        """
        with self._lock:
            if self._player:
                mode = 'absolute+exact' if precise else 'absolute'
                self._player.seek(time_seconds, mode)
    
    def seek_frame(self, frames: int) -> None:
        """
        Seek by number of frames (relative).
        
        Args:
            frames: Number of frames to seek (positive = forward, negative = backward).
        """
        with self._lock:
            if self._player:
                # mpv's frame-step and frame-back-step commands
                if frames > 0:
                    for _ in range(frames):
                        self._player.command('frame-step')
                elif frames < 0:
                    for _ in range(-frames):
                        self._player.command('frame-back-step')
    
    def step_forward(self) -> None:
        """Step forward one frame."""
        with self._lock:
            if self._player:
                self._player.command('frame-step')
    
    def step_backward(self) -> None:
        """Step backward one frame."""
        with self._lock:
            if self._player:
                self._player.command('frame-back-step')
    
    def set_volume(self, volume: float) -> None:
        """
        Set volume level.
        
        Args:
            volume: Volume level from 0.0 to 1.0.
        """
        with self._lock:
            self._volume = max(0.0, min(1.0, volume)) * 100
            if self._player:
                self._player.volume = self._volume
    
    def set_muted(self, muted: bool) -> None:
        """Set mute state."""
        with self._lock:
            if self._player:
                self._player.mute = muted
    
    def set_audio_track(self, track_id: int) -> None:
        """
        Set the active audio track.
        
        Args:
            track_id: Audio track ID (1-based), or 0 to disable audio.
        """
        with self._lock:
            if self._player:
                self._player.aid = track_id if track_id > 0 else 'no'
    
    @property
    def current_time(self) -> float:
        """Current playback time in seconds."""
        return self._current_time
    
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
    
    def set_time_pos_callback(self, callback: Callable[[float], None]) -> None:
        """Set callback for time position updates."""
        self._on_time_pos = callback
    
    def set_end_file_callback(self, callback: Callable[[], None]) -> None:
        """Set callback for end of file."""
        self._on_end_file = callback
    
    def set_duration_callback(self, callback: Callable[[float], None]) -> None:
        """Set callback for duration updates."""
        self._on_duration = callback
    
    def close(self) -> None:
        """Release resources."""
        with self._lock:
            if self._player:
                try:
                    self._player.terminate()
                except Exception:
                    pass
                self._player = None
            self._filepath = None
    
    def __del__(self):
        self.close()


def is_available() -> bool:
    """Check if native player is available."""
    return MPV_AVAILABLE
