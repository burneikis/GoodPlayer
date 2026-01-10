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
| `A` | Toggle audio mixer panel |
| `Ctrl/Cmd+O` | Open file |

## Building Standalone Executables

### Prerequisites

```bash
pip install pyinstaller
```

### Quick Build

```bash
# Build executable
python build.py

# Build with installer (DMG on macOS, NSIS on Windows)
python build.py --installer

# Clean and rebuild
python build.py --clean
```

### macOS

```bash
# Build .app bundle
python build.py

# Output: dist/GoodPlayer.app

# Create DMG installer (requires create-dmg: brew install create-dmg)
python build.py --installer
```

To create a properly signed app for distribution:
```bash
# Sign the app (requires Apple Developer certificate)
codesign --deep --force --verify --verbose \
    --sign "Developer ID Application: Your Name" \
    dist/GoodPlayer.app

# Notarize for Gatekeeper (requires App Store Connect API key)
xcrun notarytool submit dist/GoodPlayer-1.0.0.dmg \
    --apple-id your@email.com \
    --team-id YOURTEAMID \
    --password @keychain:AC_PASSWORD
```

### Windows

```bash
# Build executable
python build.py

# Output: dist/GoodPlayer/GoodPlayer.exe

# Create installer (requires NSIS: https://nsis.sourceforge.io/)
python build.py --installer

# Output: dist/GoodPlayer-Setup.exe
```

### Manual PyInstaller Build

If you prefer to run PyInstaller directly:

```bash
# Using spec file (recommended)
pyinstaller --clean GoodPlayer.spec

# Or simple one-liner
pyinstaller --name GoodPlayer --windowed --onedir run.py
```

## Build Outputs

| Platform | Output | Description |
|----------|--------|-------------|
| macOS | `dist/GoodPlayer.app` | Application bundle |
| macOS | `dist/GoodPlayer-1.0.0.dmg` | DMG installer |
| Windows | `dist/GoodPlayer/GoodPlayer.exe` | Portable executable |
| Windows | `dist/GoodPlayer-Setup.exe` | NSIS installer |

## Troubleshooting Builds

### Missing FFmpeg/libav libraries
PyAV bundles FFmpeg libraries, but if you encounter issues:
```bash
# macOS
brew install ffmpeg

# Windows - download from https://ffmpeg.org/download.html
```

### Audio issues on Windows
Ensure PortAudio is available. sounddevice usually bundles it, but if not:
```bash
pip install sounddevice --force-reinstall
```

### PyQt6 import errors
```bash
pip install PyQt6 --force-reinstall
```
