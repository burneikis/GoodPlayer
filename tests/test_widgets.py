from PyQt6.QtCore import QPointF, Qt

from src.widgets import ClickableSlider


def _make_slider(qapp, orientation):
    slider = ClickableSlider(orientation)
    slider.setRange(0, 100)
    slider.resize(200, 200)
    return slider


def test_horizontal_click_maps_x(qapp):
    slider = _make_slider(qapp, Qt.Orientation.Horizontal)
    slider._update_value_from_pos(QPointF(100.0, 10.0))
    assert slider.value() == 50


def test_horizontal_click_clamped(qapp):
    slider = _make_slider(qapp, Qt.Orientation.Horizontal)
    slider._update_value_from_pos(QPointF(-50.0, 0.0))
    assert slider.value() == 0
    slider._update_value_from_pos(QPointF(500.0, 0.0))
    assert slider.value() == 100


def test_vertical_click_uses_y(qapp):
    """Regression: vertical sliders used the x coordinate for position."""
    slider = _make_slider(qapp, Qt.Orientation.Vertical)
    # Click near the bottom (y large) -> low value
    slider._update_value_from_pos(QPointF(0.0, 180.0))
    assert slider.value() == 10
    # Click near the top (y small) -> high value
    slider._update_value_from_pos(QPointF(0.0, 20.0))
    assert slider.value() == 90


def test_is_slider_down_tracks_dragging(qapp):
    slider = _make_slider(qapp, Qt.Orientation.Horizontal)
    assert not slider.isSliderDown()
    slider._dragging = True
    assert slider.isSliderDown()
