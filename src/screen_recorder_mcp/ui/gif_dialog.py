"""Export GIF dialog (Snipping Tool's CreateGifDialog): quality cards with size hints, progress, Export / Copy."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QProgressBar, QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

from screen_recorder_mcp.service import RecorderService, RecordingInfo
from screen_recorder_mcp.ui.file_actions import copy_file_to_clipboard
from screen_recorder_mcp.ui.formatting import estimate_gif_bytes, format_bytes
from screen_recorder_mcp.ui.qt_bridge import BackgroundRunner
from screen_recorder_mcp.ui.theme_controller import ThemeController
from screen_recorder_mcp.ui.widgets_common import role_label

SavePathChooser = Callable[[str], Path | None]


class _QualityCard(QFrame):
    """Clickable card: radio + title on top, size hint under it."""

    def __init__(self, title: str, hint: str, group_owner: QWidget) -> None:
        super().__init__(group_owner)
        self.setProperty("role", "card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.radio = QRadioButton(title)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)
        layout.addWidget(self.radio)
        note = role_label(hint, "muted")
        note.setContentsMargins(26, 0, 0, 0)
        layout.addWidget(note)

    def mousePressEvent(self, _event: object) -> None:
        self.radio.setChecked(True)


class GifExportDialog(QDialog):
    """Example: GifExportDialog(service, info, theme, runner, choose_save).exec()"""

    def __init__(
        self, service: RecorderService, info: RecordingInfo, theme: ThemeController,
        runner: BackgroundRunner, choose_save: SavePathChooser, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create GIF")
        self.setMinimumWidth(420)
        self._service, self._info, self._runner, self._choose_save = service, info, runner, choose_save
        self._theme = theme
        self.exported_path: Path | None = None
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addWidget(role_label("Create GIF", "title"))
        layout.addWidget(role_label("Pick a quality. Smaller GIFs are easier to share, larger ones look sharper.", "muted"))
        self._high = _QualityCard("High quality", self._hint(True), self)
        self._low = _QualityCard("Low quality", self._hint(False), self)
        self._high.radio.setChecked(True)
        layout.addWidget(self._high)
        layout.addWidget(self._low)
        self._status = role_label("", "muted")
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate: export_gif reports no percentage
        self._progress.setTextVisible(False)
        self._progress.hide()
        layout.addWidget(self._status)
        layout.addWidget(self._progress)
        layout.addLayout(self._build_buttons())

    def _hint(self, high: bool) -> str:
        i = self._info
        size = format_bytes(estimate_gif_bytes(i.duration_seconds, i.width, i.height, high))
        detail = "full size, 15 fps" if high else "half size, 10 fps"
        return f"About {size}  -  {detail}"

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch(1)
        self._cancel = QPushButton("Cancel")
        self._copy = QPushButton("Copy")
        self._export = QPushButton("Export")
        self._export.setProperty("role", "primary")
        self._cancel.clicked.connect(self.reject)
        self._copy.clicked.connect(self._start_copy)
        self._export.clicked.connect(self._start_export)
        for button in (self._cancel, self._copy, self._export):
            row.addWidget(button)
        return row

    def high_quality(self) -> bool:
        return self._high.radio.isChecked()

    # --- actions ---------------------------------------------------------
    def _start_export(self) -> None:
        suggested = f"{self._info.path.stem}.gif"
        destination = self._choose_save(suggested)
        if destination is None:
            return
        self._run(destination, copy_after=False)

    def _start_copy(self) -> None:
        self._run(None, copy_after=True)

    def _run(self, output: Path | None, copy_after: bool) -> None:
        self._set_busy(True)
        quality = self.high_quality()
        self._runner.submit(
            lambda: self._service.export_gif(self._info.recording_id, quality, output),
            on_result=lambda path: self._finished(path, copy_after),
            on_error=self._failed,
        )

    def _finished(self, path: Path, copy_after: bool) -> None:
        self.exported_path = path
        if copy_after:
            copy_file_to_clipboard(path)
        self._set_busy(False)
        self.accept()

    def _failed(self, error: Exception) -> None:
        self._set_busy(False)
        self._status.setText(f"Export failed: {error}")
        self._status.setStyleSheet(f"color: {self._theme.palette.failure};")

    def _set_busy(self, busy: bool) -> None:
        self._progress.setVisible(busy)
        self._status.setText("Creating GIF..." if busy else "")
        for widget in (self._export, self._copy, self._high, self._low):
            widget.setEnabled(not busy)
