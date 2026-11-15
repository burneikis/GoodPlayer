use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::time::{Duration, Instant};

use crossbeam_channel::{Receiver, Sender, unbounded};
use eframe::egui::{self, Align2, Color32, CornerRadius, FontId, Key, Pos2, Rect, vec2};

use super::{format_time, mixer, timeline, video_view};
use crate::engine::{AudioOutput, Event, Player};
use crate::media::thumbs::Thumbnailer;
use crate::profile::Profile;

const CURSOR_HIDE_AFTER: Duration = Duration::from_secs(2);
/// Height of the seek bar overlay at the bottom of the video.
const CONTROLS_HEIGHT: f32 = 34.0;
/// Height of the gradient that keeps the overlay readable on bright video.
const CONTROLS_SHADE: f32 = 80.0;
/// Seconds for the overlay to fade in or out.
const CONTROLS_FADE: f32 = 0.2;
const THUMB_SLOTS: u32 = 240;
const STRIP_COUNT: u32 = 10;
const VOLUME_STEP: f32 = 0.05;
const PANEL_BG: Color32 = Color32::from_rgb(32, 32, 32);
const VIDEO_EXTENSIONS: &[&str] = &[
    "mp4", "mkv", "mov", "avi", "webm", "m4v", "flv", "wmv", "mpg", "mpeg", "ts", "m2ts", "mts",
    "3gp", "ogv",
];
#[cfg(target_os = "macos")]
const OPEN_KEY: &str = "Cmd+O";
#[cfg(not(target_os = "macos"))]
const OPEN_KEY: &str = "Ctrl+O";

pub struct App {
    profile: &'static Profile,
    ctx: egui::Context,
    output: Rc<AudioOutput>,
    player: Option<Player>,
    thumbs: Option<Thumbs>,
    gpu: bool,
    volume: f32,
    muted: bool,
    show_mixer: bool,
    show_strip: bool,
    show_help: bool,
    fullscreen: bool,
    toast: Option<(String, Instant, Duration)>,
    error: Option<String>,
    /// Some(was_playing) while the timeline is being dragged.
    scrub: Option<bool>,
    pointer_moved: Instant,
    picked: (Sender<PathBuf>, Receiver<PathBuf>),
}

impl App {
    pub fn new(
        cc: &eframe::CreationContext<'_>,
        profile: &'static Profile,
        file: Option<PathBuf>,
    ) -> Self {
        let gpu = video_view::install(cc);
        let get = |key: &str| cc.storage.and_then(|s| s.get_string(key));
        let volume = get("volume")
            .and_then(|v| v.parse().ok())
            .unwrap_or(1.0f32)
            .clamp(0.0, 1.0);
        let output = Rc::new(AudioOutput::open());
        output.set_master(volume);
        let mut app = Self {
            profile,
            ctx: cc.egui_ctx.clone(),
            output,
            player: None,
            thumbs: None,
            gpu,
            volume,
            muted: false,
            show_mixer: get("mixer").as_deref() == Some("1"),
            show_strip: profile.thumbnails && get("strip").as_deref() == Some("1"),
            show_help: false,
            fullscreen: false,
            toast: None,
            error: (!gpu).then(|| "GPU renderer unavailable: video cannot be shown".to_string()),
            scrub: None,
            pointer_moved: Instant::now(),
            picked: unbounded(),
        };
        if let Some(file) = file {
            app.open(&file);
        }
        app
    }

