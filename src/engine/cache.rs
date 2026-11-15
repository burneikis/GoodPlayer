//! Decoded frames near the playhead, linked in presentation order.
//!
//! Each entry remembers the frame that was decoded right after it, so
//! "next" and "previous" are answered from memory without assuming a
//! constant frame rate. Eviction is by distance from the playhead and
//! bounded in bytes.

use std::collections::BTreeMap;
use std::sync::Arc;

use crate::media::VideoFrame;

/// Frames are keyed by media time in microseconds.
pub type Key = i64;

pub fn key(time: f64) -> Key {
    (time * 1e6).round() as Key
}

pub fn time(key: Key) -> f64 {
    key as f64 / 1e6
}

struct Entry {
    frame: Arc<VideoFrame>,
    next: Option<Key>,
    last: bool,
}

pub struct FrameCache {
    map: BTreeMap<Key, Entry>,
    bytes: usize,
    budget: usize,
}

impl FrameCache {
    pub fn new(budget: usize) -> Self {
        Self {
            map: BTreeMap::new(),
            bytes: 0,
            budget,
        }
    }

    /// Insert a frame decoded directly after `prev` (if any), evicting what
    /// lies furthest from `center` when over budget.
    pub fn insert(&mut self, frame: Arc<VideoFrame>, prev: Option<Key>, center: Key) -> Key {
        let k = key(frame.time);
        let size = frame.bytes();
        match self.map.get_mut(&k) {
            Some(e) => e.frame = frame,
            None => {
                self.map.insert(
                    k,
                    Entry {
                        frame,
                        next: None,
                        last: false,
                    },
                );
                self.bytes += size;
            }
        }
        if let Some(p) = prev.and_then(|p| self.map.get_mut(&p))
            && p.frame.time < time(k)
        {
            p.next = Some(k);
        }
        self.evict(center, k);
        k
    }

    pub fn mark_last(&mut self, k: Key) {
        if let Some(e) = self.map.get_mut(&k) {
            e.last = true;
        }
    }

    pub fn get(&self, k: Key) -> Option<Arc<VideoFrame>> {
        self.map.get(&k).map(|e| Arc::clone(&e.frame))
    }

    pub fn is_last(&self, k: Key) -> bool {
        self.map.get(&k).is_some_and(|e| e.last)
    }

    pub fn next(&self, k: Key) -> Option<Arc<VideoFrame>> {
        self.map
            .get(&k)
            .and_then(|e| e.next)
            .and_then(|n| self.get(n))
    }

    pub fn prev(&self, k: Key) -> Option<Arc<VideoFrame>> {
        self.map
            .range(..k)
            .next_back()
            .filter(|(_, e)| e.next == Some(k))
            .map(|(_, e)| Arc::clone(&e.frame))
    }

    /// The frame on screen at `t`, if the cache knows for certain: the
    /// latest frame starting at or before `t` whose successor is known to
    /// start after it.
    pub fn at(&self, t: f64, slack: f64) -> Option<Arc<VideoFrame>> {
        let limit = key(t + slack);
        let (_, e) = self.map.range(..=limit).next_back()?;
        let covered = e.last || e.next.is_some_and(|n| n > limit);
        covered.then(|| Arc::clone(&e.frame))
    }

    fn evict(&mut self, center: Key, keep: Key) {
        while self.bytes > self.budget && self.map.len() > 2 {
            let first = *self.map.keys().next().expect("non-empty");
            let last = *self.map.keys().next_back().expect("non-empty");
            let victim = if (center - first).abs() >= (last - center).abs() {
                first
            } else {
                last
            };
            let victim = if victim == keep || victim == center {
                if victim == first { last } else { first }
            } else {
                victim
            };
            if let Some(e) = self.map.remove(&victim) {
                self.bytes -= e.frame.bytes();
            }
        }
    }
}
