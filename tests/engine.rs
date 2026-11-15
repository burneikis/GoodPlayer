//! End-to-end checks of the engine against generated media. Each frame's
//! luma is `(index * 7) mod 256` (lossless), so the decoded picture says
//! which frame it is.

use std::path::{Path, PathBuf};
use std::process::Command;
use std::rc::Rc;
use std::sync::OnceLock;
use std::time::{Duration, Instant};

use goodplayer::engine::{AudioOutput, Player};
use goodplayer::media::{MediaInfo, VideoFrame};

const FPS: u32 = 30;
const SECONDS: u32 = 10;
const FRAMES: u64 = (FPS * SECONDS) as u64;

fn fixture() -> &'static Path {
    static PATH: OnceLock<PathBuf> = OnceLock::new();
    PATH.get_or_init(|| {
        goodplayer::media::init();
        let path = std::env::temp_dir().join(format!("goodplayer-test-{}.mkv", std::process::id()));
        let status = Command::new("ffmpeg")
            .args(["-v", "error", "-y", "-f", "lavfi", "-i"])
            .arg(format!(
                "color=black:size=64x48:rate={FPS}:duration={SECONDS}"
            ))
            .args(["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"])
            .args(["-f", "lavfi", "-i", "sine=frequency=660:sample_rate=44100"])
            .args(["-vf", "format=yuv420p,geq=lum='mod(N*7,256)':cb=128:cr=128"])
            .args([
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-map",
                "2:a",
                "-t",
                &SECONDS.to_string(),
            ])
            .args([
                "-c:v", "libx264", "-qp", "0", "-g", "45", "-bf", "2", "-c:a", "flac",
            ])
            .arg(&path)
            .status()
            .expect("ffmpeg must be installed to run the engine tests");
        assert!(status.success(), "fixture generation failed");
        path
    })
}

fn index_of(frame: &VideoFrame) -> u64 {
    let luma = u64::from(frame.y[frame.y.len() / 2]);
    // Invert (n * 7) mod 256 for n < 256; beyond that use the timestamp to
    // pick the right wrap.
    let approx = (frame.time * f64::from(FPS)).round() as u64;
    (0..FRAMES)
        .filter(|n| (n * 7) % 256 == luma)
        .min_by_key(|n| n.abs_diff(approx))
        .expect("luma maps to a frame")
}

fn open() -> Player {
    Player::open(fixture(), Rc::new(AudioOutput::silent()), 64 << 20, || {}).expect("fixture opens")
}

/// Pump the player until it shows a frame different from `before`.
fn settle(p: &mut Player, before: Option<u64>) -> u64 {
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        p.update();
        if let Some(f) = p.frame() {
            let id = f.id;
            if Some(id) != before {
                // Drain anything else in flight for this epoch.
                std::thread::sleep(Duration::from_millis(5));
                p.update();
                return index_of(p.frame().unwrap());
            }
        }
        assert!(Instant::now() < deadline, "no frame arrived");
        std::thread::sleep(Duration::from_millis(2));
    }
}

fn current_id(p: &Player) -> Option<u64> {
    p.frame().map(|f| f.id)
}

#[test]
fn probe_reads_the_file() {
    let info = MediaInfo::probe(fixture()).unwrap();
    assert_eq!((info.width, info.height), (64, 48));
    assert!((info.fps - f64::from(FPS)).abs() < 1e-6);
    assert_eq!(info.audio.len(), 2);
    assert_eq!(info.total_frames(), FRAMES);
}

#[test]
fn exact_seeks_land_on_the_requested_frame() {
    let mut p = open();
    assert_eq!(settle(&mut p, None), 0);
    for n in [1, 44, 45, 46, 150, 299, 7, 200, 89, 90] {
        let before = current_id(&p);
        p.seek(n as f64 / f64::from(FPS), true);
        let got = settle(&mut p, before);
        assert_eq!(got, n, "seek to frame {n}");
        assert_eq!(p.frame_number(), n);
    }
}

