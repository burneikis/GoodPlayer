"""
Stage 2 & 5: Audio Engine (Multitrack) with Performance Monitoring
Provides stable, mixed audio playback with sample accuracy.
"""

import av
import logging
import numpy as np
import sounddevice as sd
import threading
from typing import Optional

# Setup logging
logger = logging.getLogger(__name__)


class RingBuffer:
    """Thread-safe ring buffer for audio samples."""
    
    def __init__(self, max_seconds: float, sample_rate: int, channels: int):
        self.sample_rate = sample_rate
        self.channels = channels
        max_samples = int(max_seconds * sample_rate)
        self._buffer = np.zeros((max_samples, channels), dtype=np.float32)
        self._write_pos = 0
        self._read_pos = 0
        self._size = 0
        self._capacity = max_samples
        self._lock = threading.Lock()
        self._underruns = 0
    
    def write(self, data: np.ndarray) -> int:
        with self._lock:
            samples_to_write = min(len(data), self._capacity - self._size)
            if samples_to_write == 0:
                return 0
            
            first_chunk = min(samples_to_write, self._capacity - self._write_pos)
            self._buffer[self._write_pos:self._write_pos + first_chunk] = data[:first_chunk]
            
            if first_chunk < samples_to_write:
                second_chunk = samples_to_write - first_chunk
                self._buffer[:second_chunk] = data[first_chunk:first_chunk + second_chunk]
            
            self._write_pos = (self._write_pos + samples_to_write) % self._capacity
            self._size += samples_to_write
            return samples_to_write
    
    def read(self, num_samples: int) -> np.ndarray:
        with self._lock:
            samples_to_read = min(num_samples, self._size)
            
            if samples_to_read < num_samples:
                self._underruns += 1
            
            if samples_to_read == 0:
                return np.zeros((0, self.channels), dtype=np.float32)
            
            result = np.zeros((samples_to_read, self.channels), dtype=np.float32)
            
            first_chunk = min(samples_to_read, self._capacity - self._read_pos)
            result[:first_chunk] = self._buffer[self._read_pos:self._read_pos + first_chunk]
            
            if first_chunk < samples_to_read:
                second_chunk = samples_to_read - first_chunk
                result[first_chunk:] = self._buffer[:second_chunk]
            
            self._read_pos = (self._read_pos + samples_to_read) % self._capacity
            self._size -= samples_to_read
            return result
    
    def available(self) -> int:
        with self._lock:
            return self._size
    
    def space_available(self) -> int:
        with self._lock:
            return self._capacity - self._size
    
    def clear(self) -> None:
        with self._lock:
            self._write_pos = 0
            self._read_pos = 0
            self._size = 0
    
    @property
    def underruns(self) -> int:
        with self._lock:
            return self._underruns
    
    def reset_underruns(self) -> None:
        with self._lock:
            self._underruns = 0


