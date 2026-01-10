# GoodPlayer3

A frame-accurate video player with multi-track audio support, built with Python, PyAV, and PyQt6.

## Features

- **Frame-accurate playback**: Navigate to any frame with precision
- **Multi-track audio**: Support for up to 3 audio tracks with mixing
- **Frame stepping**: Step forward/backward one frame at a time
- **Audio-synced playback**: Audio is the master clock for A/V sync
- **LRU frame caching**: Fast backward stepping with intelligent caching
- **Background prefetching**: Smooth playback with ahead-of-time decoding

## Installation

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Mac/Linux
# On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## Usage

### Launch the player

```bash
python run.py [video_file]
```

### Keyboard controls

| Key | Action |
|-----|--------|
| `Space` | Play / Pause |
| `Left` | Step back one frame |
| `Right` | Step forward one frame |
| `Up` | Volume up |
| `Down` | Volume down |
| `[` | Skip back 5 seconds |
| `]` | Skip forward 10 seconds |
| `{` | Skip back 15 seconds |
| `}` | Skip forward 30 seconds |

## Key Design Decisions

1. **Audio is the master clock**: Video frames are requested based on audio time, ensuring perfect A/V sync.

2. **Backward stepping via keyframes**: Seeking backward requires decoding from the nearest keyframe, but the LRU cache makes repeated backward steps fast.

3. **Non-blocking audio callback**: The sounddevice callback must never block, so audio is pre-decoded into ring buffers.

4. **Prefetching**: A background thread prefetches frames ahead of the current position for smooth playback.

## Requirements

- Python 3.10+
- PyAV 10.0+
- NumPy 1.24+
- sounddevice 0.4+
- PyQt6 6.5+
