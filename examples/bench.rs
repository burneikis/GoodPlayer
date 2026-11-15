//! Headless engine benchmark: `cargo run --release --example bench -- FILE`.

use std::rc::Rc;
use std::time::{Duration, Instant};

use goodplayer::engine::{AudioOutput, Player};

fn wait(p: &mut Player, before: Option<u64>) -> Duration {
    let t = Instant::now();
    loop {
        p.update();
        if p.frame().map(|f| f.id) != before && p.frame().is_some() {
            return t.elapsed();
        }
        assert!(t.elapsed() < Duration::from_secs(10), "timeout");
        std::thread::sleep(Duration::from_micros(500));
    }
}

fn main() {
    env_logger::init();
    goodplayer::media::init();
    let path = std::env::args().nth(1).expect("usage: bench FILE");
    let t = Instant::now();
    // BENCH_DEVICE=1 uses the real audio device, muted.
    let output = if std::env::var_os("BENCH_DEVICE").is_some() {
        let out = AudioOutput::open();
        out.set_master(0.0);
        out
    } else {
        AudioOutput::silent()
    };
    let mut p = Player::open(path.as_ref(), Rc::new(output), 512 << 20, || {}).unwrap();
    let first = wait(&mut p, None);
    println!(
        "open {:?}, first frame {:?} ({} audio tracks, {:.2} fps, {:.0}s)",
        t.elapsed() - first,
        first,
        p.info.audio.len(),
        p.info.fps,
        p.info.duration
    );

    let d = p.info.duration;
    for frac in [0.5, 0.1, 0.9, 0.3] {
        let b = p.frame().map(|f| f.id);
        p.seek(d * frac + 0.73, true);
        println!(
            "exact seek to {:.1}s: {:?}",
            d * frac + 0.73,
            wait(&mut p, b)
        );
    }
    let mut back = Vec::new();
    for _ in 0..150 {
        let b = p.frame().map(|f| f.id);
        p.step(-1);
        back.push(wait(&mut p, b));
    }
    back.sort();
    println!(
        "150 steps back: median {:?}, p95 {:?}, max {:?}",
        back[75], back[142], back[149]
    );
    let mut fwd = Vec::new();
    for _ in 0..60 {
        let b = p.frame().map(|f| f.id);
        p.step(1);
        fwd.push(wait(&mut p, b));
    }
    fwd.sort();
    println!("60 steps forward: median {:?}, max {:?}", fwd[30], fwd[59]);

    let cpu0 = cpu();
    let t0 = p.time();
    p.play();
    let start = Instant::now();
    let (mut shown, mut last_id, mut late) = (0u32, 0u64, 0u32);
    while start.elapsed() < Duration::from_secs(5) {
        p.update();
        if let Some(f) = p.frame()
            && f.id != last_id
        {
            shown += 1;
            last_id = f.id;
            if p.time() - f.time > 2.0 / p.info.fps {
                late += 1;
            }
        }
        std::thread::sleep(Duration::from_micros(4167)); // ~240 Hz "vsync"
    }
    let played = p.time();
    p.pause();
    println!(
        "5s playback: {shown} frames shown for {:.0} expected, {late} late, cpu {:.0}%",
        (played - t0) * p.info.fps,
        (cpu() - cpu0) / 5.0 * 100.0
    );
}

fn cpu() -> f64 {
    let s = std::fs::read_to_string("/proc/self/stat").unwrap_or_default();
    let f: Vec<&str> = s
        .rsplit(')')
        .next()
        .unwrap_or("")
        .split_whitespace()
        .collect();
    let ticks: f64 = f.get(11).and_then(|v| v.parse().ok()).unwrap_or(0.0)
        + f.get(12).and_then(|v| v.parse().ok()).unwrap_or(0.0);
    ticks / 100.0
}
