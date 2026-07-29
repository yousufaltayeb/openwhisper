"""OpenWhisper desktop entry point."""

from __future__ import annotations

import argparse
import sys

from openwhisper import __version__


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openwhisper",
        description="Privacy-first Linux dictation with excellent Arabic support",
    )
    parser.add_argument("--version", action="version", version=f"OpenWhisper {__version__}")
    parser.add_argument(
        "--show",
        action="store_true",
        help="open the main window at launch instead of starting in the tray",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        from PySide6.QtCore import QCoreApplication, QObject, Qt, Signal
        from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon
    except ImportError:
        print(
            "OpenWhisper requires PySide6. Run `uv sync` or reinstall OpenWhisper.",
            file=sys.stderr,
        )
        return 2

    QCoreApplication.setApplicationName("OpenWhisper")
    QCoreApplication.setApplicationVersion(__version__)
    QCoreApplication.setOrganizationName("OpenWhisper")
    QCoreApplication.setOrganizationDomain("yousufaltayeb.github.io")
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps)
    application = QApplication(sys.argv[:1] + (argv or []))
    application.setDesktopFileName("io.github.yousufaltayeb.OpenWhisper")
    application.setQuitOnLastWindowClosed(False)

    from .runtime import RuntimeController
    from .ui import MainWindow, RecordingOverlay, TrayController
    from .ui.styles import APP_STYLESHEET

    application.setStyleSheet(APP_STYLESHEET)
    try:
        controller = RuntimeController()
    except Exception as exc:
        QMessageBox.critical(
            None,
            "OpenWhisper could not start",
            f"OpenWhisper could not initialize its local data: {exc}",
        )
        return 1

    window = MainWindow(controller)
    overlay = RecordingOverlay(controller.toggle_recording, controller.cancel)
    tray = TrayController(window, overlay, controller)

    def route_event(event: str, payload: object) -> None:
        details = payload if isinstance(payload, dict) else {}
        if event == "state":
            state = str(details.get("state", "idle"))
            tray.set_state(state)
            if state == "recording":
                overlay.show_recording()
            elif state == "processing":
                overlay.show_processing()
            elif state in {"idle", "completed", "cancelled", "failed", "error"}:
                overlay.hide()
        elif event == "partial":
            overlay.set_preview(str(details.get("text", "")))
        elif event == "audio-level":
            overlay.set_level(float(details.get("rms", 0)))
        elif event == "warning":
            tray.notify("OpenWhisper", str(details.get("message", "Warning")), True)
        elif event == "error":
            tray.notify(
                "OpenWhisper error",
                str(details.get("message", "Dictation failed")),
                True,
            )
        elif event == "transcript" and controller.settings().notifications:
            text = str(details.get("text", ""))
            preview = text[:100] + ("…" if len(text) > 100 else "")
            tray.notify("Dictation inserted", preview)

    class _EventBridge(QObject):
        event = Signal(str, object)

    event_bridge = _EventBridge(application)
    event_bridge.event.connect(route_event)
    controller.subscribe(event_bridge.event.emit)
    application.aboutToQuit.connect(controller.shutdown)

    tray_available = QSystemTrayIcon.isSystemTrayAvailable()
    application.setQuitOnLastWindowClosed(not tray_available)
    window.close_to_tray = tray_available
    if not tray_available or args.show:
        window.show()
    if tray_available:
        tray.show()
    controller.start_shortcut()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
