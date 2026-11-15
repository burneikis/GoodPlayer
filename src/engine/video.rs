//! The video thread: frame-exact positioning, stepping, and playback.
//!
//! The UI never waits on it. Commands go in, frames come out tagged with
//! the epoch of the command that produced them, and the UI ignores anything
//! from an older epoch.

use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::thread::JoinHandle;

use anyhow::Result;
use crossbeam_channel::{Receiver, Sender, bounded, select, unbounded};

use super::cache::{self, FrameCache, Key};
use super::output::Clock;
use crate::media::video::VideoDecoder;
use crate::media::{MediaInfo, VideoFrame};

/// Frames decoded ahead of the clock during playback.
const PLAY_AHEAD: usize = 4;
/// How far a running decoder may be behind a target and still read through
/// to it rather than seek.
const READ_THROUGH_SECONDS: f64 = 3.0;
/// Playback this far behind the clock jumps ahead instead of catching up
/// frame by frame.
const RESYNC_SECONDS: f64 = 1.0;

/// Where playback is, readable from the video thread: the media time the
/// clock started at, plus the clock.
#[derive(Clone)]
pub struct Playhead {
    origin: Arc<AtomicU64>,
    clock: Clock,
}

impl Playhead {
    pub fn new(clock: Clock) -> Self {
        Self {
            origin: Arc::new(AtomicU64::new(0f64.to_bits())),
            clock,
        }
    }

    pub fn set_origin(&self, time: f64) {
        self.origin.store(time.to_bits(), Ordering::Release);
    }

    pub fn origin(&self) -> f64 {
        f64::from_bits(self.origin.load(Ordering::Acquire))
    }

    pub fn now(&self) -> f64 {
        self.origin() + self.clock.seconds()
    }
}

#[derive(Debug, Clone, Copy)]
pub enum Cmd {
    /// Show the frame at `time`. Inexact shows the nearest keyframe, which
    /// is what scrubbing wants.
    Show {
        time: f64,
        exact: bool,
        epoch: u64,
    },
    Step {
        delta: i32,
        epoch: u64,
    },
    Play {
        time: f64,
        epoch: u64,
    },
    /// Stop decoding ahead; `at` is the frame left on screen.
    Pause {
        at: Option<f64>,
    },
}

pub enum Msg {
    Frame {
        epoch: u64,
        frame: Arc<VideoFrame>,
    },
    /// A step hit the start or end, or playback reached the end.
    Edge {
        epoch: u64,
        end: bool,
    },
}

pub struct VideoWorker {
    cmd: Option<Sender<Cmd>>,
    pub msgs: Receiver<Msg>,
    thread: Option<JoinHandle<()>>,
}

impl VideoWorker {
    pub fn spawn(
        info: &MediaInfo,
        cache_bytes: usize,
        playhead: Playhead,
        wake: impl Fn() + Send + 'static,
    ) -> Result<Self> {
        let decoder = VideoDecoder::open(info)?;
        let (cmd_tx, cmd_rx) = unbounded();
        let (msg_tx, msgs) = bounded(PLAY_AHEAD);
        let mut worker = Worker {
            decoder,
            cache: FrameCache::new(cache_bytes),
            last: None,
            eof: false,
            current: None,
            slack: info.frame_duration() / 4.0,
            frame_dur: info.frame_duration(),
            cmds: cmd_rx,
            out: msg_tx,
            wake: Box::new(wake),
            deferred: None,
            playhead,
        };
        let thread = std::thread::Builder::new()
            .name("video-decode".into())
            .spawn(move || worker.run())?;
        Ok(Self {
            cmd: Some(cmd_tx),
            msgs,
            thread: Some(thread),
        })
    }

    pub fn send(&self, cmd: Cmd) {
        if let Some(tx) = &self.cmd {
            let _ = tx.send(cmd);
        }
    }
}

impl Drop for VideoWorker {
    fn drop(&mut self) {
        // Disconnecting the command channel wakes the thread out of any
        // send it is blocked in (see `Worker::emit`).
        self.cmd = None;
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}

struct Worker {
    decoder: VideoDecoder,
    cache: FrameCache,
    /// Key of the last frame the decoder produced since its last seek.
    last: Option<Key>,
    eof: bool,
    /// The frame the user is looking at, for stepping.
    current: Option<Key>,
    slack: f64,
    frame_dur: f64,
    cmds: Receiver<Cmd>,
    out: Sender<Msg>,
    wake: Box<dyn Fn() + Send>,
    /// A command that arrived while a send was blocked.
    deferred: Option<Cmd>,
    playhead: Playhead,
}

enum Flow {
    Continue,
    Exit,
}

impl Worker {
    fn run(&mut self) {
        loop {
            let cmd = match self.deferred.take() {
                Some(c) => c,
                None => match self.cmds.recv() {
                    Ok(c) => c,
                    Err(_) => return,
                },
            };
            let cmd = self.coalesce(cmd);
            if let Flow::Exit = self.handle(cmd) {
                return;
            }
        }
    }

