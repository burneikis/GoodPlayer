from src.widgets import format_time


def test_zero():
    assert format_time(0.0) == "00:00.000"


def test_seconds_millis():
    assert format_time(5.5) == "00:05.500"


def test_minutes():
    assert format_time(65.25) == "01:05.250"


def test_hours():
    assert format_time(3661.5) == "1:01:01.500"


def test_over_an_hour_not_shown_as_minutes():
    # Regression: 75 minutes used to render as "75:00.000"
    assert format_time(4500.0) == "1:15:00.000"


def test_negative_clamped():
    assert format_time(-3.0) == "00:00.000"