    fn open(&mut self, path: &Path) {
        // Tear down the old file first so its threads release the mixer.
        self.player = None;
        self.thumbs = None;
        let ctx = self.ctx.clone();
        let wake = move || ctx.request_repaint();
        match Player::open(
            path,
            Rc::clone(&self.output),
            self.profile.cache_mb << 20,
            wake.clone(),
        ) {
            Ok(player) => {
                if self.profile.thumbnails {
                    self.thumbs = Thumbnailer::spawn(&player.info, THUMB_SLOTS, wake)
                        .ok()
                        .map(Thumbs::new);
                }
                let title = format!("{} - {}", self.profile.name, player.info.file_name());
                self.ctx
                    .send_viewport_cmd(egui::ViewportCommand::Title(title));
                self.player = Some(player);
                self.error = None;
            }
            Err(e) => {
                log::error!("{e:#}");
                self.error = Some(format!("{e:#}"));
            }
        }
    }

    fn pick_file(&self) {
        let dialog = rfd::AsyncFileDialog::new()
            .set_title(format!("Open video - {}", self.profile.name))
            .add_filter("Video", VIDEO_EXTENSIONS)
            .add_filter("All files", &["*"])
            .pick_file();
        let tx = self.picked.0.clone();
        let ctx = self.ctx.clone();
        std::thread::spawn(move || {
            if let Some(file) = pollster::block_on(dialog) {
                let _ = tx.send(file.path().to_path_buf());
                ctx.request_repaint();
            }
        });
    }

    fn notify(&mut self, text: impl Into<String>, ms: u64) {
        self.toast = Some((text.into(), Instant::now(), Duration::from_millis(ms)));
    }

    fn set_volume(&mut self, volume: f32) {
        self.volume = ((volume * 20.0).round() / 20.0).clamp(0.0, 1.0);
        self.output.set_master(self.volume);
        self.notify(format!("Volume {}%", (self.volume * 100.0).round()), 700);
    }

    fn toggle_fullscreen(&mut self) {
        self.fullscreen = !self.fullscreen;
        self.ctx
            .send_viewport_cmd(egui::ViewportCommand::Fullscreen(self.fullscreen));
    }

    fn handle_keys(&mut self, ctx: &egui::Context) {
        // Keys belong to the player, never to a slider or checkbox that
        // happened to be clicked last.
        ctx.memory_mut(|m| {
            if let Some(id) = m.focused() {
                m.surrender_focus(id);
            }
        });
        let events = ctx.input(|i| i.events.clone());
        if !events.is_empty() {
            log::debug!("events {events:?}");
        }
        for event in events {
            match event {
                egui::Event::Key {
                    key,
                    pressed: true,
                    modifiers,
                    ..
                } => self.on_key(key, modifiers),
                egui::Event::Text(text) => {
                    if text == "?" {
                        self.show_help = !self.show_help;
                        continue;
                    }
                    let skip = match text.as_str() {
                        "[" => -5.0,
                        "]" => 10.0,
                        "{" => -15.0,
                        "}" => 30.0,
                        _ => continue,
                    };
                    if let Some(p) = &mut self.player {
                        p.skip(skip);
                        self.notify(format!("Skip {skip:+}s"), 600);
                    }
                }
                _ => {}
            }
        }
        for file in ctx.input(|i| i.raw.dropped_files.clone()) {
            let path = file.path().to_path_buf();
            if !path.as_os_str().is_empty() {
                self.open(&path);
            }
        }
        while let Ok(path) = self.picked.1.try_recv() {
            self.open(&path);
        }
    }

    fn on_key(&mut self, key: Key, modifiers: egui::Modifiers) {
        if modifiers.command && key == Key::O {
            self.pick_file();
            return;
        }
        if modifiers.any() && !modifiers.shift_only() {
            return;
        }
        match key {
            Key::F | Key::F11 => self.toggle_fullscreen(),
            Key::Escape if self.show_help => self.show_help = false,
            Key::Escape if self.fullscreen => self.toggle_fullscreen(),
            Key::A => {
                self.show_mixer = !self.show_mixer;
                self.notify(
                    if self.show_mixer {
                        "Mixer: On"
                    } else {
                        "Mixer: Off"
                    },
                    600,
                );
            }
            Key::T if self.profile.thumbnails => {
                self.show_strip = !self.show_strip;
                self.notify(
                    if self.show_strip {
                        "Timeline: On"
                    } else {
                        "Timeline: Off"
                    },
                    600,
                );
            }
            Key::ArrowUp => self.set_volume(self.volume + VOLUME_STEP),
            Key::ArrowDown => self.set_volume(self.volume - VOLUME_STEP),
            Key::M => {
                self.muted = !self.muted;
                self.output.set_muted(self.muted);
                self.notify(if self.muted { "Muted" } else { "Unmuted" }, 700);
            }
            _ => self.on_transport_key(key),
        }
    }

