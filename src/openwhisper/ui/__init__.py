"""PySide6 user interface for OpenWhisper.

Qt widgets are imported lazily so headless core/provider tests can import the
runtime controller without loading PySide6.
"""

from __future__ import annotations

from typing import Any

__all__ = ["MainWindow", "RecordingOverlay", "TrayController"]


def __getattr__(name: str) -> Any:
    if name == "MainWindow":
        from .main_window import MainWindow

        return MainWindow
    if name == "RecordingOverlay":
        from .overlay import RecordingOverlay

        return RecordingOverlay
    if name == "TrayController":
        from .tray import TrayController

        return TrayController
    raise AttributeError(name)