class AudioTrack:
    """Represents a single audio track with its own decoder and resampler."""
    
    def __init__(self, stream: av.audio.AudioStream,
                 target_sample_rate: int, target_channels: int):
        self.stream = stream
        self.target_sample_rate = target_sample_rate
        self.target_channels = target_channels
        
        # Get source stream properties
        self.source_sample_rate = stream.codec_context.sample_rate or 48000
        self.source_layout = stream.codec_context.layout.name if stream.codec_context.layout else 'stereo'
        self.source_channels = stream.codec_context.channels or 2
        
        logger.info(f"Audio track: {self.source_sample_rate}Hz, {self.source_channels}ch ({self.source_layout}) -> {target_sample_rate}Hz, {target_channels}ch")
        
        # Create resampler with explicit source and target formats
        target_layout = 'stereo' if target_channels == 2 else 'mono'
        self.resampler = av.AudioResampler(
            format='fltp',  # Planar float - easier to work with
            layout=target_layout,
            rate=target_sample_rate
        )
        
        self.buffer = RingBuffer(5.0, target_sample_rate, target_channels)
        self.volume = 1.0
        self.muted = False
    
    def decode_frame(self, frame: av.AudioFrame) -> None:
        """Decode an audio frame and add resampled audio to buffer."""
        try:
            # Resample the frame
            resampled_frames = self.resampler.resample(frame)
            
            for resampled in resampled_frames:
                if resampled is None:
                    continue
                
                # Convert to numpy - format is planar float (fltp)
                # Shape will be (channels, samples) for planar formats
                audio_array = resampled.to_ndarray()
                
                # Handle different array shapes
                if audio_array.ndim == 1:
                    # Mono, single dimension
                    audio_data = audio_array.reshape(-1, 1)
                    if self.target_channels == 2:
                        audio_data = np.column_stack([audio_data, audio_data])
                elif audio_array.shape[0] <= 2 and audio_array.shape[1] > audio_array.shape[0]:
                    # Planar format: (channels, samples) -> need to transpose to (samples, channels)
                    audio_data = audio_array.T
                else:
                    # Already (samples, channels) or interleaved
                    audio_data = audio_array
                
                # Ensure correct number of channels
                if audio_data.shape[1] == 1 and self.target_channels == 2:
                    audio_data = np.column_stack([audio_data, audio_data])
                elif audio_data.shape[1] > self.target_channels:
                    audio_data = audio_data[:, :self.target_channels]
                
                self.buffer.write(audio_data.astype(np.float32))
                
        except Exception as e:
            logger.debug(f"Audio decode error: {e}")


