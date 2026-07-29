"""Shortcut gesture semantics independent of any global-hotkey library."""

from __future__ import annotations

import threading
from collections.abc import Callable
from enum import StrEnum


class ShortcutMode(StrEnum):
    TOGGLE = "toggle"
    PUSH_TO_TALK = "push-to-talk"


class ShortcutController:
    """Translate debounced key edges into dictation lifecycle callbacks.

    A push-to-talk release stops only a recording this controller started. This
    avoids accidentally stopping a recording started from the tray or another
    input source while the shortcut key happens to be pressed.
    """

    def __init__(
        self,
        mode: ShortcutMode | str,
        *,
        start_recording: Callable[[], object],
        stop_recording: Callable[[], object],
        is_recording: Callable[[], bool],
    ) -> None:
        self.mode = ShortcutMode(mode)
        self._start_recording = start_recording
        self._stop_recording = stop_recording
        self._is_recording = is_recording
        self._pressed = False
        self._owns_recording = False
        self._lock = threading.RLock()

    @property
    def is_pressed(self) -> bool:
        with self._lock:
            return self._pressed

    def pressed(self) -> None:
        """Handle a key-down edge; repeated operating-system events are ignored."""

        with self._lock:
            if self._pressed:
                return
            self._pressed = True
            if self.mode is ShortcutMode.PUSH_TO_TALK:
                if self._is_recording():
                    self._owns_recording = False
                    return
                try:
                    self._start_recording()
                except Exception:
                    # Keep the physical key debounced until release, but never
                    # turn that failed start into a stop on release.
                    self._owns_recording = False
                    raise
                self._owns_recording = True
                return

            if self._is_recording():
                self._stop_recording()
            else:
                self._start_recording()

    def released(self) -> None:
        """Handle a key-up edge for push-to-talk mode."""

        with self._lock:
            if not self._pressed:
                return
            self._pressed = False
            owns_recording = self._owns_recording
            self._owns_recording = False
            if self.mode is ShortcutMode.PUSH_TO_TALK and owns_recording and self._is_recording():
                self._stop_recording()

    # Common callback-library spellings. Keeping these aliases avoids putting
    # pynput-specific naming into the UI/runtime layer.
    on_press = pressed
    on_release = released

    def reset(self) -> None:
        """Forget key state after a hotkey backend is re-registered."""

        with self._lock:
            self._pressed = False
            self._owns_recording = False