    fn on_transport_key(&mut self, key: Key) {
        let Some(p) = &mut self.player else { return };
        let toast = match key {
            Key::Space => {
                p.toggle();
                Some(if p.is_playing() { "Play" } else { "Pause" })
            }
            Key::ArrowRight | Key::Period => {
                p.step(1);
                None
            }
            Key::ArrowLeft | Key::Comma => {
                p.step(-1);
                None
            }
            Key::Home => {
                p.seek(0.0, true);
                Some("Start")
            }
            Key::End => {
                p.seek(p.info.duration, true);
                Some("End")
            }
            _ => None,
        };
        if let Some(t) = toast {
            self.notify(t, 600);
        }
    }

    fn controls(&mut self, ui: &mut egui::Ui) {
        let (position, duration) = self
            .player
            .as_ref()
            .map_or((0.0, 0.0), |p| (p.time(), p.info.duration));
        let bar = timeline::show(ui, position, duration, self.player.is_some());
        let Some(time) = bar.pointer_time else { return };
        let Some(player) = &mut self.player else {
            return;
        };

        if bar.response.drag_started() || (bar.response.clicked() && self.scrub.is_none()) {
            self.scrub = Some(player.is_playing());
            player.pause();
        }
        if bar.response.dragged() {
            player.seek(time, false);
        }
        if bar.response.drag_stopped() || bar.response.clicked() {
            player.seek(time, true);
            if self.scrub.take() == Some(true) {
                player.play();
            }
        }
        if bar.response.hovered() || bar.response.dragged() {
            self.hover_preview(ui, &bar.response.rect, time);
        }
    }

    fn hover_preview(&mut self, ui: &egui::Ui, bar: &Rect, time: f64) {
        let Some(player) = &self.player else { return };
        let duration = player.info.duration;
        let x = bar.left() + bar.width() * (time / duration.max(1e-9)) as f32;
        let painter = ui.ctx().layer_painter(egui::LayerId::new(
            egui::Order::Tooltip,
            egui::Id::unique("preview"),
        ));
        let label = format_time(time);
        let font = FontId::monospace(13.0);
        let slot = ((time / duration.max(1e-9)) * f64::from(THUMB_SLOTS)) as u32;
        let texture = self
            .thumbs
            .as_mut()
            .and_then(|t| t.get(slot.min(THUMB_SLOTS - 1)));
        let size = texture.map_or(vec2(90.0, 0.0), |t| {
            let s = t.size_vec2();
            s * (180.0 / s.x.max(s.y / 0.75))
        });
        let screen = ui.ctx().content_rect();
        let left = (x - size.x / 2.0).clamp(screen.left() + 4.0, screen.right() - size.x - 4.0);
        let top = bar.top() - size.y - 26.0;
        let image_rect = Rect::from_min_size(egui::pos2(left, top), size);
        if let Some(tex) = texture {
            painter.rect_filled(
                image_rect.expand(2.0),
                CornerRadius::same(3),
                Color32::from_gray(80),
            );
            painter.image(
                tex.id(),
                image_rect,
                Rect::from_min_max(Pos2::ZERO, egui::pos2(1.0, 1.0)),
                Color32::WHITE,
            );
        }
        let text_pos = egui::pos2(image_rect.center().x, image_rect.bottom() + 12.0);
        let galley = painter.layout_no_wrap(label, font, Color32::WHITE);
        let text_rect = Rect::from_center_size(text_pos, galley.size() + vec2(10.0, 4.0));
        painter.rect_filled(
            text_rect,
            CornerRadius::same(3),
            Color32::from_black_alpha(200),
        );
        painter.galley(text_rect.min + vec2(5.0, 2.0), galley, Color32::WHITE);
    }

