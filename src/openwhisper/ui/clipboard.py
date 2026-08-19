"""Qt-thread-marshalled clipboard access for the private engine process."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import QApplication


class QtClipboardBridge(QObject):
    """Expose QClipboard safely to provider workers without touching Qt off-thread."""

    _copy_requested = Signal(str, object)
    _read_requested = Signal(object)

    def __init__(self, application: QApplication) -> None:
        super().__init__(application)
        self._application = application
        blocking = Qt.ConnectionType.BlockingQueuedConnection
        self._copy_requested.connect(self._copy_on_application_thread, blocking)
        self._read_requested.connect(self._read_on_application_thread, blocking)

    def copy(self, text: str) -> None:
        outcome: dict[str, Any] = {}
        if self._on_application_thread():
            self._copy_on_application_thread(text, outcome)
        else:
            self._copy_requested.emit(text, outcome)
        if outcome.get("failed"):
            raise RuntimeError("The desktop clipboard is unavailable.")

    def read(self) -> str | None:
        outcome: dict[str, Any] = {}
        if self._on_application_thread():
            self._read_on_application_thread(outcome)
        else:
            self._read_requested.emit(outcome)
        value = outcome.get("text")
        return value if isinstance(value, str) else None

    def _on_application_thread(self) -> bool:
        return QThread.currentThread() == self.thread()

    @Slot(str, object)
    def _copy_on_application_thread(self, text: str, outcome: object) -> None:
        result = outcome if isinstance(outcome, dict) else {}
        try:
            self._application.clipboard().setText(text)
        except Exception:
            result["failed"] = True

    @Slot(object)
    def _read_on_application_thread(self, outcome: object) -> None:
        result = outcome if isinstance(outcome, dict) else {}
        try:
            result["text"] = self._application.clipboard().text()
        except Exception:
            result["failed"] = True
