//! The audio device, the track mixer, and the playback clock.
//!
//! The device consuming samples is the master clock. Every audio track has
//! its own lock-free ring; the device callback mixes them with per-track
//! gains. A track that runs dry accrues a debt it pays back by skipping the
//! samples it missed, so tracks can never drift apart from each other or
//! from the clock.

use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use crossbeam_channel::{Receiver, Sender, bounded};
use rtrb::{Consumer, Producer, RingBuffer};

/// Output is always interleaved stereo internally.
pub const CHANNELS: usize = 2;
/// Seconds of decoded audio each track ring can hold.
const RING_SECONDS: usize = 2;
const NULL_RATE: u32 = 48_000;

/// Live controls for one track, shared between the UI and the callback.
#[derive(Debug)]
pub struct TrackControl {
    gain: AtomicU32,
    muted: AtomicBool,
}

impl TrackControl {
    fn new() -> Self {
        Self {
            gain: AtomicU32::new(1.0f32.to_bits()),
            muted: AtomicBool::new(false),
        }
    }
    pub fn gain(&self) -> f32 {
        f32::from_bits(self.gain.load(Ordering::Relaxed))
    }
    pub fn set_gain(&self, gain: f32) {
        self.gain
            .store(gain.clamp(0.0, 2.0).to_bits(), Ordering::Relaxed);
    }
    pub fn muted(&self) -> bool {
        self.muted.load(Ordering::Relaxed)
    }
    pub fn set_muted(&self, muted: bool) {
        self.muted.store(muted, Ordering::Relaxed);
    }
    fn effective(&self) -> f32 {
        if self.muted() { 0.0 } else { self.gain() }
    }
}

struct TrackInput {
    ring: Consumer<f32>,
    control: Arc<TrackControl>,
    /// Frames this track failed to deliver, to be skipped when data arrives.
    debt: usize,
}

struct Shared {
    rate: u32,
    base: Instant,
    running: AtomicBool,
    master: AtomicU32,
    muted: AtomicBool,
    /// Frames consumed since the last flush.
    played: AtomicU64,
    flush_req: AtomicU64,
    flush_ack: AtomicU64,
    /// Seqlock over (frames played before the current buffer, when that
    /// buffer starts being heard), used to interpolate the clock.
    seq: AtomicU64,
    mark_played: AtomicU64,
    mark_ns: AtomicU64,
}

impl Shared {
    fn now_ns(&self) -> u64 {
        self.base.elapsed().as_nanos() as u64
    }

    fn set_mark(&self, played: u64, heard_at_ns: u64) {
        self.seq.fetch_add(1, Ordering::AcqRel);
        self.mark_played.store(played, Ordering::Relaxed);
        self.mark_ns.store(heard_at_ns, Ordering::Relaxed);
        self.seq.fetch_add(1, Ordering::AcqRel);
    }

    fn mark(&self) -> (u64, u64) {
        loop {
            let s1 = self.seq.load(Ordering::Acquire);
            let played = self.mark_played.load(Ordering::Relaxed);
            let ns = self.mark_ns.load(Ordering::Relaxed);
            let s2 = self.seq.load(Ordering::Acquire);
            if s1 == s2 && s1 % 2 == 0 {
                return (played, ns);
            }
            std::hint::spin_loop();
        }
    }
}

/// State owned by the real-time callback.
struct Callback {
    shared: Arc<Shared>,
    tracks: Vec<TrackInput>,
    incoming: Receiver<Vec<TrackInput>>,
    garbage: Sender<Vec<TrackInput>>,
    mix: Vec<f32>,
    seen_flush: u64,
}

impl Callback {
    /// Fill `out` (interleaved, `channels` wide) and advance the clock.
    fn render(&mut self, out: &mut [f32], channels: usize, latency_ns: u64) {
        if let Ok(tracks) = self.incoming.try_recv() {
            let old = std::mem::replace(&mut self.tracks, tracks);
            let _ = self.garbage.try_send(old);
        }
        let shared = Arc::clone(&self.shared);
        let flush = shared.flush_req.load(Ordering::Acquire);
        if flush != self.seen_flush {
            for t in &mut self.tracks {
                let n = t.ring.slots();
                if let Ok(chunk) = t.ring.read_chunk(n) {
                    chunk.commit_all();
                }
                t.debt = 0;
            }
            shared.played.store(0, Ordering::Release);
            self.seen_flush = flush;
            shared.flush_ack.store(flush, Ordering::Release);
        }

        let frames = out.len() / channels.max(1);
        let played = shared.played.load(Ordering::Acquire);
        shared.set_mark(played, shared.now_ns() + latency_ns);
        if !shared.running.load(Ordering::Acquire) {
            out.fill(0.0);
            return;
        }

        self.mix.clear();
        self.mix.resize(frames * CHANNELS, 0.0);
        for t in &mut self.tracks {
            mix_track(t, &mut self.mix);
        }

        let master = if shared.muted.load(Ordering::Relaxed) {
            0.0
        } else {
            f32::from_bits(shared.master.load(Ordering::Relaxed))
        };
        for (frame, src) in out
            .chunks_exact_mut(channels)
            .zip(self.mix.chunks_exact(CHANNELS))
        {
            let (l, r) = (
                (src[0] * master).clamp(-1.0, 1.0),
                (src[1] * master).clamp(-1.0, 1.0),
            );
            match frame {
                [mono] => *mono = 0.5 * (l + r),
                [a, b, rest @ ..] => {
                    *a = l;
                    *b = r;
                    rest.fill(0.0);
                }
                [] => {}
            }
        }
        shared.played.fetch_add(frames as u64, Ordering::AcqRel);
    }
}

