//! Playback engine: audio output and clock, frame cache, video thread, and
//! the transport that ties them together.

pub mod cache;
pub mod output;
pub mod player;
pub mod video;

pub use output::AudioOutput;
pub use player::{Event, Player};