    fn strip(&mut self, ui: &mut egui::Ui) {
        let (Some(player), Some(thumbs)) = (&mut self.player, &mut self.thumbs) else {
            return;
        };
        let duration = player.info.duration;
        let spacing = 4.0;
        let width =
            (ui.available_width() - spacing * (STRIP_COUNT as f32 - 1.0)) / STRIP_COUNT as f32;
        let height = (width * 9.0 / 16.0).min(110.0);
        ui.spacing_mut().item_spacing.x = spacing;
        ui.horizontal(|ui| {
            for i in 0..STRIP_COUNT {
                let slot =
                    ((f64::from(i) + 0.5) / f64::from(STRIP_COUNT) * f64::from(THUMB_SLOTS)) as u32;
                let (rect, response) =
                    ui.allocate_exact_size(vec2(width, height), egui::Sense::click());
                ui.painter()
                    .rect_filled(rect, CornerRadius::ZERO, Color32::BLACK);
                if let Some(tex) = thumbs.get(slot) {
                    let s = tex.size_vec2();
                    let fit = rect
                        .size()
                        .min(s * (rect.height() / s.y).min(rect.width() / s.x));
                    let img = Rect::from_center_size(rect.center(), fit);
                    ui.painter().image(
                        tex.id(),
                        img,
                        Rect::from_min_max(Pos2::ZERO, egui::pos2(1.0, 1.0)),
                        Color32::WHITE,
                    );
                }
                if response.hovered() {
                    ui.painter().rect_stroke(
                        rect,
                        CornerRadius::ZERO,
                        (2.0, timeline::ACCENT),
                        egui::StrokeKind::Inside,
                    );
                }
                if response.clicked() {
                    player.seek(
                        (f64::from(slot) + 0.5) / f64::from(THUMB_SLOTS) * duration,
                        true,
                    );
                }
            }
        });
    }

    /// The seek bar, floating over the bottom of the video and fading out
    /// with the pointer while playing.
    fn controls_overlay(&mut self, ui: &mut egui::Ui, video: Rect, fade: f32) {
        if fade <= 0.0 {
            return;
        }
        let area = controls_rect(video);
        let shade = Rect::from_min_max(
            egui::pos2(area.left(), area.bottom() - CONTROLS_SHADE),
            area.right_bottom(),
        );
        let top = Color32::TRANSPARENT;
        let bottom = Color32::from_black_alpha((170.0 * fade) as u8);
        let mut mesh = egui::Mesh::default();
        mesh.colored_vertex(shade.left_top(), top);
        mesh.colored_vertex(shade.right_top(), top);
        mesh.colored_vertex(shade.right_bottom(), bottom);
        mesh.colored_vertex(shade.left_bottom(), bottom);
        mesh.add_triangle(0, 1, 2);
        mesh.add_triangle(0, 2, 3);
        ui.painter_at(video).add(mesh);

        let mut child = ui.new_child(
            egui::UiBuilder::new()
                .id_salt("controls")
                .max_rect(area.shrink2(vec2(10.0, 6.0))),
        );
        child.multiply_opacity(fade);
        self.controls(&mut child);
    }

