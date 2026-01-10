# GoodPlayer

Video player but you can go frame by frame backwards and forwards and it can play multi track audio simultaneously.

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
