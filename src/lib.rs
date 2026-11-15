//! GoodPlayer: a frame-accurate video player that plays every audio track
//! of a file at once, with a per-track mixer.

pub mod engine;
pub mod media;
pub mod profile;
pub mod ui;

pub use profile::{DOESPLAYER, GOODPLAYER, Profile};

/// Run the player with the given edition. Returns the process exit code.
pub fn run(profile: &'static Profile) -> std::process::ExitCode {
    env_logger::Builder::from_env(
        env_logger::Env::default().default_filter_or("warn,goodplayer=info"),
    )
    .init();
    media::init();
    let file = std::env::args_os()
        .skip(1)
        .map(std::path::PathBuf::from)
        .find(|p| p.is_file());
    match ui::run(profile, file) {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("{}: {e}", profile.name);
            std::process::ExitCode::FAILURE
        }
    }
}
