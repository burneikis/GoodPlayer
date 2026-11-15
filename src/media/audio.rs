//! Decodes every audio track of a file on one thread and feeds the mixer.
//!
//! One demuxer serves all tracks, so the file is read once. Each track is
//! resampled to the device rate as interleaved stereo and positioned by its
//! timestamps: samples before the seek point are trimmed and gaps are filled
//! with silence, so all tracks start exactly at the requested time.

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread::JoinHandle;
use std::time::Duration;

use anyhow::Result;
use crossbeam_channel::{Receiver, Sender, TryRecvError, unbounded};
use rtrb::Producer;

use super::ffmpeg::{self, software::resampling};
use super::probe::MediaInfo;
use crate::engine::output::{CHANNELS, FlushHandle};

/// Once every track has this much queued, playback may start.
const PRIME_SECONDS: f64 = 0.15;
/// Timestamp disagreements smaller than this are treated as jitter.
const SLACK_SECONDS: f64 = 0.04;

enum Cmd {
    Seek { time: f64, flush: u64 },
    Stop,
}

/// Handle to the audio decoding thread.
pub struct AudioWorker {
    cmd: Sender<Cmd>,
    primed: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl AudioWorker {
    pub fn spawn(
        info: &MediaInfo,
        producers: Vec<Producer<f32>>,
        flush: FlushHandle,
    ) -> Result<Self> {
        let mut decoder = Decoder::open(info, producers, flush.rate())?;
        let (cmd, rx) = unbounded();
        let primed = Arc::new(AtomicBool::new(false));
        let flag = Arc::clone(&primed);
        let thread = std::thread::Builder::new()
            .name("audio-decode".into())
            .spawn(move || decoder.run(&rx, &flush, &flag))?;
        Ok(Self {
            cmd,
            primed,
            thread: Some(thread),
        })
    }

    /// Restart decoding at media time `time`, after mixer flush `flush`.
    pub fn seek(&self, time: f64, flush: u64) {
        self.primed.store(false, Ordering::Release);
        let _ = self.cmd.send(Cmd::Seek { time, flush });
    }

    pub fn primed(&self) -> bool {
        self.primed.load(Ordering::Acquire)
    }
}

impl Drop for AudioWorker {
    fn drop(&mut self) {
        let _ = self.cmd.send(Cmd::Stop);
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}

struct Track {
    stream: usize,
    time_base: f64,
    decoder: ffmpeg::decoder::Audio,
    resampler: Option<(ResampleKey, resampling::Context)>,
    ring: Producer<f32>,
    /// Interleaved samples waiting for room in the ring.
    pending: Vec<f32>,
    /// Frames emitted since the seek point.
    written: i64,
    aligned: bool,
}

type ResampleKey = (ffmpeg::format::Sample, u64, u32, i32);

struct Decoder {
    input: ffmpeg::format::context::Input,
    tracks: Vec<Track>,
    start: f64,
    rate: u32,
    origin: f64,
    frame: ffmpeg::frame::Audio,
}

impl Decoder {
    fn open(info: &MediaInfo, producers: Vec<Producer<f32>>, rate: u32) -> Result<Self> {
        let mut input = ffmpeg::format::input(&info.path)?;
        let indices: Vec<usize> = info.audio.iter().map(|t| t.index).collect();
        super::discard_other_streams(&mut input, &indices);
        let mut tracks = Vec::with_capacity(indices.len());
        for (index, ring) in indices.into_iter().zip(producers) {
            let stream = input
                .stream(index)
                .ok_or_else(|| anyhow::anyhow!("audio stream {index} vanished"))?;
            let mut ctx = ffmpeg::codec::context::Context::from_parameters(stream.parameters())?;
            super::enable_threads(&mut ctx, 1);
            tracks.push(Track {
                stream: index,
                time_base: f64::from(stream.time_base()),
                decoder: ctx.decoder().audio()?,
                resampler: None,
                ring,
                pending: Vec::new(),
                written: 0,
                aligned: false,
            });
        }
        Ok(Self {
            input,
            tracks,
            start: info.start,
            rate,
            origin: 0.0,
            frame: ffmpeg::frame::Audio::empty(),
        })
    }

    fn run(&mut self, rx: &Receiver<Cmd>, flush: &FlushHandle, primed: &AtomicBool) {
        let mut active = false;
        let mut at_end = false;
        loop {
            let cmd = if active {
                match rx.try_recv() {
                    Ok(c) => Some(c),
                    Err(TryRecvError::Empty) => None,
                    Err(TryRecvError::Disconnected) => return,
                }
            } else {
                match rx.recv() {
                    Ok(c) => Some(c),
                    Err(_) => return,
                }
            };
            match cmd {
                Some(Cmd::Stop) => return,
                Some(Cmd::Seek { time, flush: id }) => {
                    self.seek(time);
                    flush.wait(id);
                    active = true;
                    at_end = false;
                }
                None => {}
            }
            if !active {
                continue;
            }

            let backed_up = self.push_pending();
            if self.is_primed() || at_end {
                primed.store(true, Ordering::Release);
            }
            if backed_up {
                std::thread::sleep(Duration::from_millis(5));
                continue;
            }
            if at_end {
                active = false;
                continue;
            }
            at_end = !self.decode_packet();
        }
    }

