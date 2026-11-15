use std::sync::Arc;

use anyhow::{Context as _, Result};

use super::ffmpeg;
use super::frame::{FrameConverter, VideoFrame};
use super::probe::MediaInfo;

/// A sequential video decoder over its own demuxer. It only knows how to
/// seek (to the keyframe at or before a time) and to produce the next frame;
/// all frame-accuracy logic lives in the engine on top of it.
pub struct VideoDecoder {
    input: ffmpeg::format::context::Input,
    decoder: ffmpeg::decoder::Video,
    stream: usize,
    time_base: f64,
    start: f64,
    frame_dur: f64,
    converter: FrameConverter,
    raw: ffmpeg::frame::Video,
    drained: bool,
    last_time: Option<f64>,
}

impl VideoDecoder {
    pub fn open(info: &MediaInfo) -> Result<Self> {
        let mut input = ffmpeg::format::input(&info.path)?;
        super::discard_other_streams(&mut input, &[info.video_index]);
        let stream = input
            .stream(info.video_index)
            .context("video stream vanished")?;
        let mut ctx = ffmpeg::codec::context::Context::from_parameters(stream.parameters())?;
        super::enable_threads(&mut ctx, 0);
        let decoder = ctx.decoder().video()?;
        Ok(Self {
            input,
            decoder,
            stream: info.video_index,
            time_base: info.time_base,
            start: info.start,
            frame_dur: info.frame_duration(),
            converter: FrameConverter::default(),
            raw: ffmpeg::frame::Video::empty(),
            drained: false,
            last_time: None,
        })
    }

    /// Position before the keyframe at or before media time `time`.
    pub fn seek(&mut self, time: f64) {
        if let Err(e) = super::seek_input(&mut self.input, time + self.start) {
            log::warn!("video seek to {time:.3}s failed: {e}");
        }
        self.decoder.flush();
        self.drained = false;
        self.last_time = None;
    }

    /// The next frame in presentation order, or `None` at the end.
    pub fn next_frame(&mut self) -> Option<Arc<VideoFrame>> {
        loop {
            match self.decoder.receive_frame(&mut self.raw) {
                Ok(()) => {
                    let time = self.frame_time();
                    match self.converter.convert(&self.raw, time) {
                        Ok(frame) => return Some(Arc::new(frame)),
                        Err(e) => log::warn!("frame conversion failed: {e}"),
                    }
                }
                Err(ffmpeg::Error::Eof) => return None,
                Err(e) if super::is_again(&e) => {
                    if !self.feed() {
                        return None;
                    }
                }
                Err(e) => {
                    log::warn!("video decode error: {e}");
                    if !self.feed() {
                        return None;
                    }
                }
            }
        }
    }

    /// Send one packet (or end-of-stream) to the decoder. False when there
    /// is nothing left to send.
    fn feed(&mut self) -> bool {
        if self.drained {
            return false;
        }
        loop {
            match self.input.packets().next() {
                Some((stream, packet)) if stream.index() == self.stream => {
                    if let Err(e) = self.decoder.send_packet(&packet) {
                        log::debug!("video packet rejected: {e}");
                    }
                    return true;
                }
                Some(_) => {}
                None => {
                    let _ = self.decoder.send_eof();
                    self.drained = true;
                    return true;
                }
            }
        }
    }

    fn frame_time(&mut self) -> f64 {
        let time = self
            .raw
            .timestamp()
            .or(self.raw.pts())
            .map(|ts| ts as f64 * self.time_base - self.start)
            .or_else(|| self.last_time.map(|t| t + self.frame_dur))
            .unwrap_or(0.0);
        self.last_time = Some(time);
        time
    }
}
