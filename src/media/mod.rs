//! Everything that touches FFmpeg: probing, video/audio decoding, thumbnails.
//!
//! Time convention used across the app: "media time" is seconds since the
//! start of the video stream. Audio tracks are positioned on the same axis.

pub mod audio;
pub mod frame;
pub mod probe;
pub mod thumbs;
pub mod video;

pub use ffmpeg_next as ffmpeg;
pub use frame::VideoFrame;
pub use probe::{AudioTrackInfo, MediaInfo};

/// One-time FFmpeg setup.
pub fn init() {
    if let Err(e) = ffmpeg::init() {
        log::error!("ffmpeg init failed: {e}");
    }
    ffmpeg::util::log::set_level(ffmpeg::util::log::Level::Error);
}

/// Seconds to `AV_TIME_BASE` microseconds, as used by container seeks.
pub(crate) fn secs_to_av(secs: f64) -> i64 {
    (secs * f64::from(ffmpeg::ffi::AV_TIME_BASE)).round() as i64
}

/// Keyframe-backward seek to `secs` (absolute container time).
pub(crate) fn seek_input(
    input: &mut ffmpeg::format::context::Input,
    secs: f64,
) -> Result<(), ffmpeg::Error> {
    let ts = secs_to_av(secs.max(0.0));
    input.seek(ts, ..ts)
}

/// True for the "try again" error a decoder returns when it needs more input.
pub(crate) fn is_again(e: &ffmpeg::Error) -> bool {
    matches!(e, ffmpeg::Error::Other { errno } if *errno == ffmpeg::util::error::EAGAIN)
}

/// Tell the demuxer to skip every stream except `keep`.
pub(crate) fn discard_other_streams(input: &mut ffmpeg::format::context::Input, keep: &[usize]) {
    for i in 0..input.nb_streams() as usize {
        if !keep.contains(&i) {
            // SAFETY: index is in range and the stream pointer is owned by `input`.
            unsafe {
                let stream = *(*input.as_mut_ptr()).streams.add(i);
                (*stream).discard = ffmpeg::ffi::AVDiscard::AVDISCARD_ALL;
            }
        }
    }
}

/// Configure a decoder context for multithreaded decoding.
pub(crate) fn enable_threads(ctx: &mut ffmpeg::codec::context::Context, count: usize) {
    ctx.set_threading(ffmpeg::codec::threading::Config {
        kind: ffmpeg::codec::threading::Type::Frame,
        count,
    });
}
