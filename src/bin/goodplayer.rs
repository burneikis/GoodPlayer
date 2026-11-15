#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]

fn main() -> std::process::ExitCode {
    goodplayer::run(&goodplayer::GOODPLAYER)
}