    fn mixer(&mut self, ui: &mut egui::Ui) {
        ui.spacing_mut().item_spacing.y = 8.0;
        ui.add_space(2.0);
        ui.label(egui::RichText::new("Audio Mixer").strong().size(15.0));
        egui::ScrollArea::vertical()
            .auto_shrink(false)
            .show(ui, |ui| {
                if mixer::channel(ui, "Master", &mut self.volume, &mut self.muted) {
                    self.output.set_master(self.volume);
                    self.output.set_muted(self.muted);
                }
                let Some(player) = &self.player else { return };
                if player.tracks.is_empty() {
                    ui.weak("No audio tracks");
                }
                for (control, info) in player.tracks.iter().zip(&player.info.audio) {
                    let mut gain = control.gain();
                    let mut muted = control.muted();
                    if mixer::channel(ui, &info.label, &mut gain, &mut muted) {
                        control.set_gain(gain);
                        control.set_muted(muted);
                    }
                }
            });
    }

    fn keybinds(&self) -> Vec<(&'static str, &'static str)> {
        let mut binds = vec![
            ("Space", "Play / pause"),
            ("Left / Right  , / .", "Step one frame"),
            ("[ / ]", "Skip -5 s / +10 s"),
            ("{ / }", "Skip -15 s / +30 s"),
            ("Home / End", "Start / end"),
            ("Up / Down", "Volume"),
            ("M", "Mute"),
            ("A", "Audio mixer"),
        ];
        if self.profile.thumbnails {
            binds.push(("T", "Thumbnail strip"));
        }
        binds.extend([
            ("F / F11", "Fullscreen"),
            ("Esc", "Leave fullscreen"),
            (OPEN_KEY, "Open a file"),
            ("?", "Toggle this help"),
        ]);
        binds
    }

    fn help_overlay(&self, painter: &egui::Painter, area: Rect) {
        let font = FontId::proportional(15.0);
        let key_font = FontId::monospace(14.0);
        let rows: Vec<_> = self
            .keybinds()
            .into_iter()
            .map(|(key, action)| {
                (
                    painter.layout_no_wrap(key.into(), key_font.clone(), timeline::ACCENT),
                    painter.layout_no_wrap(action.into(), font.clone(), Color32::WHITE),
                )
            })
            .collect();
        let title = painter.layout_no_wrap(
            "Keyboard shortcuts".into(),
            FontId::proportional(18.0),
            Color32::WHITE,
        );
        let (pad, gap, row_h) = (18.0, 24.0, 22.0);
        let key_w = rows.iter().map(|r| r.0.size().x).fold(0.0, f32::max);
        let action_w = rows.iter().map(|r| r.1.size().x).fold(0.0, f32::max);
        let width = (key_w + gap + action_w).max(title.size().x) + pad * 2.0;
        let height = title.size().y + 12.0 + row_h * rows.len() as f32 + pad * 2.0;
        let panel = Rect::from_center_size(area.center(), vec2(width, height));
        painter.rect(
            panel,
            CornerRadius::same(12),
            Color32::from_black_alpha(220),
            (1.0, Color32::from_gray(85)),
            egui::StrokeKind::Inside,
        );
        let mut y = panel.top() + pad;
        let title_h = title.size().y;
        painter.galley(egui::pos2(panel.left() + pad, y), title, Color32::WHITE);
        y += title_h + 12.0;
        for (key, action) in rows {
            let x = panel.left() + pad;
            let key_y = y + (row_h - key.size().y) / 2.0;
            let action_y = y + (row_h - action.size().y) / 2.0;
            painter.galley(egui::pos2(x, key_y), key, Color32::WHITE);
            painter.galley(
                egui::pos2(x + key_w + gap, action_y),
                action,
                Color32::WHITE,
            );
            y += row_h;
        }
    }