#[test]
fn stepping_is_exact_in_both_directions_across_keyframes() {
    let mut p = open();
    settle(&mut p, None);
    p.seek(100.0 / f64::from(FPS), true);
    let mut at = settle(&mut p, None);
    // Back across two keyframes (90 and 45), then forward again.
    for _ in 0..70 {
        let before = current_id(&p);
        p.step(-1);
        let got = settle(&mut p, before);
        assert_eq!(got, at - 1, "step back from {at}");
        at = got;
    }
    for _ in 0..80 {
        let before = current_id(&p);
        p.step(1);
        let got = settle(&mut p, before);
        assert_eq!(got, at + 1, "step forward from {at}");
        at = got;
    }
}

#[test]
fn steps_stop_at_the_ends() {
    let mut p = open();
    assert_eq!(settle(&mut p, None), 0);
    p.step(-1);
    std::thread::sleep(Duration::from_millis(100));
    p.update();
    assert_eq!(index_of(p.frame().unwrap()), 0);
    p.seek(f64::from(SECONDS), true);
    let before = current_id(&p);
    let last = settle(&mut p, before);
    assert_eq!(last, FRAMES - 1);
    p.step(1);
    std::thread::sleep(Duration::from_millis(100));
    p.update();
    assert_eq!(index_of(p.frame().unwrap()), FRAMES - 1);
}

#[test]
fn playback_follows_the_clock() {
    let mut p = open();
    settle(&mut p, None);
    p.seek(2.0, true);
    let before = current_id(&p);
    settle(&mut p, before);
    p.play();
    let start = Instant::now();
    let mut shown = Vec::new();
    while start.elapsed() < Duration::from_millis(1200) {
        p.update();
        if let Some(f) = p.frame() {
            shown.push(index_of(f));
        }
        std::thread::sleep(Duration::from_millis(4));
    }
    let t = p.time();
    p.pause();
    // About a second played (allowing for start-up), and the picture
    // tracked the clock without going backwards.
    assert!((2.8..=3.3).contains(&t), "clock at {t}");
    assert!(
        shown.windows(2).all(|w| w[0] <= w[1]),
        "picture went backwards"
    );
    let last = *shown.last().unwrap();
    let expected = (t * f64::from(FPS)) as u64;
    assert!(
        last.abs_diff(expected) <= 2,
        "showing {last}, clock says {expected}"
    );
    // Pausing keeps the frame on screen and steps continue from it.
    let before = current_id(&p);
    p.step(1);
    assert_eq!(settle(&mut p, before), last + 1);
}

#[test]
fn playback_stops_on_the_last_frame() {
    let mut p = open();
    settle(&mut p, None);
    p.seek(f64::from(SECONDS) - 0.5, true);
    let before = current_id(&p);
    settle(&mut p, before);
    p.play();
    let deadline = Instant::now() + Duration::from_secs(3);
    while p.is_playing() {
        p.update();
        assert!(Instant::now() < deadline, "playback never ended");
        std::thread::sleep(Duration::from_millis(4));
    }
    assert_eq!(index_of(p.frame().unwrap()), FRAMES - 1);
    // Play again restarts from the top.
    p.play();
    std::thread::sleep(Duration::from_millis(300));
    p.update();
    assert!(p.time() < 1.0);
}

#[test]
fn video_catches_up_after_the_ui_stalls() {
    let mut p = open();
    settle(&mut p, None);
    p.play();
    let start = Instant::now();
    while start.elapsed() < Duration::from_millis(300) {
        p.update();
        std::thread::sleep(Duration::from_millis(4));
    }
    // A hidden window: nothing pumps the player for two seconds.
    std::thread::sleep(Duration::from_secs(2));
    let resumed = Instant::now();
    while resumed.elapsed() < Duration::from_millis(400) {
        p.update();
        std::thread::sleep(Duration::from_millis(4));
    }
    let shown = p.frame().unwrap().time;
    let t = p.time();
    p.pause();
    assert!(t > 2.4, "clock kept running: {t}");
    assert!(
        t - shown < 0.15,
        "picture {shown:.3}s is behind the clock {t:.3}s"
    );
}
