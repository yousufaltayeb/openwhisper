from __future__ import annotations

import os
from datetime import UTC, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from openwhisper.ui import MainWindow
from openwhisper.ui.models import AppSettings, HistoryRow, ProviderOption


class Controller:
    def __init__(self):
        self.config = AppSettings()
        self.callbacks = []

    def settings(self):
        return self.config

    def providers(self):
        return (
            ProviderOption(
                "faster-whisper",
                "Faster Whisper",
                "Local",
                ("large-v3-turbo", "medium"),
                supports_streaming=True,
            ),
            ProviderOption(
                "deepgram",
                "Deepgram",
                "Cloud",
                ("nova-3",),
                needs_api_key=True,
            ),
        )

    def save_settings(self, settings):
        self.config = settings

    def save_api_key(self, *_args):
        return None

    def has_api_key(self, *_args):
        return False

    def test_provider(self, *_args):
        return True, "Ready"

    def local_pack_status(self):
        return False, "Not installed"

    def install_local_pack(self, _token=None):
        return True, "Installed"

    def search_history(self, query):
        row = HistoryRow(
            "one",
            datetime.now(UTC),
            "مرحبا hello",
            "مرحبا hello",
            "faster-whisper",
            1.5,
            "ar",
        )
        return (row,) if not query or query in row.final_text else ()

    def copy_text(self, _text):
        return None

    def toggle_recording(self):
        return None

    def cancel(self):
        return None

    def shutdown(self):
        return None

    def subscribe(self, callback):
        self.callbacks.append(callback)


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def test_window_exposes_personal_dictation_workspace_and_streaming_route(application):
    controller = Controller()
    window = MainWindow(controller)
    try:
        assert window.pages.count() == 8
        assert [button.text() for button in window.nav_buttons] == [
            "Dictate",
            "History",
            "Modes",
            "Vocabulary",
            "Snippets",
            "Transforms",
            "Providers",
            "Diagnostics",
        ]
        assert window.history_list.count() == 1
        assert window.mode_combo.currentData() == "raw"
        assert window.context_badge.text() == "Context off"
        assert window.live_insertion.isEnabled()

        window.handle_event("state", {"state": "recording"})
        window.handle_event("partial", {"text": "مرحبا hello"})
        assert window.dictation_status.text() == "Listening…"
        assert window.live_preview.text() == "مرحبا hello"

        window.cleanup_mode.setCurrentIndex(window.cleanup_mode.findData("clean"))
        assert not window.live_insertion.isEnabled()
    finally:
        window.close_to_tray = False
        window.close()


def test_cloud_provider_disables_live_insertion(application):
    window = MainWindow(Controller())
    try:
        window.provider_combo.setCurrentIndex(window.provider_combo.findData("deepgram"))
        assert not window.live_insertion.isEnabled()
        assert window.model_combo.currentText() == "nova-3"
    finally:
        window.close_to_tray = False
        window.close()


def test_personalization_pages_keep_context_off_and_preview_transforms(application):
    window = MainWindow(Controller())
    try:
        window.select_page(2)
        assert window.mode_context_status.text() == "Context off"

        window.select_page(5)
        window.transform_text.setPlainText("hello comma world")
        window._preview_transform()
        assert window.apply_transform_button.isEnabled()
        assert "Hello, world" in window.transform_diff.toPlainText()
        window._apply_transform()
        assert window.transform_text.toPlainText() == "Hello, world"
        window._undo_transform()
        assert window.transform_text.toPlainText() == "hello comma world"
    finally:
        window.close_to_tray = False
        window.close()
