"""
Hybrid Playback Controller
Combines native player (mpv) for smooth playback with frame-accurate decoder
for precise control (stepping, scrubbing).
"""

import logging
import threading
from typing import Optional, Callable
from enum import Enum, auto
import numpy as np

from src.video_decoder import VideoDecoder
from src.audio_engine import AudioEngine

logger = logging.getLogger(__name__)

# Check for native player availability
try:
    from src.native_player import NativePlayer, is_available as native_available
    NATIVE_PLAYER_AVAILABLE = native_available()
except ImportError:
    NATIVE_PLAYER_AVAILABLE = False
    NativePlayer = None


class PlaybackMode(Enum):
    """Current playback mode."""
    NATIVE = auto()      # Using mpv for smooth playback
    FRAME_ACCURATE = auto()  # Using PyAV decoder for frame-by-frame


class HybridPlaybackController:
    """
    Hybrid controller that switches between native player and frame-accurate decoder.
    
    - Normal playback: Uses mpv for hardware-accelerated, smooth playback
    - Paused/Stepping: Uses PyAV decoder for precise frame access
    
    The switch is automatic and seamless to the user.
    """
    
    def __init__(self, filepath: str, window_id: Optional[int] = None, 
                 prefer_native: bool = True):
        """
        Initialize the hybrid controller.
        
        Args:
            filepath: Path to the video file.
            window_id: Window ID for embedding native player (Windows HWND).
            prefer_native: Whether to prefer native player for playback.
        """
        self.filepath = filepath
        self._window_id = window_id
        self._prefer_native = prefer_native and NATIVE_PLAYER_AVAILABLE
        
        # Initialize frame-accurate decoder (always needed for stepping/metadata)
        self._video_decoder = VideoDecoder(filepath)
        self._audio_engine = AudioEngine(filepath)
        
        # Native player (lazy initialization)
        self._native_player: Optional[NativePlayer] = None
        self._native_initialized = False
        
        # Current mode
        self._mode = PlaybackMode.FRAME_ACCURATE
        self._playing = False
        self._lock = threading.Lock()
        
        # Time tracking
        self._current_time: float = 0.0
        self._last_frame_index: int = -1
        
        # Callbacks
        self._on_time_update: Optional[Callable[[float], None]] = None
        self._on_mode_change: Optional[Callable[[PlaybackMode], None]] = None
        self._on_end_reached: Optional[Callable[[], None]] = None
        
        logger.info(f"HybridController: Native player {'available' if self._prefer_native else 'unavailable'}")
    
    def _init_native_player(self) -> bool:
        """Initialize native player on first use."""
        if self._native_initialized:
            return self._native_player is not None
        
        self._native_initialized = True
        
        if not NATIVE_PLAYER_AVAILABLE:
            logger.warning("Native player not available, using frame-accurate mode only")
            return False
        
        try:
            self._native_player = NativePlayer(wid=self._window_id)
            self._native_player.open(self.filepath)
            
            # Setup callbacks
            self._native_player.set_time_pos_callback(self._on_native_time_update)
            self._native_player.set_end_file_callback(self._on_native_end)
            
            logger.info("HybridController: Native player initialized")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize native player: {e}")
            self._native_player = None
            return False
    
    def _on_native_time_update(self, time_pos: float) -> None:
        """Handle time updates from native player."""
        self._current_time = time_pos
        if self._on_time_update:
            self._on_time_update(time_pos)
    
    def _on_native_end(self) -> None:
        """Handle end of file from native player."""
        self._playing = False
        if self._on_end_reached:
            self._on_end_reached()
    
    def _switch_to_native(self) -> bool:
        """Switch to native playback mode."""
        if not self._prefer_native:
            return False
        
        if not self._init_native_player():
            return False
        
        with self._lock:
            if self._mode == PlaybackMode.NATIVE:
                return True
            
            # Sync position
            if self._native_player:
                self._native_player.seek(self._current_time, precise=True)
            
            # Stop frame-accurate audio
            self._audio_engine.pause()
            
            self._mode = PlaybackMode.NATIVE
            logger.debug("Switched to NATIVE mode")
            
            if self._on_mode_change:
                self._on_mode_change(PlaybackMode.NATIVE)
            
            return True
    
    def _switch_to_frame_accurate(self) -> None:
        """Switch to frame-accurate mode."""
        with self._lock:
            if self._mode == PlaybackMode.FRAME_ACCURATE:
                return
            
            # Get current position from native player
            if self._native_player:
                self._current_time = self._native_player.current_time
                self._native_player.pause()
            
            self._mode = PlaybackMode.FRAME_ACCURATE
            logger.debug("Switched to FRAME_ACCURATE mode")
            
            if self._on_mode_change:
                self._on_mode_change(PlaybackMode.FRAME_ACCURATE)
    
    # === Properties (match PlaybackController interface) ===
    
    @property
    def video_decoder(self) -> VideoDecoder:
        """Access to video decoder for frame-accurate operations."""
        return self._video_decoder
    
    @property
    def audio_engine(self) -> AudioEngine:
        """Access to audio engine."""
        return self._audio_engine
    
    @property
    def is_playing(self) -> bool:
        """Whether playback is active."""
        with self._lock:
            return self._playing
    
    @property
    def current_mode(self) -> PlaybackMode:
        """Current playback mode."""
        return self._mode
    
    @property
    def current_time(self) -> float:
        """Current playback time in seconds."""
        if self._mode == PlaybackMode.NATIVE and self._native_player:
            return self._native_player.current_time
        return self._current_time
    
    @property
    def current_frame(self) -> int:
        """Current frame index."""
        return int(self.current_time * self.fps)
    
    @property
    def duration(self) -> float:
        """Total duration in seconds."""
        return self._video_decoder.duration
    
    @property
    def total_frames(self) -> int:
        """Total number of frames."""
        return self._video_decoder.total_frames
    
    @property
    def fps(self) -> float:
        """Frames per second."""
        return self._video_decoder.fps
    
    @property
    def num_audio_tracks(self) -> int:
        """Number of audio tracks."""
        return self._audio_engine.num_tracks
    
    @property
    def native_available(self) -> bool:
        """Whether native player is available."""
        return NATIVE_PLAYER_AVAILABLE and self._prefer_native
    
    # === Playback Control ===
    
    def play(self) -> None:
        """Start playback (uses native player if available)."""
        with self._lock:
            if self._playing:
                return
            self._playing = True
        
        # Try to use native player for smooth playback
        if self._switch_to_native():
            if self._native_player:
                self._native_player.play()
        else:
            # Fallback to frame-accurate playback
            self._audio_engine.seek(self._current_time)
            self._audio_engine.play()
    
    def pause(self) -> None:
        """Pause playback (switches to frame-accurate mode)."""
        with self._lock:
            if not self._playing:
                return
            self._playing = False
        
        if self._mode == PlaybackMode.NATIVE and self._native_player:
            # Capture current position before pausing
            self._current_time = self._native_player.current_time
            self._native_player.pause()
        else:
            self._audio_engine.pause()
            self._current_time = self._audio_engine.current_time()
        
        # Switch to frame-accurate mode when paused (for stepping)
        self._switch_to_frame_accurate()
    
    def toggle_playback(self) -> None:
        """Toggle between play and pause."""
        if self.is_playing:
            self.pause()
        else:
            self.play()
    
    def seek(self, time_seconds: float) -> None:
        """Seek to a specific time."""
        time_seconds = max(0.0, min(time_seconds, self.duration))
        
        was_playing = self.is_playing
        
        if was_playing:
            self.pause()
        
        self._current_time = time_seconds
        
        # Seek both players to keep them in sync
        if self._native_player:
            self._native_player.seek(time_seconds, precise=True)
        
        if was_playing:
            self.play()
    
    def seek_to_frame(self, frame_index: int) -> None:
        """Seek to a specific frame."""
        time_seconds = frame_index / self.fps if self.fps > 0 else 0.0
        self.seek(time_seconds)
    
    def step_forward(self, num_frames: int = 1) -> Optional[np.ndarray]:
        """
        Step forward by frames. Pauses and switches to frame-accurate mode.
        Returns the new frame.
        """
        if self.is_playing:
            self.pause()
        
        # Ensure we're in frame-accurate mode
        self._switch_to_frame_accurate()
        
        # Step time
        frame_duration = 1.0 / self.fps if self.fps > 0 else 1.0 / 30.0
        self._current_time = max(0.0, self._current_time + num_frames * frame_duration)
        
        return self.get_current_frame()
    
    def step_backward(self, num_frames: int = 1) -> Optional[np.ndarray]:
        """
        Step backward by frames. Pauses and switches to frame-accurate mode.
        Returns the new frame.
        """
        if self.is_playing:
            self.pause()
        
        # Ensure we're in frame-accurate mode
        self._switch_to_frame_accurate()
        
        # Step time
        frame_duration = 1.0 / self.fps if self.fps > 0 else 1.0 / 30.0
        self._current_time = max(0.0, self._current_time - num_frames * frame_duration)
        
        return self.get_current_frame()
    
    def get_current_frame(self) -> Optional[np.ndarray]:
        """Get the current frame (uses frame-accurate decoder)."""
        frame_index = self.current_frame
        frame_index = max(0, min(frame_index, self.total_frames - 1))
        return self._video_decoder.get_frame(frame_index)
    
    def get_frame_at_time(self, time_seconds: float) -> Optional[np.ndarray]:
        """Get the frame at a specific time."""
        frame_index = int(time_seconds * self.fps)
        frame_index = max(0, min(frame_index, self.total_frames - 1))
        return self._video_decoder.get_frame(frame_index)
    
    # === Audio Control ===
    
    def set_track_volume(self, track_index: int, volume: float) -> None:
        """Set volume for an audio track."""
        self._audio_engine.set_track_volume(track_index, volume)
        # Also update native player if using it
        if self._native_player:
            self._native_player.set_volume(volume)
    
    def set_track_muted(self, track_index: int, muted: bool) -> None:
        """Mute or unmute an audio track."""
        self._audio_engine.set_track_muted(track_index, muted)
        if self._native_player:
            self._native_player.set_muted(muted)
    
    # === Callbacks ===
    
    def set_time_update_callback(self, callback: Callable[[float], None]) -> None:
        """Register callback for time position updates."""
        self._on_time_update = callback
    
    def set_mode_change_callback(self, callback: Callable[[PlaybackMode], None]) -> None:
        """Register callback for mode changes."""
        self._on_mode_change = callback
    
    def set_end_reached_callback(self, callback: Callable[[], None]) -> None:
        """Register callback for end of playback."""
        self._on_end_reached = callback
    
    # === Resource Management ===
    
    def close(self) -> None:
        """Release all resources."""
        self.pause()
        
        if self._native_player:
            self._native_player.close()
            self._native_player = None
        
        self._audio_engine.close()
        self._video_decoder.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
