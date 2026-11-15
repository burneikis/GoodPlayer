use std::path::{Path, PathBuf};

use anyhow::{Context as _, Result, anyhow};

use super::ffmpeg;

/// Static facts about an opened file.
#[derive(Debug, Clone)]
pub struct MediaInfo {
    pub path: PathBuf,
    pub video_index: usize,
    pub width: u32,
    pub height: u32,
    /// Seconds per video timestamp tick.
    pub time_base: f64,
    /// First video timestamp, in seconds. Media time zero.
    pub start: f64,
    /// Nominal frame rate (average), used for frame numbers and step sizes.
    pub fps: f64,
    pub duration: f64,
    pub audio: Vec<AudioTrackInfo>,
}

#[derive(Debug, Clone)]
pub struct AudioTrackInfo {
    pub index: usize,
    pub label: String,
}

/// Hard cap on simultaneously mixed tracks; more than this is a broken file.
pub const MAX_AUDIO_TRACKS: usize = 16;

impl MediaInfo {
    pub fn probe(path: &Path) -> Result<Self> {
        let input = ffmpeg::format::input(path)
            .with_context(|| format!("cannot open {}", path.display()))?;
        let video = input
            .streams()
            .best(ffmpeg::media::Type::Video)
            .ok_or_else(|| anyhow!("no video stream in {}", path.display()))?;
        let params = ffmpeg::codec::context::Context::from_parameters(video.parameters())?;
        let decoder = params
            .decoder()
            .video()
            .context("unsupported video codec")?;

        let time_base = f64::from(video.time_base());
        let start = if video.start_time() == ffmpeg::ffi::AV_NOPTS_VALUE {
            0.0
        } else {
            video.start_time() as f64 * time_base
        };
        let fps = [video.avg_frame_rate(), video.rate()]
            .into_iter()
            .map(f64::from)
            .find(|r| r.is_finite() && *r > 1.0 && *r < 1000.0)
            .unwrap_or(30.0);
        let duration = if video.duration() > 0 {
            video.duration() as f64 * time_base
        } else if input.duration() > 0 {
            input.duration() as f64 / f64::from(ffmpeg::ffi::AV_TIME_BASE) - start
        } else {
            0.0
        };

        let audio = input
            .streams()
            .filter(|s| s.parameters().medium() == ffmpeg::media::Type::Audio)
            .take(MAX_AUDIO_TRACKS)
            .enumerate()
            .map(|(n, s)| {
                let meta = s.metadata();
                let label = meta
                    .get("title")
                    .or_else(|| meta.get("language").filter(|l| *l != "und"))
                    .map_or_else(|| format!("Track {}", n + 1), str::to_string);
                AudioTrackInfo {
                    index: s.index(),
                    label,
                }
            })
            .collect();

        Ok(Self {
            path: path.to_path_buf(),
            video_index: video.index(),
            width: decoder.width(),
            height: decoder.height(),
            time_base,
            start,
            fps,
            duration: duration.max(0.0),
            audio,
        })
    }

    pub fn frame_duration(&self) -> f64 {
        1.0 / self.fps
    }

    pub fn total_frames(&self) -> u64 {
        (self.duration * self.fps).round().max(1.0) as u64
    }

    /// Zero-based frame number for a frame's media time. Rounded, because
    /// timestamps in coarse time bases (e.g. mkv's milliseconds) land just
    /// either side of the exact frame boundary.
    pub fn frame_number(&self, time: f64) -> u64 {
        (time * self.fps).round().max(0.0) as u64
    }

    pub fn file_name(&self) -> String {
        self.path.file_name().map_or_else(
            || self.path.display().to_string(),
            |n| n.to_string_lossy().into_owned(),
        )
    }
}
