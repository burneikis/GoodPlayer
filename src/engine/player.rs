//! Transport: play, pause, seek and step, with audio as the master clock.

use std::collections::VecDeque;
use std::path::Path;
use std::rc::Rc;
use std::sync::Arc;
use std::time::{Duration, Instant};

use anyhow::Result;

use super::output::{AudioOutput, TrackControl};
use super::video::{Cmd, Msg, Playhead, VideoWorker};
use crate::media::audio::AudioWorker;
use crate::media::{MediaInfo, VideoFrame};

/// Longest we wait for audio and video to be ready before starting anyway.
const START_TIMEOUT: Duration = Duration::from_millis(1500);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum State {
    Paused,
    /// Waiting for the first frame and enough audio to start together.
    Starting,
    Playing,
}

/// Things the UI may want to tell the user about.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Event {
    ReachedStart,
    ReachedEnd,
}

pub struct Player {
    pub info: MediaInfo,
    pub tracks: Vec<Arc<TrackControl>>,
    output: Rc<AudioOutput>,
    video: VideoWorker,
    audio: Option<AudioWorker>,
    playhead: Playhead,
    /// The mixer flush the current start is waiting on.
    flush: u64,
    epoch: u64,
    state: State,
    started: Instant,
    shown: Option<Arc<VideoFrame>>,
    queued: VecDeque<Arc<VideoFrame>>,
    /// Where the playhead is while paused.
    position: f64,
    video_ended: bool,
    at_end: bool,
}

impl Player {
    pub fn open(
        path: &Path,
        output: Rc<AudioOutput>,
        cache_bytes: usize,
        wake: impl Fn() + Clone + Send + 'static,
    ) -> Result<Self> {
        let info = MediaInfo::probe(path)?;
        output.set_running(false);
        let (producers, tracks) = output.connect(info.audio.len());
        let audio = if info.audio.is_empty() {
            None
        } else {
            Some(AudioWorker::spawn(&info, producers, output.handle())?)
        };
        let playhead = Playhead::new(output.clock_handle());
        let video = VideoWorker::spawn(&info, cache_bytes, playhead.clone(), wake)?;
        let mut player = Self {
            info,
            tracks,
            output,
            video,
            audio,
            playhead,
            flush: 0,
            epoch: 0,
            state: State::Paused,
            started: Instant::now(),
            shown: None,
            queued: VecDeque::new(),
            position: 0.0,
            video_ended: false,
            at_end: false,
        };
        player.seek(0.0, true);
        Ok(player)
    }

    pub fn is_playing(&self) -> bool {
        self.state != State::Paused
    }

    /// The frame to draw.
    pub fn frame(&self) -> Option<&Arc<VideoFrame>> {
        self.shown.as_ref()
    }

    /// Current playhead in media seconds.
    pub fn time(&self) -> f64 {
        match self.state {
            State::Playing => self.playhead.now().min(self.info.duration),
            _ => self.position,
        }
    }

    /// Zero-based number of the frame on screen.
    pub fn frame_number(&self) -> u64 {
        let t = self.shown.as_ref().map_or(self.position, |f| f.time);
        self.info
            .frame_number(t)
            .min(self.info.total_frames().saturating_sub(1))
    }

    pub fn toggle(&mut self) {
        if self.is_playing() {
            self.pause()
        } else {
            self.play()
        }
    }

    pub fn play(&mut self) {
        if self.is_playing() {
            return;
        }
        // `position` tracks the latest paused target, including a seek whose
        // frame has not arrived yet; `shown` may still be the old frame.
        let from = if self.at_end { 0.0 } else { self.position };
        self.start_at(from);
    }

    pub fn pause(&mut self) {
        if !self.is_playing() {
            return;
        }
        self.output.set_running(false);
        self.state = State::Paused;
        self.epoch += 1;
        self.queued.clear();
        let at = self.shown.as_ref().map(|f| f.time);
        self.position = at.unwrap_or(self.position);
        self.video.send(Cmd::Pause { at });
    }

    /// Jump to `time`. Inexact seeks land on a keyframe (for scrubbing).
    pub fn seek(&mut self, time: f64, exact: bool) {
        let time = time.clamp(0.0, self.info.duration);
        self.at_end = false;
        if self.is_playing() {
            self.start_at(time);
        } else {
            self.epoch += 1;
            self.position = time;
            self.video.send(Cmd::Show {
                time,
                exact,
                epoch: self.epoch,
            });
        }
    }

    pub fn skip(&mut self, seconds: f64) {
        let base = self
            .shown
            .as_ref()
            .filter(|_| !self.is_playing())
            .map_or_else(|| self.time(), |f| f.time);
        self.seek(base + seconds, true);
    }

    pub fn step(&mut self, delta: i32) {
        self.pause();
        self.at_end = false;
        self.epoch += 1;
        self.video.send(Cmd::Step {
            delta,
            epoch: self.epoch,
        });
    }

    fn start_at(&mut self, time: f64) {
        self.epoch += 1;
        self.queued.clear();
        self.video_ended = false;
        self.at_end = false;
        self.output.set_running(false);
        let flush = self.output.request_flush();
        if let Some(audio) = &self.audio {
            audio.seek(time, flush);
        }
        self.flush = flush;
        self.playhead.set_origin(time);
        self.video.send(Cmd::Play {
            time,
            epoch: self.epoch,
        });
        self.position = time;
        self.state = State::Starting;
        self.started = Instant::now();
    }

    /// Advance the transport. Call once per UI frame.
    pub fn update(&mut self) -> Option<Event> {
        let mut event = None;
        let now = self.time();
        loop {
            // While playing, only pull frames until one is in the future:
            // the bounded channel is what keeps the decoder from racing ahead.
            if self.is_playing() && self.queued.back().is_some_and(|f| f.time > now) {
                break;
            }
            let Ok(msg) = self.video.msgs.try_recv() else {
                break;
            };
            match msg {
                Msg::Frame { epoch, frame } if epoch == self.epoch => {
                    if self.is_playing() {
                        self.queued.push_back(frame);
                    } else {
                        self.position = frame.time;
                        self.shown = Some(frame);
                    }
                }
                Msg::Edge { epoch, end } if epoch == self.epoch => {
                    if self.is_playing() {
                        self.video_ended = true;
                    } else {
                        event = Some(if end {
                            Event::ReachedEnd
                        } else {
                            Event::ReachedStart
                        });
                    }
                }
                _ => {}
            }
        }

        match self.state {
            State::Paused => {}
            State::Starting => {
                let audio_ready = self.audio.as_ref().is_none_or(AudioWorker::primed);
                let video_ready = !self.queued.is_empty() || self.video_ended;
                let flushed = self.output.is_flushed(self.flush);
                if flushed
                    && ((audio_ready && video_ready) || self.started.elapsed() > START_TIMEOUT)
                {
                    if let Some(first) = self.queued.pop_front() {
                        self.shown = Some(first);
                    }
                    self.state = State::Playing;
                    self.output.set_running(true);
                }
            }
            State::Playing => {
                let now = self.time();
                while self.queued.front().is_some_and(|f| f.time <= now) {
                    self.shown = self.queued.pop_front();
                }
                let shown_end =
                    self.shown.as_ref().map_or(0.0, |f| f.time) + self.info.frame_duration();
                if self.video_ended
                    && self.queued.is_empty()
                    && now >= shown_end.min(self.info.duration)
                {
                    self.pause();
                    self.at_end = true;
                    event = Some(Event::ReachedEnd);
                }
            }
        }
        event
    }
}

impl Drop for Player {
    fn drop(&mut self) {
        self.output.set_running(false);
    }
}
