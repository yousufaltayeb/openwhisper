"""Small non-focus-stealing recording/processing overlay."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QSettings, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)


class RecordingOverlay(QFrame):
    def __init__(self, on_stop, on_cancel) -> None:
        super().__init__()
        self._on_stop = on_stop
        self._on_cancel = on_cancel
        self._settings = QSettings()
        self._drag_offset: QPoint | None = None
        self.setObjectName("RecordingOverlay")
        self.setAccessibleName("OpenWhisper recording controls")
        self.setProperty("reducedMotion", self._settings.value("ui/reduced_motion", False, bool))
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(
            "QFrame#RecordingOverlay { background:#151a24; border:1px solid #30394a; "
            "border-radius:14px; } QLabel { background:transparent; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 12, 12)
        row = QHBoxLayout()
        self.indicator = QLabel("●")
        self.indicator.setStyleSheet("color:#f36b72; font-size:18px")
        self.label = QLabel("Listening…")
        self.label.setMinimumWidth(160)
        self.label.setAccessibleName("Recording status; drag to move the overlay")
        self.label.setCursor(Qt.CursorShape.OpenHandCursor)
        self.label.installEventFilter(self)
        row.addWidget(self.indicator)
        row.addWidget(self.label)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setAccessibleName("Stop dictation")
        self.stop_button.clicked.connect(on_stop)
        row.addWidget(self.stop_button)
        cancel = QPushButton("×")
        cancel.setToolTip("Cancel this dictation")
        cancel.setAccessibleName("Cancel dictation")
        cancel.clicked.connect(on_cancel)
        row.addWidget(cancel)
        layout.addLayout(row)
        self.level = QProgressBar()
        self.level.setRange(0, 100)
        self.level.setValue(0)
        self.level.setTextVisible(False)
        self.level.setMaximumHeight(5)
        self.level.setAccessibleName("Microphone input level")
        layout.addWidget(self.level)
        self.preview = QLabel("")
        self.preview.setWordWrap(True)
        self.preview.setMaximumWidth(430)
        self.preview.hide()
        layout.addWidget(self.preview)
        self.adjustSize()

    def show_recording(self) -> None:
        self.indicator.setText("●")
        self.indicator.setStyleSheet("color:#f36b72; font-size:18px")
        self.label.setText("Listening…")
        self.stop_button.setEnabled(True)
        self.level.setValue(0)
        self._show_bottom_center()

    def show_processing(self) -> None:
        self.indicator.setText("◌")
        self.indicator.setStyleSheet("color:#67e8b2; font-size:18px")
        self.label.setText("Transcribing…")
        self.stop_button.setEnabled(False)
        self._show_bottom_center()

    def set_preview(self, text: str) -> None:
        clipped = text.strip()[-180:]
        self.preview.setText(clipped)
        self.preview.setVisible(bool(clipped))
        self.adjustSize()
        self._position_bottom_center()

    def set_level(self, rms: float) -> None:
        self.level.setValue(max(0, min(100, round(rms * 180))))

    def _show_bottom_center(self) -> None:
        self.adjustSize()
        if not self._restore_position():
            self._position_bottom_center()
        self.show()

    def _position_bottom_center(self) -> None:
        screen = self.screen()
        if screen is None and self.windowHandle() is not None:
            screen = self.windowHandle().screen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = area.x() + (area.width() - self.width()) // 2
        y = area.bottom() - self.height() - 28
        self.move(x, y)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.label and isinstance(event, QMouseEvent):
            if (
                event.type() is QEvent.Type.MouseButtonPress
                and event.button() is Qt.MouseButton.LeftButton
            ):
                self._drag_offset = (
                    event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                )
                self.label.setCursor(Qt.CursorShape.ClosedHandCursor)
                return True
            if event.type() is QEvent.Type.MouseMove and self._drag_offset is not None:
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                return True
            if event.type() is QEvent.Type.MouseButtonRelease:
                self._drag_offset = None
                self.label.setCursor(Qt.CursorShape.OpenHandCursor)
                return True
        return super().eventFilter(watched, event)

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if self.isVisible():
            self._settings.setValue("overlay/position", self.pos())

    def _restore_position(self) -> bool:
        saved = self._settings.value("overlay/position")
        if not isinstance(saved, QPoint):
            return False
        screen = self.screen()
        if screen is None or not screen.availableGeometry().contains(saved):
            return False
        self.move(saved)
        return True
