"""Long-running private Python engine supervised by the Tauri host."""

from __future__ import annotations

import argparse
import sys
import threading
from collections.abc import Mapping
from typing import Any

from . import __version__
from .protocol import (
    EngineApi,
    EventNormalizer,
    ProtocolDispatcher,
    ProtocolError,
    ProtocolWriter,
    read_frames,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openwhisper-engine", add_help=True)
    parser.add_argument("--version", action="version", version=f"OpenWhisper engine {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    _parser().parse_args(argv)
    try:
        from PySide6.QtCore import QCoreApplication, QObject, Qt, QTimer, Signal, Slot
        from PySide6.QtWidgets import QApplication
    except ImportError:
        sys.stderr.write("OpenWhisper engine requires PySide6.\n")
        return 2

    QCoreApplication.setApplicationName("OpenWhisper")
    QCoreApplication.setApplicationVersion(__version__)
    QCoreApplication.setOrganizationName("OpenWhisper")
    QCoreApplication.setOrganizationDomain("yousufaltayeb.github.io")
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps)
    application = QApplication(sys.argv[:1])
    application.setDesktopFileName("io.github.yousufaltayeb.OpenWhisper")
    application.setQuitOnLastWindowClosed(False)

    from .runtime import RuntimeController
    from .ui.clipboard import QtClipboardBridge
    from .ui.overlay import RecordingOverlay
    from .ui.styles import APP_STYLESHEET

    application.setStyleSheet(APP_STYLESHEET)
    clipboard = QtClipboardBridge(application)

    class MainThreadBridge(QObject):
        invoke = Signal(object)

        def __init__(self) -> None:
            super().__init__(application)
            self.invoke.connect(self.run)

        @Slot(object)
        def run(self, callback: object) -> None:
            if callable(callback):
                callback()

        def submit(self, callback: object) -> None:
            self.invoke.emit(callback)

    main_thread = MainThreadBridge()
    try:
        controller = RuntimeController(
            clipboard=clipboard,
            shortcut_dispatch=main_thread.submit,
        )
    except Exception:
        sys.stderr.write("OpenWhisper engine could not initialize local application data.\n")
        return 1

    writer = ProtocolWriter(sys.stdout.buffer)
    events = EventNormalizer(writer.write)

    class EventBridge(QObject):
        event = Signal(str, object)

    event_bridge = EventBridge(application)
    overlay = RecordingOverlay(controller.stop_recording, controller.cancel)

    @Slot(str, object)
    def route_overlay(event: str, raw_payload: object) -> None:
        payload = raw_payload if isinstance(raw_payload, Mapping) else {}
        if event == "state":
            state = str(payload.get("state", "idle"))
            if state == "recording":
                overlay.show_recording()
            elif state in {"processing", "cleaning", "inserting"}:
                overlay.show_processing()
            elif state in {"idle", "completed", "cancelled", "failed", "error"}:
                overlay.hide()
        elif event == "partial":
            overlay.set_preview(str(payload.get("text", "")))
        elif event == "audio-level":
            overlay.set_level(float(payload.get("rms", 0.0)))

    event_bridge.event.connect(route_overlay)

    def runtime_event(event: str, payload: Mapping[str, Any]) -> None:
        events.runtime_event(event, payload)
        event_bridge.event.emit(event, payload)

    controller.subscribe(runtime_event)

    def request_shutdown() -> None:
        QTimer.singleShot(0, application.quit)

    api = EngineApi(controller, events, on_shutdown=request_shutdown)
    dispatcher = ProtocolDispatcher(api)

    class RequestBridge(QObject):
        request = Signal(object)
        eof = Signal()

        @Slot(object)
        def dispatch(self, raw_frame: object) -> None:
            frame = raw_frame if isinstance(raw_frame, Mapping) else {}
            writer.write(dispatcher.dispatch(frame))

    request_bridge = RequestBridge(application)
    request_bridge.request.connect(request_bridge.dispatch)
    request_bridge.eof.connect(request_shutdown)

    def read_stdin() -> None:
        try:
            for frame in read_frames(sys.stdin.buffer):
                request_bridge.request.emit(frame)
        except ProtocolError as exc:
            sys.stderr.write(f"OpenWhisper engine protocol input closed: {exc.message}\n")
        except Exception:
            sys.stderr.write("OpenWhisper engine protocol input failed.\n")
        finally:
            request_bridge.eof.emit()

    reader = threading.Thread(target=read_stdin, name="openwhisper-engine-input", daemon=True)
    reader.start()
    QTimer.singleShot(0, controller.start_shortcut)
    application.aboutToQuit.connect(controller.shutdown)
    exit_code = application.exec()
    overlay.close()
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