    fn video_area(&mut self, ui: &mut egui::Ui) {
        let rect = ui.max_rect();
        let response = ui.allocate_rect(rect, egui::Sense::click());
        let painter = ui.painter_at(rect);
        painter.rect_filled(rect, CornerRadius::ZERO, Color32::BLACK);

        let moved = ui.input(|i| i.pointer.delta() != egui::Vec2::ZERO || i.pointer.any_down());
        if moved {
            self.pointer_moved = Instant::now();
        }
        let playing = self.player.as_ref().is_some_and(Player::is_playing);
        let idle = self.pointer_moved.elapsed() >= CURSOR_HIDE_AFTER;
        let over_controls = ui.rect_contains_pointer(controls_rect(rect));
        let show_controls = !playing || !idle || over_controls || self.scrub.is_some();
        let controls_fade = ui.ctx().animate_bool_with_time(
            egui::Id::unique("controls_fade"),
            show_controls,
            CONTROLS_FADE,
        );

        if let Some(frame) = self
            .player
            .as_ref()
            .and_then(|p| p.frame())
            .filter(|_| self.gpu)
        {
            let aspect = frame.width as f32 / frame.height.max(1) as f32;
            let size = if rect.width() / rect.height() > aspect {
                vec2(rect.height() * aspect, rect.height())
            } else {
                vec2(rect.width(), rect.width() / aspect)
            };
            let video = Rect::from_center_size(rect.center(), size);
            video_view::paint(&painter, video, frame.clone());
        }
        if response.double_clicked() {
            self.toggle_fullscreen();
        }

        let small = FontId::monospace(14.0);
        let shadowed = |pos: Pos2, align: Align2, text: String| {
            painter.text(
                pos + vec2(1.0, 1.0),
                align,
                &text,
                small.clone(),
                Color32::from_black_alpha(220),
            );
            painter.text(pos, align, text, small.clone(), Color32::WHITE);
        };
        let margin = 10.0;
        // Labels ride above the timeline while it is shown.
        let lift = CONTROLS_HEIGHT * controls_fade;
        if let Some(p) = &self.player {
            shadowed(
                rect.left_bottom() + vec2(margin, -margin - lift),
                Align2::LEFT_BOTTOM,
                format!(
                    "{} / {}",
                    format_time(p.time()),
                    format_time(p.info.duration)
                ),
            );
            if self.profile.frame_counter {
                shadowed(
                    rect.right_bottom() + vec2(-margin, -margin - lift),
                    Align2::RIGHT_BOTTOM,
                    format!("Frame {} / {}", p.frame_number(), p.info.total_frames()),
                );
            }
        } else {
            let welcome = Rect::from_center_size(rect.center(), vec2(360.0, 140.0));
            let hover = ui.rect_contains_pointer(welcome);
            let colour = if hover {
                Color32::WHITE
            } else {
                Color32::from_gray(170)
            };
            painter.rect(
                welcome,
                CornerRadius::same(12),
                Color32::from_black_alpha(200),
                (
                    2.0,
                    if hover {
                        Color32::from_gray(140)
                    } else {
                        Color32::from_gray(85)
                    },
                ),
                egui::StrokeKind::Inside,
            );
            painter.text(
                welcome.center(),
                Align2::CENTER_CENTER,
                format!("Click, drop a file, or press {OPEN_KEY}"),
                FontId::proportional(19.0),
                colour,
            );
            painter.text(
                welcome.center_bottom() + vec2(0.0, 16.0),
                Align2::CENTER_TOP,
                "Press ? for keyboard shortcuts",
                FontId::proportional(14.0),
                Color32::from_gray(140),
            );
            if hover {
                ui.ctx().set_cursor_icon(egui::CursorIcon::PointingHand);
                if response.clicked() {
                    self.pick_file();
                }
            }
        }

        if let Some(error) = &self.error {
            painter.text(
                rect.center_top() + vec2(0.0, 40.0),
                Align2::CENTER_TOP,
                error,
                FontId::proportional(16.0),
                Color32::LIGHT_RED,
            );
        }
        if let Some((text, since, ttl)) = &self.toast {
            if since.elapsed() < *ttl {
                let galley = painter.layout_no_wrap(
                    text.clone(),
                    FontId::proportional(18.0),
                    Color32::WHITE,
                );
                let pos = rect.right_top() + vec2(-margin - galley.size().x - 20.0, margin);
                let bg = Rect::from_min_size(pos, galley.size() + vec2(20.0, 12.0));
                painter.rect_filled(bg, CornerRadius::same(8), Color32::from_black_alpha(180));
                painter.galley(bg.min + vec2(10.0, 6.0), galley, Color32::WHITE);
                ui.ctx().request_repaint_after(*ttl - since.elapsed());
            } else {
                self.toast = None;
            }
        }

        if self.show_help {
            self.help_overlay(&painter, rect);
        }

        // Drawn last so the timeline sits on top of the picture and wins
        // pointer hit-testing over the video area.
        self.controls_overlay(ui, rect, controls_fade);

        // Hide the pointer over the picture while playing and idle.
        if playing && response.hovered() && idle {
            ui.ctx().set_cursor_icon(egui::CursorIcon::None);
        }
    }
}