    /// Fold a burst of queued commands: later positioning supersedes
    /// earlier, and consecutive steps add up.
    fn coalesce(&mut self, mut cmd: Cmd) -> Cmd {
        while let Ok(next) = self.cmds.try_recv() {
            cmd = match (cmd, next) {
                (Cmd::Step { delta: a, .. }, Cmd::Step { delta: b, epoch }) => Cmd::Step {
                    delta: a + b,
                    epoch,
                },
                (
                    Cmd::Show { .. } | Cmd::Play { .. },
                    n @ (Cmd::Show { .. } | Cmd::Play { .. }),
                ) => n,
                (Cmd::Pause { at }, n) => {
                    // Superseded, but the frame it left on screen is where
                    // whatever comes next starts from.
                    if let Some(t) = at {
                        self.current = Some(cache::key(t));
                    }
                    n
                }
                (c, n) => {
                    // Order matters (e.g. a step after a show); run this one.
                    self.deferred = Some(n);
                    return c;
                }
            };
        }
        cmd
    }

    fn handle(&mut self, cmd: Cmd) -> Flow {
        match cmd {
            Cmd::Show { time, exact, epoch } => {
                let frame = if exact {
                    self.frame_at(time)
                } else {
                    self.keyframe_near(time)
                };
                match frame {
                    Some(f) => {
                        self.current = Some(cache::key(f.time));
                        self.emit(Msg::Frame { epoch, frame: f })
                    }
                    None => self.emit(Msg::Edge { epoch, end: true }),
                }
            }
            Cmd::Step { delta, epoch } => self.step(delta, epoch),
            Cmd::Play { time, epoch } => self.play(time, epoch),
            Cmd::Pause { at } => {
                if let Some(t) = at {
                    self.current = Some(cache::key(t));
                }
                Flow::Continue
            }
        }
    }

    /// Send to the UI without ever blocking a command behind it.
    fn emit(&mut self, msg: Msg) -> Flow {
        select! {
            send(self.out, msg) -> res => {
                if res.is_err() {
                    return Flow::Exit;
                }
                (self.wake)();
                Flow::Continue
            }
            recv(self.cmds) -> cmd => match cmd {
                Ok(c) => {
                    self.deferred = Some(c);
                    Flow::Continue
                }
                Err(_) => Flow::Exit,
            }
        }
    }

    fn reposition(&mut self, time: f64) {
        self.decoder.seek(time.max(0.0));
        self.last = None;
        self.eof = false;
    }

    /// Decode the next frame into the cache, linked to the one before.
    fn decode(&mut self, center: Key) -> Option<Arc<VideoFrame>> {
        if self.eof {
            return None;
        }
        match self.decoder.next_frame() {
            Some(f) => {
                self.last = Some(self.cache.insert(Arc::clone(&f), self.last, center));
                Some(f)
            }
            None => {
                if let Some(l) = self.last {
                    self.cache.mark_last(l);
                }
                self.eof = true;
                None
            }
        }
    }

    /// Exactly the frame on screen at `time`.
    fn frame_at(&mut self, time: f64) -> Option<Arc<VideoFrame>> {
        self.cache
            .at(time, self.slack)
            .or_else(|| self.decode_to(time))
    }

    /// Decode up to the frame on screen at `time`, leaving the decoder just
    /// past it (so its successor is decoded and linked too).
    fn decode_to(&mut self, time: f64) -> Option<Arc<VideoFrame>> {
        let center = cache::key(time);
        let limit = time + self.slack;
        let reading_through = !self.eof
            && self.last.is_some_and(|l| {
                cache::time(l) <= limit && time - cache::time(l) < READ_THROUGH_SECONDS
            });
        if reading_through {
            let mut prev = self.last.and_then(|l| self.cache.get(l));
            while let Some(f) = self.decode(center) {
                if f.time > limit {
                    return prev.or(Some(f));
                }
                prev = Some(f);
            }
            return prev;
        }
        // A seek can land after the target on a bad index; back off.
        let mut prev = None;
        for back in [0.0, 1.0, 4.0, 15.0] {
            self.reposition(time - back);
            prev = None;
            while let Some(f) = self.decode(center) {
                if f.time > limit {
                    if prev.is_none() && time - back > 0.0 && back < 15.0 {
                        break; // landed past the target: seek further back
                    }
                    return prev.or(Some(f));
                }
                prev = Some(f);
            }
            if prev.is_some() {
                return prev; // end of stream
            }
        }
        prev
    }

