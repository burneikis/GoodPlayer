"""
Stage 1 & 5: Video Decoder Core (Frame-Accurate) with Performance Optimizations
Provides reliable random access to individual video frames.
"""

import av
import itertools
import logging
import threading
import queue
from collections import OrderedDict
from fractions import Fraction
from typing import Optional
import numpy as np
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtCore import Qt

logger = logging.getLogger(__name__)


class LRUCache:
    """Thread-safe LRU cache for decoded frames."""
    
    def __init__(self, max_size: int = 120):
        self.max_size = max_size
        self._cache: OrderedDict[int, np.ndarray] = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
    
    def get(self, key: int) -> Optional[np.ndarray]:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._hits += 1
                return self._cache[key]
            self._misses += 1
            return None
    
    def put(self, key: int, value: np.ndarray) -> None:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            else:
                if len(self._cache) >= self.max_size:
                    self._cache.popitem(last=False)
                self._cache[key] = value
    
    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0
    
    def __contains__(self, key: int) -> bool:
        with self._lock:
            return key in self._cache
    
    def resize(self, new_size: int) -> None:
        with self._lock:
            self.max_size = new_size
            while len(self._cache) > self.max_size:
                self._cache.popitem(last=False)
    
    @property
    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            hit_rate = self._hits / total if total > 0 else 0.0
            return {
                "size": len(self._cache),
                "max_size": self.max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": hit_rate
            }


class FrameRequest:
    """Request for a specific frame."""
    def __init__(self, frame_index: int, priority: int = 0):
        self.frame_index = frame_index
        self.priority = priority  # Lower = higher priority
        self.event = threading.Event()
        self.result: Optional[np.ndarray] = None