class AudioEngine:
    """
    Multi-track audio engine with mixing, playback clock, and monitoring.
    """
    
    TARGET_SAMPLE_RATE = 48000
    TARGET_CHANNELS = 2
    BUFFER_SIZE = 1024
    
    def __init__(self, filepath: str, max_tracks: int = 3):
        self.filepath = filepath
        self.max_tracks = max_tracks
        
        self._container: Optional[av.Container] = None
        self._tracks: list[AudioTrack] = []
        self._stream_map: dict[int, int] = {}
        
        self._playing = False
        self._paused_time: float = 0.0
        self._samples_played: int = 0
        self._samples_lock = threading.Lock()
        
        self._stream: Optional[sd.OutputStream] = None
        
        self._decoder_thread: Optional[threading.Thread] = None
        self._decoder_running = False
        self._seek_requested: Optional[float] = None
        self._seek_lock = threading.Lock()
        self._seek_complete = threading.Event()
        self._eof_reached = False
        
        self._callback_underruns = 0
        self._total_callbacks = 0
        
        self._open(filepath)
    
    def _open(self, filepath: str) -> None:
        self._container = av.open(filepath)
        
        audio_streams = list(self._container.streams.audio)
        num_tracks = min(len(audio_streams), self.max_tracks)
        
        if num_tracks == 0:
            logger.warning("No audio streams found")
            return
        
        for i in range(num_tracks):
            stream = audio_streams[i]
            # Set thread type for faster decoding
            stream.thread_type = "AUTO"
            
            track = AudioTrack(
                stream,
                self.TARGET_SAMPLE_RATE,
                self.TARGET_CHANNELS
            )
            self._tracks.append(track)
            self._stream_map[stream.index] = i
        
        logger.info(f"Opened {num_tracks} audio track(s)")
        
        self._decoder_running = True
        self._decoder_thread = threading.Thread(target=self._decoder_loop, daemon=True)
        self._decoder_thread.start()
    
    def _decoder_loop(self) -> None:
        while self._decoder_running:
            with self._seek_lock:
                if self._seek_requested is not None:
                    self._perform_seek(self._seek_requested)
                    self._seek_requested = None
                    self._seek_complete.set()
            
            if not self._tracks:
                threading.Event().wait(0.1)
                continue
            
            min_available = min(t.buffer.available() for t in self._tracks)
            
            if min_available < self.TARGET_SAMPLE_RATE * 2 and not self._eof_reached:
                self._decode_more()
            else:
                threading.Event().wait(0.01)
    
    def _decode_more(self) -> None:
        if not self._tracks or not self._container:
            return
        
        try:
            audio_streams = [t.stream for t in self._tracks]
            
            frames_decoded = 0
            for packet in self._container.demux(audio_streams):
                if packet.dts is None:
                    continue
                
                stream_idx = packet.stream.index
                if stream_idx in self._stream_map:
                    track_idx = self._stream_map[stream_idx]
                    track = self._tracks[track_idx]
                    
                    try:
                        for frame in packet.decode():
                            track.decode_frame(frame)
                            frames_decoded += 1
                    except Exception as e:
                        logger.debug(f"Packet decode error: {e}")
                
                if frames_decoded >= 50:
                    break
                
                with self._seek_lock:
                    if self._seek_requested is not None:
                        return
                        
        except av.EOFError:
            self._eof_reached = True
        except Exception as e:
            logger.debug(f"Audio decode error: {e}")
    
    def _perform_seek(self, time_seconds: float) -> None:
        for track in self._tracks:
            track.buffer.clear()
            # Reset the resampler to clear any buffered data
            target_layout = 'stereo' if track.target_channels == 2 else 'mono'
            track.resampler = av.AudioResampler(
                format='fltp',
                layout=target_layout,
                rate=track.target_sample_rate
            )
        
        self._eof_reached = False
        
        if self._container:
            try:
                # Seek to the target time
                # Use the audio stream's time_base for more precise seeking
                if self._tracks:
                    stream = self._tracks[0].stream
                    # Convert seconds to stream time_base units
                    pts = int(time_seconds / stream.time_base)
                    self._container.seek(pts, stream=stream, backward=True, any_frame=False)
                else:
                    timestamp = int(time_seconds * av.time_base)
                    self._container.seek(timestamp, backward=True, any_frame=False)
            except Exception as e:
                logger.warning(f"Seek failed: {e}, seeking to start")
                try:
                    self._container.seek(0)
                except Exception:
                    pass
        
        with self._samples_lock:
            self._samples_played = int(time_seconds * self.TARGET_SAMPLE_RATE)
        
        # Pre-fill buffers after seek
        self._prefill_buffers()
    
    def _prefill_buffers(self) -> None:
        """Decode enough audio to fill buffers for smooth playback."""
        if not self._tracks or not self._container:
            return
        
        target_samples = self.TARGET_SAMPLE_RATE  # 1 second of audio
        
        try:
            audio_streams = [t.stream for t in self._tracks]
            
            while True:
                min_available = min(t.buffer.available() for t in self._tracks)
                if min_available >= target_samples:
                    break
                
                # Decode more
                frames_decoded = 0
                for packet in self._container.demux(audio_streams):
                    if packet.dts is None:
                        continue
                    
                    stream_idx = packet.stream.index
                    if stream_idx in self._stream_map:
                        track_idx = self._stream_map[stream_idx]
                        track = self._tracks[track_idx]
                        
                        try:
                            for frame in packet.decode():
                                track.decode_frame(frame)
                                frames_decoded += 1
                        except Exception:
                            pass
                    
                    # Check if we have enough
                    min_available = min(t.buffer.available() for t in self._tracks)
                    if min_available >= target_samples:
                        break
                    
                    if frames_decoded >= 200:
                        break
                
                if frames_decoded == 0:
                    break  # EOF or error
                    
        except av.EOFError:
            self._eof_reached = True
        except Exception as e:
            logger.debug(f"Prefill error: {e}")
    
    def _audio_callback(self, outdata: np.ndarray, frames: int,
                        time_info, status) -> None:
        """Audio callback - must never block."""
        self._total_callbacks += 1
        
        if status:
            self._callback_underruns += 1
        
        if not self._playing or not self._tracks:
            outdata.fill(0)
            return
        
        mixed = np.zeros((frames, self.TARGET_CHANNELS), dtype=np.float32)
        any_data = False
        
        for track in self._tracks:
            if track.muted:
                continue
            
            data = track.buffer.read(frames)
            if len(data) > 0:
                any_data = True
                if len(data) < frames:
                    padded = np.zeros((frames, self.TARGET_CHANNELS), dtype=np.float32)
                    padded[:len(data)] = data
                    data = padded
                
                mixed += data * track.volume
        
        if not any_data and not self._eof_reached:
            self._callback_underruns += 1
        
        np.clip(mixed, -1.0, 1.0, out=mixed)
        outdata[:] = mixed
        
        if self._samples_lock.acquire(blocking=False):
            try:
                self._samples_played += frames
            finally:
                self._samples_lock.release()
    
    def play(self) -> None:
        if self._playing:
            return
        
        if not self._tracks:
            logger.warning("No audio tracks to play")
            return
        
        if self._stream is None:
            self._stream = sd.OutputStream(
                samplerate=self.TARGET_SAMPLE_RATE,
                channels=self.TARGET_CHANNELS,
                dtype=np.float32,
                blocksize=self.BUFFER_SIZE,
                callback=self._audio_callback
            )
        
        self._playing = True
        self._stream.start()
        logger.debug("Audio playback started")
    
    def pause(self) -> None:
        if not self._playing:
            return
        
        with self._samples_lock:
            current_samples = self._samples_played
        self._paused_time = current_samples / self.TARGET_SAMPLE_RATE
        
        self._playing = False
        
        if self._stream:
            try:
                self._stream.stop()
            except Exception as e:
                logger.debug(f"Stream stop error: {e}")
        
        logger.debug(f"Audio playback paused at {self._paused_time:.3f}s")
    
    def seek(self, time_seconds: float) -> None:
        time_seconds = max(0.0, time_seconds)
        
        was_playing = self._playing
        if was_playing:
            # Just set flag, don't call pause() to avoid recursion issues
            self._playing = False
            if self._stream:
                try:
                    self._stream.stop()
                except Exception:
                    pass
        
        self._seek_complete.clear()
        with self._seek_lock:
            self._seek_requested = time_seconds
        self._paused_time = time_seconds
        
        if not self._seek_complete.wait(timeout=3.0):
            logger.warning("Seek timeout - forcing completion")
            with self._seek_lock:
                self._seek_requested = None
            with self._samples_lock:
                self._samples_played = int(time_seconds * self.TARGET_SAMPLE_RATE)
        
        if was_playing:
            self._playing = True
            if self._stream:
                try:
                    self._stream.start()
                except Exception:
                    pass

    
    def current_time(self) -> float:
        if self._playing:
            with self._samples_lock:
                return self._samples_played / self.TARGET_SAMPLE_RATE
        else:
            return self._paused_time
    
    def set_track_volume(self, track_index: int, volume: float) -> None:
        if 0 <= track_index < len(self._tracks):
            self._tracks[track_index].volume = max(0.0, min(1.0, volume))
    
    def set_track_muted(self, track_index: int, muted: bool) -> None:
        if 0 <= track_index < len(self._tracks):
            self._tracks[track_index].muted = muted
    
    @property
    def num_tracks(self) -> int:
        return len(self._tracks)
    
    @property
    def sample_rate(self) -> int:
        return self.TARGET_SAMPLE_RATE
    
    @property
    def is_playing(self) -> bool:
        return self._playing
    
    @property
    def stats(self) -> dict:
        track_underruns = sum(t.buffer.underruns for t in self._tracks)
        return {
            "callback_underruns": self._callback_underruns,
            "track_buffer_underruns": track_underruns,
            "total_callbacks": self._total_callbacks,
            "underrun_rate": self._callback_underruns / max(1, self._total_callbacks)
        }
    
    def log_stats(self) -> None:
        stats = self.stats
        logger.info(
            f"Audio stats: underruns={stats['callback_underruns']}, "
            f"rate={stats['underrun_rate']:.2%}"
        )
    
    def close(self) -> None:
        self._playing = False
        self._decoder_running = False
        
        if self._decoder_thread:
            self._decoder_thread.join(timeout=1.0)
        
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        
        if self._container:
            self._container.close()
            self._container = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