    /// The first frame at the keyframe at or before `time`. Cheap.
    fn keyframe_near(&mut self, time: f64) -> Option<Arc<VideoFrame>> {
        if let Some(f) = self.cache.at(time, self.slack) {
            return Some(f);
        }
        self.reposition(time);
        self.decode(cache::key(time))
            .or_else(|| self.frame_at(time))
    }

    fn step(&mut self, delta: i32, epoch: u64) -> Flow {
        let Some(mut current) = self.current else {
            return self.emit(Msg::Edge {
                epoch,
                end: delta > 0,
            });
        };
        let mut frame = None;
        for _ in 0..delta.unsigned_abs() {
            let next = if delta > 0 {
                self.step_forward(current)
            } else {
                self.step_back(current)
            };
            match next {
                Some(f) => {
                    current = cache::key(f.time);
                    frame = Some(f);
                }
                None => break,
            }
        }
        self.current = Some(current);
        match frame {
            Some(frame) => self.emit(Msg::Frame { epoch, frame }),
            None => self.emit(Msg::Edge {
                epoch,
                end: delta > 0,
            }),
        }
    }

    fn step_forward(&mut self, from: Key) -> Option<Arc<VideoFrame>> {
        if let Some(f) = self.cache.next(from) {
            return Some(f);
        }
        if self.cache.is_last(from) {
            return None;
        }
        if self.last == Some(from) {
            return self.decode(from);
        }
        // Get the decoder onto `from`; decoding the frame on screen also
        // decodes (and links) the one after it.
        self.decode_to(cache::time(from));
        match self.cache.next(from) {
            Some(f) => Some(f),
            None if self.last == Some(from) => self.decode(from),
            None => None,
        }
    }

    fn step_back(&mut self, from: Key) -> Option<Arc<VideoFrame>> {
        if let Some(f) = self.cache.prev(from) {
            return Some(f);
        }
        let t = cache::time(from);
        if t <= self.slack {
            return None;
        }
        // Decode the run of frames leading up to `from`; they are all kept,
        // so the next steps back are free.
        for back in [self.frame_dur / 2.0, 1.0, 4.0, 15.0] {
            self.reposition(t - back);
            let mut first = true;
            while let Some(f) = self.decode(from) {
                let k = cache::key(f.time);
                if k >= from {
                    break;
                }
                first = false;
            }
            if let Some(f) = self.cache.prev(from) {
                return Some(f);
            }
            if !first || t - back <= 0.0 {
                break;
            }
        }
        None
    }

    fn play(&mut self, time: f64, epoch: u64) -> Flow {
        let Some(first) = self.frame_at(time) else {
            return self.emit(Msg::Edge { epoch, end: true });
        };
        let mut cursor = cache::key(first.time);
        let mut msg = Msg::Frame {
            epoch,
            frame: first,
        };
        loop {
            if let Flow::Exit = self.emit(msg) {
                return Flow::Exit;
            }
            if self.deferred.is_some() || !self.cmds.is_empty() {
                return Flow::Continue;
            }
            let next = loop {
                self.current = Some(cursor);
                let next = match self.cache.next(cursor) {
                    Some(f) => Some(f),
                    None if self.last == Some(cursor) => self.decode(cursor),
                    None if self.cache.is_last(cursor) => None,
                    None => self.step_forward(cursor),
                };
                // Behind the clock (a stalled window, a slow decode): drop
                // late frames, and jump if hopelessly behind.
                let Some(f) = next else { break None };
                let now = self.playhead.now();
                if f.time + self.frame_dur >= now {
                    break Some(f);
                }
                if now - f.time > RESYNC_SECONDS {
                    break self.decode_to(now + 0.25).or(Some(f));
                }
                cursor = cache::key(f.time);
            };
            msg = match next {
                Some(f) => {
                    cursor = cache::key(f.time);
                    Msg::Frame { epoch, frame: f }
                }
                None => {
                    log::debug!(
                        "playback ended at {:.3}s (eof={}, last={:?}, is_last={})",
                        cache::time(cursor),
                        self.eof,
                        self.last.map(cache::time),
                        self.cache.is_last(cursor)
                    );
                    return self.emit(Msg::Edge { epoch, end: true });
                }
            };
        }
    }
}
