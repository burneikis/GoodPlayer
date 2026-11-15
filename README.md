# GoodPlayer

A video player for going through footage: frame-exact stepping in both
directions, and every audio track of a file played at once through a
per-track mixer (game, mic and Discord tracks of an OBS recording, say).

**DoesPlayer** is built from the same code as a simpler edition: same
engine and mixer, without thumbnails or the frame counter.

## Features

- Frame-exact stepping forwards and backwards, instant within the cached
  window around the playhead
- All audio tracks mixed simultaneously, each with its own volume and mute
- Audio is the master clock: picture and sound stay in sync, and tracks never
  drift apart
- GPU rendering, low CPU use, multithreaded decoding
- Timeline with hover thumbnails and a thumbnail strip (GoodPlayer)
- Drag and drop, fullscreen, auto-hiding cursor, remembered window size and
  volume

## Keys

| Key | Action |
|---|---|
| `Space` | Play / pause |
| `Left` / `Right` (or `,` / `.`) | Step one frame back / forward |
| `[` / `]` | Skip back 5 s / forward 10 s |
| `{` / `}` | Skip back 15 s / forward 30 s |
| `Home` / `End` | Start / end |
| `Up` / `Down` | Volume |
| `M` | Mute |
| `A` | Audio mixer |
| `T` | Thumbnail strip (GoodPlayer) |
| `F`, `F11`, double-click | Fullscreen (`Esc` leaves) |
| `Ctrl+O` | Open a file |

Clicking or dragging the timeline seeks; holding a step key steps
continuously.

## Install

Download an archive from [Releases](https://github.com/burneikis/goodplayer/releases).

- **Linux**: unpack and run `./install.sh` (installs to `~/.local`; FFmpeg is
  bundled). `./install.sh --uninstall` removes it.
- **Windows**: unzip and run `goodplayer.exe` or `doesplayer.exe`.
- **macOS** (Apple Silicon): unzip and move the app to Applications. The
  apps are not notarised, so the first launch needs right-click, Open (or
  `xattr -cr GoodPlayer.app`). Open files with drag and drop or Cmd+O.

## Build from source

Needs Rust (stable) and FFmpeg 6 or newer with development headers, plus
`clang` for the bindings and ALSA headers on Linux.

```sh
# Arch: pacman -S rust ffmpeg clang alsa-lib
cargo build --release
./target/release/goodplayer video.mkv
./target/release/doesplayer video.mkv
```

To link against an FFmpeg that is not installed system-wide, point
`FFMPEG_DIR` at it (a directory with `include/` and `lib/`).

`cargo test --release` runs the engine tests against generated media (needs
the `ffmpeg` CLI). `cargo run --release --example bench -- FILE` measures
seeks, steps and playback on a real file.

## How it works

```
src/
  media/    FFmpeg: probing, video decoding to YUV 4:2:0, audio decoding,
            keyframe thumbnails
  engine/   audio output and clock, frame cache, video thread, transport
  ui/       egui window, GPU YUV renderer, timeline
  profile   GoodPlayer and DoesPlayer editions
```

- One audio thread demuxes the file once and decodes every track, resampled
  to the device rate and positioned by timestamp. The device callback mixes
  the tracks; samples consumed by the device are the clock.
- The video thread owns its decoder and a cache of decoded frames, each
  linked to the frame decoded after it. Stepping and exact seeks are answered
  from the cache where possible; a step back past it decodes the run from the
  previous keyframe, keeping all of it. During playback it skips frames the
  clock has already passed.
- The UI never waits on decoding: commands go to the threads and results come
  back tagged, so a stale result is simply ignored.

## Licence

MIT. Release builds bundle LGPL builds of FFmpeg.
