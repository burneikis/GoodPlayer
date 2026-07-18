"""Smoke tests: every module must at least import cleanly.

Regressions covered:
- main_window used ``Optional`` without importing it (NameError on init).
- hybrid_controller used bare ``video_decoder``/``audio_engine`` imports
  that fail when loaded as part of the ``src`` package.
"""

import importlib

import pytest

MODULES = [
    "src.audio_engine",
    "src.video_decoder",
    "src.playback_controller",
    "src.hybrid_controller",
    "src.native_player",
    "src.qt_native_player",
    "src.widgets",
    "src.theme",
    "src.main_window",
    "src.dual_mode_window",
]


@pytest.mark.parametrize("module", MODULES)
def test_module_imports(module, qapp):
    importlib.import_module(module)


def test_main_window_instantiates(qapp):
    """MainWindow.__init__ evaluates the Optional annotation at runtime."""
    from src.main_window import MainWindow

    window = MainWindow()
    window.close()
