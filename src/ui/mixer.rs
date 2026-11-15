//! Mixer strips: a name, a level, an accent volume bar and a mute toggle.

use eframe::egui::{self, Align2, Color32, CornerRadius, FontId, Sense, vec2};

use super::timeline::ACCENT;

const CARD: Color32 = Color32::from_rgb(44, 44, 44);
const TRACK: Color32 = Color32::from_rgb(70, 70, 70);
const MUTED: Color32 = Color32::from_rgb(200, 70, 70);
const BAR_HEIGHT: f32 = 18.0;

/// One channel. `volume` is 0..=1. Returns true if anything changed.
pub fn channel(ui: &mut egui::Ui, label: &str, volume: &mut f32, muted: &mut bool) -> bool {
    let mut changed = false;
    egui::Frame::new()
        .fill(CARD)
        .corner_radius(CornerRadius::same(6))
        .inner_margin(egui::Margin::symmetric(10, 8))
        .show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.spacing_mut().item_spacing = vec2(6.0, 6.0);
            // Level on the right first, so a long name truncates instead of
            // pushing it out of the card.
            ui.allocate_ui_with_layout(
                vec2(ui.available_width(), 0.0),
                egui::Layout::right_to_left(egui::Align::Center),
                |ui| {
                    let level = if *muted {
                        "muted".to_string()
                    } else {
                        format!("{:.0}%", *volume * 100.0)
                    };
                    ui.label(
                        egui::RichText::new(level)
                            .monospace()
                            .color(Color32::from_gray(160)),
                    );
                    ui.with_layout(egui::Layout::left_to_right(egui::Align::Center), |ui| {
                        ui.add(egui::Label::new(egui::RichText::new(label).strong()).truncate());
                    });
                },
            );
            ui.horizontal(|ui| {
                let bar_width = ui.available_width() - BAR_HEIGHT - ui.spacing().item_spacing.x;
                changed |= volume_bar(ui, bar_width, volume, *muted);
                changed |= mute_toggle(ui, muted);
            });
        });
    changed
}

/// Drag or click to set, scroll to nudge, double-click for 100%.
fn volume_bar(ui: &mut egui::Ui, width: f32, volume: &mut f32, muted: bool) -> bool {
    let (rect, response) = ui.allocate_exact_size(vec2(width, BAR_HEIGHT), Sense::click_and_drag());
    let before = *volume;
    if response.double_clicked() {
        *volume = 1.0;
    } else if let Some(pos) = response.interact_pointer_pos() {
        *volume = ((pos.x - rect.left()) / rect.width()).clamp(0.0, 1.0);
    }
    if response.hovered() {
        let scroll = ui.input(|i| i.smooth_scroll_delta.y);
        if scroll != 0.0 {
            *volume = (*volume + scroll.signum() * 0.05).clamp(0.0, 1.0);
        }
        ui.ctx().set_cursor_icon(egui::CursorIcon::PointingHand);
    }

    let hot = response.hovered() || response.dragged();
    let thickness = if hot { 8.0 } else { 6.0 };
    let bar = egui::Rect::from_center_size(rect.center(), vec2(rect.width(), thickness));
    let painter = ui.painter_at(rect.expand(2.0));
    let fill = if muted {
        Color32::from_gray(110)
    } else {
        ACCENT
    };
    painter.rect_filled(bar, CornerRadius::same(4), TRACK);
    let x = bar.left() + bar.width() * *volume;
    painter.rect_filled(
        egui::Rect::from_min_max(bar.min, egui::pos2(x, bar.max.y)),
        CornerRadius::same(4),
        fill,
    );
    if hot {
        painter.circle_filled(
            egui::pos2(x.clamp(bar.left() + 5.0, bar.right() - 5.0), bar.center().y),
            6.0,
            fill,
        );
    }
    (*volume - before).abs() > f32::EPSILON
}

fn mute_toggle(ui: &mut egui::Ui, muted: &mut bool) -> bool {
    let (rect, response) = ui.allocate_exact_size(vec2(BAR_HEIGHT, BAR_HEIGHT), Sense::click());
    let response = response.on_hover_text(if *muted { "Unmute" } else { "Mute" });
    let fill = match (*muted, response.hovered()) {
        (true, _) => MUTED,
        (false, true) => Color32::from_gray(90),
        (false, false) => TRACK,
    };
    ui.painter().rect_filled(rect, CornerRadius::same(4), fill);
    ui.painter().text(
        rect.center(),
        Align2::CENTER_CENTER,
        "M",
        FontId::proportional(11.0),
        Color32::WHITE,
    );
    if response.clicked() {
        *muted = !*muted;
        return true;
    }
    false
}
