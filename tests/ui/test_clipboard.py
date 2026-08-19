from __future__ import annotations

import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from openwhisper.ui.clipboard import QtClipboardBridge


def test_clipboard_calls_from_a_worker_are_marshaled_to_qt() -> None:
    application = QApplication.instance() or QApplication([])
    bridge = QtClipboardBridge(application)
    errors: list[Exception] = []

    def copy_from_worker() -> None:
        try:
            bridge.copy("OpenWhisper clipboard probe")
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=copy_from_worker)
    worker.start()
    deadline = time.monotonic() + 3
    while worker.is_alive() and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.005)
    worker.join(timeout=0.1)

    assert not worker.is_alive()
    assert errors == []
    assert bridge.read() == "OpenWhisper clipboard probe"
    application.clipboard().clear()
