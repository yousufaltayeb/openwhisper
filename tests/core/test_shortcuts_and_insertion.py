from __future__ import annotations

from dataclasses import dataclass

import pytest

from openwhisper.core.insertion import (
    DesktopSession,
    DesktopTextInserter,
    InsertionMethod,
    X11TextBackend,
    contains_rtl,
)
from openwhisper.core.shortcuts import ShortcutController, ShortcutMode


@dataclass
class Recorder:
    recording: bool = False
    starts: int = 0
    stops: int = 0

    def start(self) -> None:
        self.starts += 1
        self.recording = True

    def stop(self) -> None:
        self.stops += 1
        self.recording = False


class Clipboard:
    def __init__(self) -> None:
        self.value = ""

    def copy(self, text: str) -> None:
        self.value = text


class Backend:
    def __init__(self, *, available: bool = True, fails: bool = False) -> None:
        self.is_available = available
        self.fails = fails
        self.values: list[str] = []

    def available(self) -> bool:
        return self.is_available

    def insert(self, text: str) -> None:
        if self.fails:
            raise RuntimeError("focus changed")
        self.values.append(text)


def test_shortcut_modes_are_debounced_and_ptt_does_not_stop_external_recording() -> None:
    recorder = Recorder()
    ptt = ShortcutController(
        ShortcutMode.PUSH_TO_TALK,
        start_recording=recorder.start,
        stop_recording=recorder.stop,
        is_recording=lambda: recorder.recording,
    )
    ptt.pressed()
    ptt.pressed()
    ptt.released()
    assert (recorder.starts, recorder.stops) == (1, 1)

    recorder.recording = True
    ptt.pressed()
    ptt.released()
    assert recorder.stops == 1

    toggle = ShortcutController(
        "toggle",
        start_recording=recorder.start,
        stop_recording=recorder.stop,
        is_recording=lambda: recorder.recording,
    )
    recorder.recording = False
    toggle.on_press()
    toggle.on_release()
    toggle.on_press()
    assert (recorder.starts, recorder.stops) == (2, 2)


def test_desktop_insertion_uses_exact_rtl_text_and_falls_back_safely() -> None:
    text = "مرحباً OpenWhisper 123"
    clipboard = Clipboard()
    x11 = Backend()
    direct = DesktopTextInserter(
        session=DesktopSession.X11,
        x11=x11,
        clipboard=clipboard,
    )
    assert direct.insert(text).method is InsertionMethod.X11
    assert x11.values == [text]
    assert clipboard.value == ""
    assert contains_rtl(text)

    unsupported_rtl = X11TextBackend(which=lambda _name: "/usr/bin/xdotool")
    fallback = DesktopTextInserter(
        session=DesktopSession.X11,
        x11=unsupported_rtl,
        clipboard=clipboard,
    )
    result = fallback.insert(text)
    assert result.method is InsertionMethod.CLIPBOARD
    assert clipboard.value == text

    broken = DesktopTextInserter(
        session=DesktopSession.WAYLAND,
        wayland=Backend(fails=True),
        clipboard=clipboard,
    )
    assert broken.insert(text).method is InsertionMethod.CLIPBOARD
    assert clipboard.value == text


def test_desktop_session_detection_and_command_backend_arguments() -> None:
    assert (
        DesktopSession.from_environment({"XDG_SESSION_TYPE": "x11", "WAYLAND_DISPLAY": "x"})
        is DesktopSession.X11
    )
    assert (
        DesktopSession.from_environment({"WAYLAND_DISPLAY": "wayland-0"}) is DesktopSession.WAYLAND
    )

    calls: list[tuple[list[str], dict[str, object]]] = []
    backend = X11TextBackend(
        which=lambda _name: "/usr/bin/xdotool",
        runner=lambda args, **kwargs: calls.append((args, kwargs)),
    )
    backend.insert("$(not a shell command)")
    assert calls[0][0][-2:] == ["--", "$(not a shell command)"]


def test_inserter_rejects_empty_text() -> None:
    with pytest.raises(ValueError, match="empty"):
        DesktopTextInserter(session=DesktopSession.UNKNOWN, clipboard=Clipboard()).insert("")