fn mix_track(t: &mut TrackInput, mix: &mut [f32]) {
    let gain = t.control.effective();
    let mut available = t.ring.slots() / CHANNELS;
    if t.debt > 0 {
        let skip = t.debt.min(available);
        if let Ok(chunk) = t.ring.read_chunk(skip * CHANNELS) {
            chunk.commit_all();
        }
        t.debt -= skip;
        available -= skip;
    }
    let wanted = mix.len() / CHANNELS;
    let n = wanted.min(available);
    if let Ok(chunk) = t.ring.read_chunk(n * CHANNELS) {
        let (a, b) = chunk.as_slices();
        for (m, s) in mix.iter_mut().zip(a.iter().chain(b)) {
            *m += s * gain;
        }
        chunk.commit_all();
    }
    t.debt += wanted - n;
}

/// The audio device (or a silent stand-in that keeps time with the wall
/// clock when there is none).
pub struct AudioOutput {
    shared: Arc<Shared>,
    tracks_tx: Sender<Vec<TrackInput>>,
    garbage_rx: Receiver<Vec<TrackInput>>,
    _stream: Option<cpal::Stream>,
    null: Option<(Arc<AtomicBool>, JoinHandle<()>)>,
}

impl AudioOutput {
    /// The default output device, falling back to [`Self::silent`].
    pub fn open() -> Self {
        Self::build(cpal::default_host().default_output_device())
    }

    /// No device: keeps time with the wall clock. Used when there is no
    /// sound card, and by tests.
    pub fn silent() -> Self {
        Self::build(None)
    }

    fn build(device: Option<cpal::Device>) -> Self {
        let (tracks_tx, tracks_rx) = bounded(4);
        let (garbage_tx, garbage_rx) = bounded(4);
        let config = device.as_ref().and_then(|d| d.default_output_config().ok());
        let rate = config.as_ref().map_or(NULL_RATE, |c| c.sample_rate());
        let shared = Arc::new(Shared {
            rate,
            base: Instant::now(),
            running: AtomicBool::new(false),
            master: AtomicU32::new(1.0f32.to_bits()),
            muted: AtomicBool::new(false),
            played: AtomicU64::new(0),
            flush_req: AtomicU64::new(0),
            flush_ack: AtomicU64::new(0),
            seq: AtomicU64::new(0),
            mark_played: AtomicU64::new(0),
            mark_ns: AtomicU64::new(0),
        });
        let callback = Callback {
            shared: Arc::clone(&shared),
            tracks: Vec::new(),
            incoming: tracks_rx,
            garbage: garbage_tx,
            mix: Vec::with_capacity(8192),
            seen_flush: 0,
        };

        let mut out = Self {
            shared,
            tracks_tx,
            garbage_rx,
            _stream: None,
            null: None,
        };
        let callback = Arc::new(Mutex::new(callback));
        match device
            .zip(config)
            .map(|(d, c)| build_stream(&d, &c, Arc::clone(&callback)))
        {
            Some(Ok(stream)) => out._stream = Some(stream),
            Some(Err(e)) => log::warn!("audio device unavailable ({e}), playing silently"),
            None => log::info!("no audio device, playing silently"),
        }
        if out._stream.is_none() {
            out.null = Some(spawn_null(callback));
        }
        out
    }

    pub fn rate(&self) -> u32 {
        self.shared.rate
    }

    /// Replace the mixed tracks with `n` fresh ones.
    pub fn connect(&self, n: usize) -> (Vec<Producer<f32>>, Vec<Arc<TrackControl>>) {
        while self.garbage_rx.try_recv().is_ok() {}
        let capacity = self.shared.rate as usize * CHANNELS * RING_SECONDS;
        let mut producers = Vec::with_capacity(n);
        let mut controls = Vec::with_capacity(n);
        let mut inputs = Vec::with_capacity(n);
        for _ in 0..n {
            let (producer, ring) = RingBuffer::new(capacity);
            let control = Arc::new(TrackControl::new());
            producers.push(producer);
            controls.push(Arc::clone(&control));
            inputs.push(TrackInput {
                ring,
                control,
                debt: 0,
            });
        }
        let _ = self.tracks_tx.send(inputs);
        (producers, controls)
    }

