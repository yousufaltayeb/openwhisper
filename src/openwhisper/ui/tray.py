"""System tray integration."""

from __future__ import annotations

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon


class TrayController(QSystemTrayIcon):
    def __init__(self, window, overlay, controller) -> None:
        style = QApplication.style()
        icon = QIcon.fromTheme(
            "audio-input-microphone",
            style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay),
        )
        super().__init__(icon, window)
        self.window = window
        self.overlay = overlay
        self.controller = controller
        self.setToolTip("OpenWhisper — Ready")

        menu = QMenu()
        show_action = QAction("Open OpenWhisper", menu)
        show_action.triggered.connect(self._show_window)
        menu.addAction(show_action)
        self.record_action = QAction("Start dictation", menu)
        self.record_action.triggered.connect(controller.toggle_recording)
        menu.addAction(self.record_action)
        menu.addSeparator()
        settings_action = QAction("Settings", menu)
        settings_action.triggered.connect(lambda: self._show_window(2))
        menu.addAction(settings_action)
        menu.addSeparator()
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)
        self.setContextMenu(menu)
        self.activated.connect(self._activated)

    def set_state(self, state: str) -> None:
        labels = {
            "idle": ("Start dictation", "OpenWhisper — Ready"),
            "recording": ("Stop dictation", "OpenWhisper — Recording"),
            "processing": ("Processing…", "OpenWhisper — Processing"),
            "error": ("Start dictation", "OpenWhisper — Needs attention"),
        }
        action, tooltip = labels.get(state, labels["idle"])
        self.record_action.setText(action)
        self.record_action.setEnabled(state != "processing")
        self.setToolTip(tooltip)

    def notify(self, title: str, message: str, warning: bool = False) -> None:
        icon = (
            QSystemTrayIcon.MessageIcon.Warning
            if warning
            else QSystemTrayIcon.MessageIcon.Information
        )
        self.showMessage(title, message, icon, 5000)

    def _activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_window()

    def _show_window(self, page: int | None = None) -> None:
        if page is not None:
            self.window.select_page(page)
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def _quit(self) -> None:
        self.controller.shutdown()
        QApplication.quit()