impl eframe::App for App {
    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        let ctx = ui.ctx().clone();
        self.handle_keys(&ctx);
        if let Some(player) = &mut self.player {
            match player.update() {
                Some(Event::ReachedEnd) => self.notify("End", 700),
                Some(Event::ReachedStart) => self.notify("Start", 700),
                None => {}
            }
        }
        if let Some(t) = &mut self.thumbs {
            t.poll(&ctx);
        }

        if self.show_strip && self.player.is_some() {
            egui::Panel::bottom("strip")
                .frame(
                    egui::Frame::new()
                        .fill(Color32::from_rgb(20, 20, 20))
                        .inner_margin(egui::Margin::same(5)),
                )
                .show(ui, |ui| self.strip(ui));
        }
        if self.show_mixer {
            egui::Panel::right("mixer")
                .exact_size(200.0)
                .resizable(false)
                .frame(
                    egui::Frame::new()
                        .fill(PANEL_BG)
                        .inner_margin(egui::Margin::same(10)),
                )
                .show(ui, |ui| self.mixer(ui));
        }
        egui::CentralPanel::no_frame().show(ui, |ui| self.video_area(ui));

        if self.player.as_ref().is_some_and(Player::is_playing) {
            // Every vsync: the clock moves continuously.
            ctx.request_repaint();
        }
    }

    fn save(&mut self, storage: &mut dyn eframe::Storage) {
        storage.set_string("volume", self.volume.to_string());
        storage.set_string("mixer", if self.show_mixer { "1" } else { "0" }.into());
        storage.set_string("strip", if self.show_strip { "1" } else { "0" }.into());
    }
}

fn controls_rect(video: Rect) -> Rect {
    Rect::from_min_max(
        egui::pos2(video.left(), video.bottom() - CONTROLS_HEIGHT),
        video.right_bottom(),
    )
}

/// Thumbnail textures, requested on demand from the background decoder.
struct Thumbs {
    worker: Thumbnailer,
    textures: HashMap<u32, egui::TextureHandle>,
    requested: HashSet<u32>,
}

impl Thumbs {
    fn new(worker: Thumbnailer) -> Self {
        Self {
            worker,
            textures: HashMap::new(),
            requested: HashSet::new(),
        }
    }

    fn poll(&mut self, ctx: &egui::Context) {
        for t in self.worker.results() {
            let image = egui::ColorImage::from_rgba_unmultiplied(
                [t.width as usize, t.height as usize],
                &t.rgba,
            );
            let texture = ctx.load_texture(
                format!("thumb{}", t.slot),
                image,
                egui::TextureOptions::LINEAR,
            );
            self.textures.insert(t.slot, texture);
        }
    }

    fn get(&mut self, slot: u32) -> Option<&egui::TextureHandle> {
        if !self.textures.contains_key(&slot) && self.requested.insert(slot) {
            self.worker.request(slot);
        }
        self.textures.get(&slot)
    }
}
