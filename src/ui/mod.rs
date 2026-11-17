//! The window: video, overlays, timeline, mixer and keyboard handling.

mod app;
mod mixer;
mod timeline;
mod video_view;

use std::path::PathBuf;

use eframe::egui;

use crate::profile::Profile;

pub fn run(profile: &'static Profile, file: Option<PathBuf>) -> anyhow::Result<()> {
    let mut viewport = egui::ViewportBuilder::default()
        .with_title(profile.name)
        .with_app_id(profile.app_id)
        .with_inner_size([1280.0, 720.0])
        .with_min_inner_size([480.0, 300.0])
        .with_drag_and_drop(true);
    if let Ok(icon) = eframe::icon_data::from_png_bytes(profile.icon_png) {
        viewport = viewport.with_icon(icon);
    }
    let mut options = eframe::NativeOptions {
        viewport,
        // Don't restore the last window state: it would reopen fullscreen
        // while `App::fullscreen` starts false, and i3 places the window anyway.
        persist_window: false,
        ..Default::default()
    };
    // No vsync: the app paces repaints to the video's frame times. Vsync'd
    // presentation on X11/NVIDIA without a compositor bunches large windows'
    // frames (several 33 ms gaps, then a burst), halving the visible
    // framerate. Falls back to vsync where no-vsync is unsupported.
    options.wgpu_options.surface.present_mode = eframe::egui_wgpu::wgpu::PresentMode::AutoNoVsync;
    eframe::run_native(
        profile.app_id,
        options,
        Box::new(move |cc| Ok(Box::new(app::App::new(cc, profile, file)))),
    )
    .map_err(|e| anyhow::anyhow!("{e}"))
}

/// `MM:SS.mmm`, or `H:MM:SS.mmm` from an hour on.
pub fn format_time(seconds: f64) -> String {
    let ms = (seconds.max(0.0) * 1000.0).round() as u64;
    let (h, m, s, ms) = (ms / 3_600_000, ms / 60_000 % 60, ms / 1000 % 60, ms % 1000);
    if h > 0 {
        format!("{h}:{m:02}:{s:02}.{ms:03}")
    } else {
        format!("{m:02}:{s:02}.{ms:03}")
    }
}

#[cfg(test)]
mod tests {
    use super::format_time;

    #[test]
    fn formats_minutes_and_hours() {
        assert_eq!(format_time(0.0), "00:00.000");
        assert_eq!(format_time(61.5), "01:01.500");
        assert_eq!(format_time(3725.25), "1:02:05.250");
        assert_eq!(format_time(-3.0), "00:00.000");
    }
}
