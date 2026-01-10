"""
Stage 3: Playback Clock & Control Layer
Centralized control of playback state with audio as master clock.
"""

import threading
from typing import Optional, Callable
import numpy as np

from video_decoder import VideoDecoder
from audio_engine import AudioEngine


class PlaybackClock:
    """
    Playback clock that uses audio time as master.
    When paused, maintains a manual time position.
    """
    
    def __init__(self, audio_engine: AudioEngine, fps: float):
        self._audio_engine = audio_engine
        self._fps = fps
        self._frame_duration = 1.0 / fps if fps > 0 else 1.0 / 30.0
        
        # Manual time used when paused or frame-stepping
        self._manual_time: float = 0.0
        self._use_manual_time: bool = True  # Start paused
        self._lock = threading.Lock()
    
    @property
    def current_time(self) -> float:
        """Get current playback time in seconds."""
        with self._lock:
            if self._use_manual_time:
                return self._manual_time
            else:
                return self._audio_engine.current_time()
    
    @property
    def current_frame(self) -> int:
        """Get current frame index."""
        return int(self.current_time * self._fps)
    
    def set_manual_time(self, time_seconds: float) -> None:
        """Set manual time (used when paused)."""
        with self._lock:
            self._manual_time = max(0.0, time_seconds)
    
    def get_manual_time(self) -> float:
        """Get the manual time value directly."""
        with self._lock:
            return self._manual_time
    
    def sync_to_audio(self) -> None:
        """Switch to using audio time as master."""
        with self._lock:
            self._use_manual_time = False
    
    def use_manual(self) -> None:
        """Switch to using manual time (capture current audio time first)."""
        with self._lock:
            if not self._use_manual_time:
                self._manual_time = self._audio_engine.current_time()
            self._use_manual_time = True
    
    def step_by_frames(self, num_frames: int) -> None:
        """Step time by a number of frames (positive or negative)."""
        with self._lock:
            self._manual_time = max(0.0, self._manual_time + num_frames * self._frame_duration)
            self._use_manual_time = True
    
    @property
    def frame_duration(self) -> float:
        """Duration of one frame in seconds."""
        return self._frame_duration
    
    @property
    def fps(self) -> float:
        """Frames per second."""
        return self._fps


class PlaybackController:
    """
    Central controller for synchronized video/audio playback.
    
    Key features:
    - Audio is the master clock during playback
    - Frame-accurate stepping when paused
    - Coordinated seek across video and audio
    """
    
    def __init__(self, filepath: str):
        self.filepath = filepath
        
        # Initialize components
        self._video_decoder = VideoDecoder(filepath)
        self._audio_engine = AudioEngine(filepath)
        
        # Playback clock
        self._clock = PlaybackClock(self._audio_engine, self._video_decoder.fps)
        
        # State
        self._playing = False
        self._needs_audio_sync = False  # Flag to indicate audio needs to be synced
        self._lock = threading.Lock()
        
        # Callbacks for state changes
        self._on_frame_change: Optional[Callable[[np.ndarray, int], None]] = None
        self._on_state_change: Optional[Callable[[bool], None]] = None
        
        # Track the audio position when we last paused
        self._last_audio_sync_time: float = 0.0
    
    @property
    def video_decoder(self) -> VideoDecoder:
        """Access to video decoder."""
        return self._video_decoder
    
    @property
    def audio_engine(self) -> AudioEngine:
        """Access to audio engine."""
        return self._audio_engine
    
    @property
    def clock(self) -> PlaybackClock:
        """Access to playback clock."""
        return self._clock
    
    @property
    def is_playing(self) -> bool:
        """Whether playback is currently active."""
        with self._lock:
            return self._playing
    
    @property
    def current_time(self) -> float:
        """Current playback time in seconds."""
        return self._clock.current_time
    
    @property
    def current_frame(self) -> int:
        """Current frame index."""
        return self._clock.current_frame
    
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
    
    def play(self) -> None:
        with self._lock:
            if self._playing:
                return
            self._playing = True
            needs_sync = self._needs_audio_sync
            self._needs_audio_sync = False
        
        # Get the current manual time (where we should start playing from)
        target_time = self._clock.get_manual_time()
        
        # Always seek audio to ensure proper sync
        # This clears buffers and refills from the correct position
        self._audio_engine.seek(target_time)
        
        # Start audio playback
        self._audio_engine.play()
        
        # Switch clock to audio master
        self._clock.sync_to_audio()
        
        if self._on_state_change:
            self._on_state_change(True)
    
    def pause(self) -> None:
        with self._lock:
            if not self._playing:
                return
            self._playing = False
        
        # Pause audio first
        self._audio_engine.pause()
        
        # Switch clock to manual (captures current audio time)
        self._clock.use_manual()
        
        if self._on_state_change:
            self._on_state_change(False)
    
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
        
        # Update clock
        self._clock.set_manual_time(time_seconds)
        
        # Mark that audio needs sync (will happen on play)
        with self._lock:
            self._needs_audio_sync = True
        
        if was_playing:
            self.play()
    
    def seek_to_frame(self, frame_index: int) -> None:
        """Seek to a specific frame."""
        time_seconds = frame_index / self.fps if self.fps > 0 else 0.0
        self.seek(time_seconds)
    
    def step_forward(self, num_frames: int = 1) -> Optional[np.ndarray]:
        """
        Step forward by specified number of frames.
        Pauses playback if playing.
        Returns the new frame.
        """
        if self.is_playing:
            self.pause()
        
        self._clock.step_by_frames(num_frames)
        
        # Mark that audio needs to be synced when we resume
        with self._lock:
            self._needs_audio_sync = True
        
        return self.get_current_frame()
    
    def step_backward(self, num_frames: int = 1) -> Optional[np.ndarray]:
        """
        Step backward by specified number of frames.
        Pauses playback if playing.
        Returns the new frame.
        """
        if self.is_playing:
            self.pause()
        
        self._clock.step_by_frames(-num_frames)
        
        # Mark that audio needs to be synced when we resume
        with self._lock:
            self._needs_audio_sync = True
        
        return self.get_current_frame()
    
    def get_current_frame(self) -> Optional[np.ndarray]:
        """Get the frame for the current playback time."""
        frame_index = self._clock.current_frame
        frame_index = max(0, min(frame_index, self.total_frames - 1))
        return self._video_decoder.get_frame(frame_index)
    
    def get_frame_at_time(self, time_seconds: float) -> Optional[np.ndarray]:
        """Get the frame at a specific time."""
        frame_index = int(time_seconds * self.fps)
        frame_index = max(0, min(frame_index, self.total_frames - 1))
        return self._video_decoder.get_frame(frame_index)
    
    def set_track_volume(self, track_index: int, volume: float) -> None:
        """Set volume for an audio track."""
        self._audio_engine.set_track_volume(track_index, volume)
    
    def set_track_muted(self, track_index: int, muted: bool) -> None:
        """Mute or unmute an audio track."""
        self._audio_engine.set_track_muted(track_index, muted)
    
    @property
    def num_audio_tracks(self) -> int:
        """Number of audio tracks."""
        return self._audio_engine.num_tracks
    
    def on_frame_change(self, callback: Callable[[np.ndarray, int], None]) -> None:
        """Register callback for frame changes."""
        self._on_frame_change = callback
    
    def on_state_change(self, callback: Callable[[bool], None]) -> None:
        """Register callback for play/pause state changes."""
        self._on_state_change = callback
    
    def close(self) -> None:
        """Release all resources."""
        self.pause()
        self._audio_engine.close()
        self._video_decoder.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
