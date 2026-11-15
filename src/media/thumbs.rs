//! Timeline thumbnails, decoded from keyframes on a background thread.

use std::thread::JoinHandle;

use anyhow::Result;
use crossbeam_channel::{Receiver, Sender, unbounded};

use super::ffmpeg::{self, format::Pixel, software::scaling};
use super::probe::MediaInfo;

/// Thumbnail width in pixels; height follows the video's aspect.
pub const THUMB_WIDTH: u32 = 256;

pub struct Thumbnail {
    pub slot: u32,
    pub width: u32,
    pub height: u32,
    pub rgba: Vec<u8>,
}

/// Requests are by slot: the timeline is divided into `slots` equal parts and
/// each slot has one thumbnail, taken from the keyframe at its start.
pub struct Thumbnailer {
    requests: Option<Sender<u32>>,
    results: Receiver<Thumbnail>,
    thread: Option<JoinHandle<()>>,
    pub slots: u32,
}

impl Thumbnailer {
    pub fn spawn(info: &MediaInfo, slots: u32, wake: impl Fn() + Send + 'static) -> Result<Self> {
        let mut worker = Worker::open(info, slots)?;
        let (req_tx, req_rx) = unbounded::<u32>();
        let (res_tx, results) = unbounded();
        let thread = std::thread::Builder::new()
            .name("thumbnails".into())
            .spawn(move || {
                // A stack, newest request first: the pointer has usually moved
                // on, and what it is over now matters most.
                let mut stack: Vec<u32> = Vec::new();
                while let Ok(first) = req_rx.recv() {
                    stack.push(first);
                    while let Some(slot) = stack.pop() {
                        if let Some(thumb) = worker.render(slot) {
                            if res_tx.send(thumb).is_err() {
                                return;
                            }
                            wake();
                        }
                        for s in req_rx.try_iter() {
                            stack.retain(|x| *x != s);
                            stack.push(s);
                        }
                    }
                }
            })?;
        Ok(Self {
            requests: Some(req_tx),
            results,
            thread: Some(thread),
            slots,
        })
    }

    pub fn request(&self, slot: u32) {
        if let Some(tx) = &self.requests {
            let _ = tx.send(slot.min(self.slots - 1));
        }
    }

    pub fn results(&self) -> impl Iterator<Item = Thumbnail> + '_ {
        self.results.try_iter()
    }
}

impl Drop for Thumbnailer {
    fn drop(&mut self) {
        self.requests = None;
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}

struct Worker {
    input: ffmpeg::format::context::Input,
    decoder: ffmpeg::decoder::Video,
    stream: usize,
    start: f64,
    duration: f64,
    slots: u32,
    scaler: Option<((Pixel, u32, u32), scaling::Context, u32, u32)>,
}

// SAFETY: as for `FrameConverter`, the scaler is owned and used by one thread.
unsafe impl Send for Worker {}

impl Worker {
    fn open(info: &MediaInfo, slots: u32) -> Result<Self> {
        let mut input = ffmpeg::format::input(&info.path)?;
        super::discard_other_streams(&mut input, &[info.video_index]);
        let stream = input
            .stream(info.video_index)
            .ok_or_else(|| anyhow::anyhow!("video stream vanished"))?;
        let mut ctx = ffmpeg::codec::context::Context::from_parameters(stream.parameters())?;
        ctx.set_threading(ffmpeg::codec::threading::Config {
            kind: ffmpeg::codec::threading::Type::Slice,
            count: 2,
        });
        let mut decoder = ctx.decoder().video()?;
        // Only keyframes are ever wanted here.
        // SAFETY: setting a plain field on an owned, opened codec context.
        unsafe {
            (*decoder.as_mut_ptr()).skip_frame = ffmpeg::ffi::AVDiscard::AVDISCARD_NONKEY;
        }
        Ok(Self {
            input,
            decoder,
            stream: info.video_index,
            start: info.start,
            duration: info.duration,
            slots: slots.max(1),
            scaler: None,
        })
    }

    fn render(&mut self, slot: u32) -> Option<Thumbnail> {
        let time = (f64::from(slot) + 0.5) / f64::from(self.slots) * self.duration;
        super::seek_input(&mut self.input, time + self.start).ok()?;
        self.decoder.flush();
        let mut raw = ffmpeg::frame::Video::empty();
        let mut fed = 0;
        loop {
            match self.decoder.receive_frame(&mut raw) {
                Ok(()) => break,
                Err(_) if fed < 600 => {
                    let packet = loop {
                        match self.input.packets().next() {
                            Some((s, p)) if s.index() == self.stream => break Some(p),
                            Some(_) => {}
                            None => break None,
                        }
                    };
                    match packet {
                        Some(p) => {
                            let _ = self.decoder.send_packet(&p);
                        }
                        None => {
                            let _ = self.decoder.send_eof();
                        }
                    }
                    fed += 1;
                }
                Err(_) => return None,
            }
        }
        self.scale(&raw, slot)
    }

    fn scale(&mut self, raw: &ffmpeg::frame::Video, slot: u32) -> Option<Thumbnail> {
        let key = (raw.format(), raw.width(), raw.height());
        if self.scaler.as_ref().is_none_or(|(k, ..)| *k != key) {
            let w = THUMB_WIDTH;
            let h =
                ((u64::from(w) * u64::from(key.2) / u64::from(key.1.max(1))) as u32).max(2) & !1;
            let ctx = scaling::Context::get(
                key.0,
                key.1,
                key.2,
                Pixel::RGBA,
                w,
                h,
                scaling::Flags::BILINEAR,
            )
            .ok()?;
            self.scaler = Some((key, ctx, w, h));
        }
        let (_, ctx, w, h) = self.scaler.as_mut()?;
        let mut out = ffmpeg::frame::Video::empty();
        ctx.run(raw, &mut out).ok()?;
        let (w, h) = (*w, *h);
        let stride = out.stride(0);
        let row = w as usize * 4;
        let mut rgba = Vec::with_capacity(row * h as usize);
        for line in out.data(0).chunks(stride).take(h as usize) {
            rgba.extend_from_slice(&line[..row]);
        }
        Some(Thumbnail {
            slot,
            width: w,
            height: h,
            rgba,
        })
    }
}
