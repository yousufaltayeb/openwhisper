"""Desktop-aware text insertion with safe X11/Wayland and clipboard routing."""

from __future__ import annotations

import os
import shutil
import subprocess
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class DesktopSession(StrEnum):
    X11 = "x11"
    WAYLAND = "wayland"
    UNKNOWN = "unknown"

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> DesktopSession:
        env = os.environ if environment is None else environment
        session_type = env.get("XDG_SESSION_TYPE", "").casefold()
        if session_type == "wayland":
            return cls.WAYLAND
        if session_type == "x11":
            return cls.X11
        if env.get("WAYLAND_DISPLAY"):
            return cls.WAYLAND
        if env.get("DISPLAY"):
            return cls.X11
        return cls.UNKNOWN


class InsertionMethod(StrEnum):
    ATSPI = "atspi"
    X11 = "x11"
    WAYLAND = "wayland"
    CLIPBOARD = "clipboard"


@dataclass(frozen=True, slots=True)
class InsertionResult:
    method: InsertionMethod
    warning: str | None = None


class DirectTextBackend(Protocol):
    def available(self) -> bool: ...

    def insert(self, text: str) -> None: ...


class ClipboardBackend(Protocol):
    def copy(self, text: str) -> None: ...


class NotificationSink(Protocol):
    def notify(self, title: str, message: str) -> None: ...


Runner = Callable[..., object]
Which = Callable[[str], str | None]


class X11TextBackend:
    """Use xdotool on X11, falling back when it cannot safely type RTL text."""

    supports_rtl = False

    def __init__(
        self,
        *,
        executable: str = "xdotool",
        which: Which = shutil.which,
        runner: Runner = subprocess.run,
    ) -> None:
        self.executable = executable
        self._which = which
        self._runner = runner

    def available(self) -> bool:
        return self._which(self.executable) is not None

    def insert(self, text: str) -> None:
        # ``--`` and a list of arguments keep transcript text out of shell
        # parsing. xdotool's keyboard mapping is not dependable for Arabic, so
        # DesktopTextInserter selects the clipboard for RTL before reaching here.
        self._runner(
            [self.executable, "type", "--clearmodifiers", "--", text],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


class WaylandTextBackend:
    """Use wtype under Wayland where the compositor permits virtual input."""

    supports_rtl = True

    def __init__(
        self,
        *,
        executable: str = "wtype",
        which: Which = shutil.which,
        runner: Runner = subprocess.run,
    ) -> None:
        self.executable = executable
        self._which = which
        self._runner = runner

    def available(self) -> bool:
        return self._which(self.executable) is not None

    def insert(self, text: str) -> None:
        self._runner(
            [self.executable, "--", text],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


class DesktopTextInserter:
    """Choose direct insertion, then clipboard fallback, without mutating text.

    Backends may optionally expose ``supports_text(text) -> bool`` or a boolean
    ``supports_rtl`` attribute. This gives command-based adapters a way to opt
    out of scripts they cannot preserve, while simple injected backends remain
    compatible and receive the exact input string unchanged.
    """

    clipboard_warning = "Direct insertion is unavailable; the transcript was copied."

    def __init__(
        self,
        *,
        clipboard: ClipboardBackend,
        session: DesktopSession | None = None,
        x11: DirectTextBackend | None = None,
        wayland: DirectTextBackend | None = None,
        notifier: NotificationSink | None = None,
    ) -> None:
        self._session = session or DesktopSession.from_environment()
        self._clipboard = clipboard
        self._x11 = x11
        self._wayland = wayland
        self._notifier = notifier

    @property
    def session(self) -> DesktopSession:
        return self._session

    def insert(self, text: str) -> InsertionResult:
        if not text:
            raise ValueError("cannot insert empty text")
        backend, method = self._direct_backend()
        if backend is not None and self._can_insert_directly(backend, text):
            try:
                backend.insert(text)
                return InsertionResult(method=method)
            except Exception:
                # Focus can change or a compositor can reject automation between
                # availability and insert. Clipboard remains the reliable path.
                pass

        self._clipboard.copy(text)
        if self._notifier is not None:
            try:
                self._notifier.notify("Transcript copied", self.clipboard_warning)
            except Exception:
                # Notification failures must not make a successful clipboard
                # copy look like a lost dictation.
                pass
        return InsertionResult(
            method=InsertionMethod.CLIPBOARD,
            warning=self.clipboard_warning,
        )

    def _can_insert_directly(self, backend: DirectTextBackend, text: str) -> bool:
        try:
            if not backend.available():
                return False
            supports_text = getattr(backend, "supports_text", None)
            if callable(supports_text):
                return bool(supports_text(text))
            if contains_rtl(text) and getattr(backend, "supports_rtl", True) is False:
                return False
            return True
        except Exception:
            return False

    def _direct_backend(
        self,
    ) -> tuple[DirectTextBackend | None, InsertionMethod]:
        if self._session is DesktopSession.X11:
            return self._x11, InsertionMethod.X11
        if self._session is DesktopSession.WAYLAND:
            return self._wayland, InsertionMethod.WAYLAND
        return None, InsertionMethod.CLIPBOARD


def contains_rtl(text: str) -> bool:
    """Return whether text contains a right-to-left Unicode character.

    Detection is used only to choose a backend; no bidi controls are added,
    removed, reversed, or normalized. That preserves mixed Arabic/English and
    code-switching transcripts exactly as the provider produced them.
    """

    return any(unicodedata.bidirectional(character) in {"R", "AL", "AN"} for character in text)
