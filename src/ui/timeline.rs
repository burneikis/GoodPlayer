//! The seek bar.

use eframe::egui::{self, Color32, CornerRadius, Sense, Stroke, pos2, vec2};

pub const ACCENT: Color32 = Color32::from_rgb(42, 130, 218);
const TRACK: Color32 = Color32::from_rgb(70, 70, 70);
const HEIGHT: f32 = 22.0;

pub struct Timeline {
    pub response: egui::Response,
    /// Media time under the pointer, while hovering or dragging.
    pub pointer_time: Option<f64>,
}

/// Draw the bar for `position` of `duration` and report interaction.
pub fn show(ui: &mut egui::Ui, position: f64, duration: f64, enabled: bool) -> Timeline {
    let width = ui.available_width();
    let sense = if enabled {
        Sense::click_and_drag()
    } else {
        Sense::hover()
    };
    let (rect, response) = ui.allocate_exact_size(vec2(width, HEIGHT), sense);
    let bar = egui::Rect::from_center_size(rect.center(), vec2(rect.width() - 12.0, 6.0));
    let fraction = if duration > 0.0 {
        (position / duration).clamp(0.0, 1.0) as f32
    } else {
        0.0
    };
    let x = bar.left() + bar.width() * fraction;

    let painter = ui.painter_at(rect);
    painter.rect_filled(bar, CornerRadius::same(3), TRACK);
    painter.rect_filled(
        egui::Rect::from_min_max(bar.min, pos2(x, bar.max.y)),
        CornerRadius::same(3),
        ACCENT,
    );
    let hot = response.hovered() || response.dragged();
    let radius = if hot { 7.0 } else { 5.5 };
    painter.circle(
        pos2(x, bar.center().y),
        radius,
        ACCENT,
        Stroke::new(1.0, Color32::WHITE),
    );

    let pointer_time = (enabled && duration > 0.0)
        .then(|| response.interact_pointer_pos().or(response.hover_pos()))
        .flatten()
        .map(|p| f64::from(((p.x - bar.left()) / bar.width()).clamp(0.0, 1.0)) * duration);
    if pointer_time.is_some() {
        ui.ctx().set_cursor_icon(egui::CursorIcon::PointingHand);
    }
    Timeline {
        response,
        pointer_time,
    }
}