class VideoDecoder:
    """
    Frame-accurate video decoder with keyframe indexing, LRU caching,
    and background prefetching.
    """
    
    # Memory budget for cache (in bytes) - ~800MB default for high-fps content
    CACHE_MEMORY_BUDGET = 800 * 1024 * 1024
    PREFETCH_AHEAD = 90  # Frames to prefetch ahead (1.5s at 60fps)
    PREFETCH_BEHIND = 10  # Frames to keep behind
    DEFAULT_CACHE_SIZE = 120
    MIN_CACHE_SIZE = 60
    DEFAULT_FPS = 30.0
    
    def __init__(self, filepath: str, cache_seconds: float = 3.0):
        self.filepath = filepath
        self._container: Optional[av.container] = None
        self._stream: Optional[av.video.VideoStream] = None
        
        # Metadata
        self.fps: float = 0.0
        self.time_base: Fraction = Fraction(1, 1)
        self.total_frames: int = 0
        self.duration: float = 0.0
        self.width: int = 0
        self.height: int = 0
        
        # Keyframe index
        self._keyframe_index: list[tuple[int, int]] = []
        
        # Decoder state (protected by lock)
        self._decoder_lock = threading.Lock()
        self._current_frame_index: int = -1
        # Persistent decode iterator. Abandoning a PyAV decode() generator
        # closes it, which flushes the codec into EOF/draining state and
        # makes the next decode() call yield nothing. We therefore keep one
        # iterator alive and only recreate it after an explicit seek.
        self._frame_iter = None
        
        # Frame cache
        self._cache: Optional[LRUCache] = None
        self._cache_seconds = cache_seconds

        # Thumbnail cache (separate from frame cache)
        self._thumbnail_cache: dict[float, np.ndarray] = {}
        self._thumbnail_cache_lock = threading.Lock()
        self._thumbnail_cache_max_size = 100

        # Separate container for thumbnails (don't block playback)
        self._thumbnail_container: Optional[av.container.Container] = None
        self._thumbnail_stream: Optional[av.video.VideoStream] = None

        # Worker thread for decoding.
        # Queue entries are (priority, seq, request); the monotonically
        # increasing seq breaks priority ties so FrameRequest objects are
        # never compared (they have no ordering, which would raise TypeError).
        self._request_queue: queue.PriorityQueue = queue.PriorityQueue()
        self._request_seq = itertools.count()
        self._worker_thread: Optional[threading.Thread] = None
        self._worker_running = False
        
        # Prefetch thread
        self._prefetch_thread: Optional[threading.Thread] = None
        self._prefetch_running = False
        self._prefetch_target: int = 0
        self._prefetch_lock = threading.Lock()
        
        # Stats
        self._frames_decoded = 0
        self._frames_dropped = 0
        
        self._open(filepath)
        self._start_workers()
    
    def _open(self, filepath: str) -> None:
        """Open video file and extract metadata."""
        # Try to open with hardware acceleration
        try:
            self._container = av.open(filepath, options={'hwaccel': 'auto'})
            logger.info("Opened video with hardware acceleration")
        except Exception:
            # Fallback to software decoding
            self._container = av.open(filepath)
            logger.info("Opened video with software decoding")
        
        self._stream = self._container.streams.video[0]
        
        # Enable multi-threaded decoding
        self._stream.thread_type = "AUTO"
        
        # Extract metadata
        self.time_base = self._stream.time_base
        self.width = self._stream.width
        self.height = self._stream.height
        
        if self._stream.average_rate:
            self.fps = float(self._stream.average_rate)
        elif self._stream.guessed_rate:
            self.fps = float(self._stream.guessed_rate)
        else:
            self.fps = 30.0
        
        # Estimate total frames
        if self._stream.frames > 0:
            self.total_frames = self._stream.frames
        elif self._stream.duration and self.time_base:
            duration_seconds = float(self._stream.duration * self.time_base)
            self.total_frames = int(duration_seconds * self.fps)
        elif self._container.duration:
            duration_seconds = self._container.duration / av.time_base
            self.total_frames = int(duration_seconds * self.fps)
        else:
            self.total_frames = 0
        
        # Calculate duration
        if self._container.duration:
            self.duration = self._container.duration / av.time_base
        else:
            self.duration = self.total_frames / self.fps if self.fps > 0 else 0.0
        
        # Calculate adaptive cache size based on frame size and memory budget
        frame_size_bytes = self.width * self.height * 3  # RGB24
        if frame_size_bytes > 0:
            max_frames_by_memory = self.CACHE_MEMORY_BUDGET // frame_size_bytes
            cache_by_time = int(self._cache_seconds * 2 * self.fps)
            cache_size = min(max_frames_by_memory, max(cache_by_time, self.MIN_CACHE_SIZE))
        else:
            cache_size = self.DEFAULT_CACHE_SIZE
        
        self._cache = LRUCache(max_size=cache_size)
        logger.info(f"Cache size: {cache_size} frames ({self.width}x{self.height})")
        
        # Build keyframe index
        self._build_keyframe_index()
    
    def _build_keyframe_index(self) -> None:
        """Build index of keyframes for efficient seeking."""
        self._keyframe_index = []
        frame_index = 0
        
        self._container.seek(0)
        
        for packet in self._container.demux(self._stream):
            if packet.is_keyframe and packet.pts is not None:
                self._keyframe_index.append((frame_index, packet.pts))
            if packet.pts is not None:
                frame_index += 1
        
        if not self._keyframe_index:
            self._container.seek(0)
            frame_index = 0
            for frame in self._container.decode(video=0):
                if frame.key_frame:
                    self._keyframe_index.append((frame_index, frame.pts))
                frame_index += 1
            if frame_index > 0:
                self.total_frames = frame_index
        
        self._container.seek(0)
        self._current_frame_index = -1
        self._frame_iter = None
        logger.info(f"Indexed {len(self._keyframe_index)} keyframes")
    
    def _start_workers(self) -> None:
        """Start background worker threads."""
        self._worker_running = True
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        
        self._prefetch_running = True
        self._prefetch_thread = threading.Thread(target=self._prefetch_loop, daemon=True)
        self._prefetch_thread.start()
    
    def _worker_loop(self) -> None:
        """Background worker that processes frame requests."""
        while self._worker_running:
            try:
                _priority, _seq, request = self._request_queue.get(timeout=0.1)
                if request is None:
                    continue
                
                frame = self._decode_frame_internal(request.frame_index)
                request.result = frame
                request.event.set()
                
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Worker error: {e}")
    
    def _prefetch_loop(self) -> None:
        """Background prefetching of frames around current position."""
        while self._prefetch_running:
            try:
                with self._prefetch_lock:
                    target = self._prefetch_target
                
                # Prefetch frames ahead of target
                frames_prefetched = 0
                for offset in range(1, self.PREFETCH_AHEAD + 1):
                    if not self._prefetch_running:
                        break
                    
                    frame_idx = target + offset
                    if 0 <= frame_idx < self.total_frames and frame_idx not in self._cache:
                        self._decode_frame_internal(frame_idx)
                        frames_prefetched += 1
                    
                    # Check if target changed significantly - allow more drift before restarting
                    with self._prefetch_lock:
                        if abs(self._prefetch_target - target) > 15:
                            break
                
                # Sleep before next prefetch cycle - shorter sleep if we're actively prefetching
                sleep_time = 0.005 if frames_prefetched > 0 else 0.016
                threading.Event().wait(sleep_time)
                
            except Exception as e:
                logger.debug(f"Prefetch error: {e}")
    
    def _find_nearest_keyframe(self, frame_index: int) -> tuple[int, int]:
        """Find the nearest keyframe at or before the given frame index."""
        if not self._keyframe_index:
            return (0, 0)
        
        left, right = 0, len(self._keyframe_index) - 1
        result = self._keyframe_index[0]
        
        while left <= right:
            mid = (left + right) // 2
            kf_index, kf_pts = self._keyframe_index[mid]
            
            if kf_index <= frame_index:
                result = self._keyframe_index[mid]
                left = mid + 1
            else:
                right = mid - 1
        
        return result
    
    def _decode_frame_internal(self, frame_index: int) -> Optional[np.ndarray]:
        """Internal method to decode a specific frame (thread-safe)."""
        frame_index = max(0, min(frame_index, self.total_frames - 1))
        
        # Check cache first
        cached = self._cache.get(frame_index)
        if cached is not None:
            return cached
        
        with self._decoder_lock:
            # Double-check cache after acquiring lock
            cached = self._cache.get(frame_index)
            if cached is not None:
                return cached
            
            # Determine if we need to seek
            need_seek = (
                self._frame_iter is None
                or frame_index <= self._current_frame_index
                or frame_index > self._current_frame_index + 60
            )
            
            if need_seek:
                self._seek_locked(frame_index)
            
            # Decode forward to target
            result = None
            try:
                while True:
                    rgb_frame = self._decode_one_locked()
                    
                    if self._current_frame_index == frame_index:
                        result = rgb_frame
                        break
                    elif self._current_frame_index > frame_index:
                        # Overshot - return what we have in cache
                        result = self._cache.get(frame_index)
                        break
                        
            except (av.EOFError, StopIteration):
                pass
            
            return result
    
    def _seek_locked(self, frame_index: int) -> None:
        """Seek to the keyframe before frame_index. Caller holds _decoder_lock."""
        kf_frame_index, kf_pts = self._find_nearest_keyframe(frame_index)
        self._container.seek(kf_pts, stream=self._stream)
        self._current_frame_index = kf_frame_index - 1
        self._frame_iter = self._container.decode(video=0)
    
    def _decode_one_locked(self) -> np.ndarray:
        """Decode the next frame and cache it. Caller holds _decoder_lock.
        
        Raises StopIteration/av.EOFError at end of stream.
        """
        if self._frame_iter is None:
            self._frame_iter = self._container.decode(video=0)
        frame = next(self._frame_iter)
        self._current_frame_index += 1
        self._frames_decoded += 1
        rgb_frame = frame.to_ndarray(format='rgb24')
        self._cache.put(self._current_frame_index, rgb_frame)
        return rgb_frame
    
    def get_frame(self, frame_index: int) -> Optional[np.ndarray]:
        """
        Get a specific frame by index.
        Uses cache if available, otherwise decodes.
        """
        frame_index = max(0, min(frame_index, self.total_frames - 1))
        
        # Update prefetch target
        with self._prefetch_lock:
            self._prefetch_target = frame_index
        
        # Check cache first (fast path)
        cached = self._cache.get(frame_index)
        if cached is not None:
            return cached
        
        # Decode the frame
        return self._decode_frame_internal(frame_index)
    
    def get_frame_async(self, frame_index: int) -> FrameRequest:
        """
        Request a frame asynchronously.
        Returns a FrameRequest that can be waited on.
        """
        frame_index = max(0, min(frame_index, self.total_frames - 1))
        
        # Update prefetch target
        with self._prefetch_lock:
            self._prefetch_target = frame_index
        
        # Check cache first
        cached = self._cache.get(frame_index)
        if cached is not None:
            request = FrameRequest(frame_index)
            request.result = cached
            request.event.set()
            return request
        
        # Queue the request
        request = FrameRequest(frame_index, priority=0)
        self._request_queue.put((0, next(self._request_seq), request))
        return request

    def get_thumbnail_at_position(self, target_time: float) -> Optional[np.ndarray]:
        """
        Get a 320x180 thumbnail at the specified time.

        Uses separate container and keyframe seeking for fast extraction
        without blocking playback. Results cached with 0.2s granularity.

        Args:
            target_time: Target time in seconds

        Returns:
            320x180 RGB numpy array, or None on failure
        """
        if not self._container:
            return None

        target_time = max(0.0, min(target_time, self.duration))

        # Round to 0.2s for cache key (better hit rate during scrubbing)
        cache_key = round(target_time * 5) / 5

        # Check cache first
        with self._thumbnail_cache_lock:
            if cache_key in self._thumbnail_cache:
                return self._thumbnail_cache[cache_key]

        # Cache miss - extract thumbnail
        try:
            # Ensure thumbnail container is open
            self._ensure_thumbnail_container()

            if not self._thumbnail_stream:
                return None

            # Seek to keyframe at or before target
            time_base = self._thumbnail_stream.time_base
            if not time_base:
                return None
            target_pts = int(target_time / float(time_base))
            self._thumbnail_container.seek(
                target_pts,
                stream=self._thumbnail_stream,
                backward=True,
                any_frame=False  # Only keyframes
            )

            # Decode first frame (will be keyframe)
            for frame in self._thumbnail_container.decode(video=0):
                rgb_frame = frame.to_ndarray(format='rgb24')

                # Resize using Qt (maintains quality, consistent with UI)
                height, width, channels = rgb_frame.shape
                bytes_per_line = channels * width

                qimage = QImage(
                    rgb_frame.data, width, height, bytes_per_line,
                    QImage.Format.Format_RGB888
                ).copy()

                pixmap = QPixmap.fromImage(qimage).scaled(
                    320, 180,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.FastTransformation
                )

                # Convert back to numpy
                scaled_image = pixmap.toImage().convertToFormat(
                    QImage.Format.Format_RGB888
                )
                w = scaled_image.width()
                h = scaled_image.height()
                ptr = scaled_image.bits()
                ptr.setsize(h * w * 3)
                thumbnail = np.frombuffer(ptr, np.uint8).reshape((h, w, 3)).copy()

                # Store in cache with LRU eviction
                with self._thumbnail_cache_lock:
                    self._thumbnail_cache[cache_key] = thumbnail

                    # LRU eviction
                    if len(self._thumbnail_cache) > self._thumbnail_cache_max_size:
                        oldest_key = next(iter(self._thumbnail_cache))
                        del self._thumbnail_cache[oldest_key]

                return thumbnail

            return None

        except Exception as e:
            logger.error(f"Error extracting thumbnail at {target_time}s: {e}")
            return None

    def _ensure_thumbnail_container(self):
        """Ensure thumbnail container is open."""
        if self._thumbnail_container is None:
            self._thumbnail_container = av.open(self.filepath)
            self._thumbnail_stream = self._thumbnail_container.streams.video[0]

    def seek_to_frame(self, frame_index: int) -> None:
        """Seek to a specific frame index."""
        frame_index = max(0, min(frame_index, self.total_frames - 1))
        
        with self._decoder_lock:
            self._seek_locked(frame_index)
            
            # Decode forward to just before target
            try:
                while self._current_frame_index < frame_index - 1:
                    self._decode_one_locked()
            except (av.EOFError, StopIteration):
                pass
        
        # Update prefetch target
        with self._prefetch_lock:
            self._prefetch_target = frame_index
    
    def decode_next_frame(self) -> Optional[np.ndarray]:
        """Decode and return the next frame as a numpy array (RGB)."""
        with self._decoder_lock:
            try:
                return self._decode_one_locked()
            except (av.EOFError, StopIteration):
                return None
    
    def frame_to_time(self, frame_index: int) -> float:
        """Convert frame index to time in seconds."""
        if self.fps > 0:
            return frame_index / self.fps
        return 0.0
    
    def time_to_frame(self, time_seconds: float) -> int:
        """Convert time in seconds to frame index."""
        if self.fps > 0:
            return int(time_seconds * self.fps)
        return 0
    
    @property
    def frame_duration(self) -> float:
        """Duration of a single frame in seconds."""
        return 1.0 / self.fps if self.fps > 0 else 0.0
    
    @property
    def stats(self) -> dict:
        """Get decoder statistics."""
        return {
            "frames_decoded": self._frames_decoded,
            "frames_dropped": self._frames_dropped,
            "cache": self._cache.stats if self._cache else {}
        }
    
    def log_stats(self) -> None:
        """Log current statistics."""
        stats = self.stats
        cache = stats.get("cache", {})
        logger.info(
            f"Decoder stats: decoded={stats['frames_decoded']}, "
            f"cache_size={cache.get('size', 0)}/{cache.get('max_size', 0)}, "
            f"hit_rate={cache.get('hit_rate', 0):.1%}"
        )
    
    def close(self) -> None:
        """Close the video file and release resources."""
        self._worker_running = False
        self._prefetch_running = False

        if self._worker_thread:
            self._worker_thread.join(timeout=1.0)
        if self._prefetch_thread:
            self._prefetch_thread.join(timeout=1.0)

        # Close thumbnail container
        if self._thumbnail_container:
            try:
                self._thumbnail_container.close()
            except Exception:
                pass
            self._thumbnail_container = None
            self._thumbnail_stream = None

        # Clear thumbnail cache
        with self._thumbnail_cache_lock:
            self._thumbnail_cache.clear()

        if self._cache:
            self._cache.clear()
        if self._container:
            self._container.close()
            self._container = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