    /// Ask the callback to drop all queued audio and restart the clock.
    pub fn request_flush(&self) -> u64 {
        self.shared.flush_req.fetch_add(1, Ordering::AcqRel) + 1
    }

    pub fn is_flushed(&self, id: u64) -> bool {
        self.shared.flush_ack.load(Ordering::Acquire) >= id
    }

    pub fn handle(&self) -> FlushHandle {
        FlushHandle(Arc::clone(&self.shared))
    }

    pub fn set_running(&self, running: bool) {
        self.shared.running.store(running, Ordering::Release);
    }

    pub fn set_master(&self, volume: f32) {
        self.shared
            .master
            .store(volume.clamp(0.0, 1.0).to_bits(), Ordering::Relaxed);
    }

    pub fn set_muted(&self, muted: bool) {
        self.shared.muted.store(muted, Ordering::Relaxed);
    }

    /// Seconds of audio heard since the last flush, interpolated between
    /// device callbacks so the picture moves smoothly.
    pub fn clock(&self) -> f64 {
        let s = &self.shared;
        let (played, heard_at) = s.mark();
        let delivered = s.played.load(Ordering::Acquire);
        let rate = f64::from(s.rate);
        if !s.running.load(Ordering::Acquire) {
            return delivered as f64 / rate;
        }
        let since = (s.now_ns() as f64 - heard_at as f64) / 1e9;
        let frames = (played as f64 + since * rate).clamp(0.0, delivered as f64);
        frames / rate
    }
}

impl Drop for AudioOutput {
    fn drop(&mut self) {
        if let Some((stop, handle)) = self.null.take() {
            stop.store(true, Ordering::Release);
            let _ = handle.join();
        }
    }
}

/// Lets the audio decoder thread wait for a flush to land.
#[derive(Clone)]
pub struct FlushHandle(Arc<Shared>);

impl FlushHandle {
    /// Wait (bounded) until flush `id` has been applied by the callback.
    pub fn wait(&self, id: u64) {
        let deadline = Instant::now() + Duration::from_millis(500);
        while self.0.flush_ack.load(Ordering::Acquire) < id && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(1));
        }
    }

    pub fn rate(&self) -> u32 {
        self.0.rate
    }
}

fn build_stream(
    device: &cpal::Device,
    config: &cpal::SupportedStreamConfig,
    callback: Arc<Mutex<Callback>>,
) -> Result<cpal::Stream, String> {
    use cpal::SampleFormat as F;
    let stream = match config.sample_format() {
        F::F32 => build_typed::<f32>(device, config, callback),
        F::I16 => build_typed::<i16>(device, config, callback),
        F::U16 => build_typed::<u16>(device, config, callback),
        F::I32 => build_typed::<i32>(device, config, callback),
        other => return Err(format!("unsupported sample format {other}")),
    }?;
    stream.play().map_err(|e| e.to_string())?;
    Ok(stream)
}

fn build_typed<T>(
    device: &cpal::Device,
    config: &cpal::SupportedStreamConfig,
    callback: Arc<Mutex<Callback>>,
) -> Result<cpal::Stream, String>
where
    T: cpal::SizedSample + cpal::FromSample<f32>,
{
    let channels = usize::from(config.channels());
    let mut scratch: Vec<f32> = Vec::with_capacity(8192);
    let data = move |out: &mut [T], info: &cpal::OutputCallbackInfo| {
        let ts = info.timestamp();
        let latency = ts.playback.duration_since(ts.callback).as_nanos() as u64;
        scratch.clear();
        scratch.resize(out.len(), 0.0);
        // Uncontended: only this callback ever locks it once running.
        if let Ok(mut cb) = callback.try_lock() {
            cb.render(&mut scratch, channels, latency);
        }
        for (o, s) in out.iter_mut().zip(&scratch) {
            *o = T::from_sample(*s);
        }
    };
    device
        .build_output_stream(
            config.config(),
            data,
            |e| log::warn!("audio stream error: {e}"),
            None,
        )
        .map_err(|e| e.to_string())
}

/// No device: render on a timer so playback still runs at the right speed.
fn spawn_null(callback: Arc<Mutex<Callback>>) -> (Arc<AtomicBool>, JoinHandle<()>) {
    let stop = Arc::new(AtomicBool::new(false));
    let flag = Arc::clone(&stop);
    let handle = std::thread::spawn(move || {
        const PERIOD: usize = (NULL_RATE / 100) as usize;
        let mut buf = vec![0.0f32; PERIOD * CHANNELS];
        let mut next = Instant::now();
        while !flag.load(Ordering::Acquire) {
            if let Ok(mut cb) = callback.lock() {
                cb.render(&mut buf, CHANNELS, 0);
            }
            next += Duration::from_millis(10);
            std::thread::sleep(next.saturating_duration_since(Instant::now()));
        }
    });
    (stop, handle)
}