    fn seek(&mut self, time: f64) {
        if let Err(e) = super::seek_input(&mut self.input, time + self.start) {
            log::warn!("audio seek to {time:.3}s failed: {e}");
        }
        self.origin = time;
        for t in &mut self.tracks {
            t.decoder.flush();
            t.resampler = None;
            t.pending.clear();
            t.written = 0;
            t.aligned = false;
        }
    }

    /// Move pending samples into the rings. True if any track is still
    /// waiting for room.
    fn push_pending(&mut self) -> bool {
        let mut backed_up = false;
        for t in &mut self.tracks {
            let n = t.pending.len().min(t.ring.slots()) / CHANNELS * CHANNELS;
            if n > 0
                && let Ok(chunk) = t.ring.write_chunk_uninit(n)
            {
                chunk.fill_from_iter(t.pending.drain(..n));
            }
            backed_up |= !t.pending.is_empty();
        }
        backed_up
    }

    fn is_primed(&self) -> bool {
        let need = (PRIME_SECONDS * f64::from(self.rate)) as i64;
        self.tracks.iter().all(|t| t.written >= need)
    }

    /// Demux one packet and decode it. False at end of file.
    fn decode_packet(&mut self) -> bool {
        let Some((stream, packet)) = self.input.packets().next() else {
            for i in 0..self.tracks.len() {
                let _ = self.tracks[i].decoder.send_eof();
                self.drain(i);
            }
            return false;
        };
        if let Some(i) = self.tracks.iter().position(|t| t.stream == stream.index()) {
            if let Err(e) = self.tracks[i].decoder.send_packet(&packet) {
                log::debug!("audio packet rejected: {e}");
            }
            self.drain(i);
        }
        true
    }

    fn drain(&mut self, i: usize) {
        while self.tracks[i]
            .decoder
            .receive_frame(&mut self.frame)
            .is_ok()
        {
            if let Err(e) = self.emit(i) {
                log::debug!("audio frame dropped: {e}");
            }
        }
    }

    /// Resample the decoded frame and queue it, aligned to its timestamp.
    fn emit(&mut self, i: usize) -> Result<(), ffmpeg::Error> {
        let (rate, origin, start) = (self.rate, self.origin, self.start);
        let frame = &mut self.frame;
        let t = &mut self.tracks[i];

        if frame.channel_layout().is_empty()
            || frame.channel_layout().0.order
                == ffmpeg::ffi::AVChannelOrder::AV_CHANNEL_ORDER_UNSPEC
        {
            let channels = i32::from(frame.channels()).max(1);
            frame.set_channel_layout(ffmpeg::ChannelLayout::default(channels));
        }
        let key: ResampleKey = (
            frame.format(),
            frame.channel_layout().bits(),
            frame.rate(),
            frame.channel_layout().channels(),
        );
        if t.resampler.as_ref().is_none_or(|(k, _)| *k != key) {
            let ctx = resampling::Context::get(
                frame.format(),
                frame.channel_layout(),
                frame.rate(),
                ffmpeg::format::Sample::F32(ffmpeg::format::sample::Type::Packed),
                ffmpeg::ChannelLayout::STEREO,
                rate,
            )?;
            t.resampler = Some((key, ctx));
        }
        let (_, resampler) = t.resampler.as_mut().expect("resampler just set");
        let mut out = ffmpeg::frame::Audio::empty();
        resampler.run(frame, &mut out)?;

        let frames = out.samples();
        let bytes = &out.data(0)[..frames * CHANNELS * 4];
        let mut samples: Vec<f32> = bytes
            .as_chunks::<4>()
            .0
            .iter()
            .map(|b| f32::from_ne_bytes(*b))
            .collect();

        let rate_f = f64::from(rate);
        if let Some(ts) = frame.timestamp().or(frame.pts()) {
            let media = ts as f64 * t.time_base - start;
            let expected = ((media - origin) * rate_f).round() as i64;
            let diff = expected - t.written;
            let slack = (SLACK_SECONDS * rate_f) as i64;
            if !t.aligned || diff.abs() > slack {
                if diff > 0 {
                    // Gap (or a track that starts late): pad with silence,
                    // bounded so a broken timestamp cannot stall playback.
                    let pad = diff.min(rate_f as i64 * 5) as usize;
                    t.pending.extend(std::iter::repeat_n(0.0, pad * CHANNELS));
                    t.written += pad as i64;
                } else if diff < 0 {
                    // Overlap or pre-roll before the seek point: trim.
                    let cut = ((-diff) as usize).min(frames) * CHANNELS;
                    samples.drain(..cut);
                }
            }
        }
        if !samples.is_empty() {
            t.aligned = true;
            t.written += (samples.len() / CHANNELS) as i64;
            t.pending.extend_from_slice(&samples);
        }
        Ok(())
    }
}
