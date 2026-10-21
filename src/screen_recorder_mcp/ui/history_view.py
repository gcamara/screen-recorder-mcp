"""History page: past recordings with thumbnail, duration and an AI badge (RecorderService.history)."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget,
)

from screen_recorder_mcp.service import RecorderService, RecordingInfo
from screen_recorder_mcp.ui.clipboard_actions import ClipboardActions
from screen_recorder_mcp.ui.qt_bridge import BackgroundRunner
from screen_recorder_mcp.ui.theme_controller import ThemeController
from screen_recorder_mcp.ui.thumbnails import ThumbnailMaker
from screen_recorder_mcp.ui.view_models import HistoryRow, history_row_for
from screen_recorder_mcp.ui.widgets_common import role_label

_THUMB = QSize(128, 72)


class HistoryRowWidget(QWidget):
    """One row: thumbnail, title + AI badge, subtitle, duration (Geist Mono, right-aligned)."""

    def __init__(self, row: HistoryRow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 12, 8)
        layout.setSpacing(12)
        self.thumb = QLabel()
        self.thumb.setFixedSize(_THUMB)
        self.thumb.setProperty("role", "card")
        layout.addWidget(self.thumb)
        layout.addLayout(self._texts(row), 1)
        layout.addWidget(role_label(row.duration_text, "mono"), 0, Qt.AlignmentFlag.AlignVCenter)

    def _texts(self, row: HistoryRow) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel(row.title)
        title.setStyleSheet("font-weight: 600;")
        top.addWidget(title)
        if row.ai_badge:
            top.addWidget(role_label("AI", "badge-ai"))
        top.addStretch(1)
        column.addStretch(1)
        column.addLayout(top)
        column.addWidget(role_label(row.subtitle, "muted"))
        column.addStretch(1)
        return column

    def set_thumbnail(self, pixmap: QPixmap) -> None:
        self.thumb.setPixmap(pixmap.scaled(_THUMB, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                           Qt.TransformationMode.SmoothTransformation))


class HistoryView(QWidget):
    """Example: view = HistoryView(service, thumbs, runner); view.refresh(); view.open_requested.connect(f)"""

    open_requested = Signal(object)  # RecordingInfo

    def __init__(
        self, service: RecorderService, thumbnails: ThumbnailMaker, runner: BackgroundRunner,
        clipboard: ClipboardActions | None = None, theme: ThemeController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service, self._thumbs, self._runner = service, thumbnails, runner
        self._clipboard, self._theme = clipboard, theme
        self._items: list[RecordingInfo] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(role_label("HISTORY", "eyebrow"))
        self._empty = role_label("No recordings yet. Press New to make one.", "muted")
        self._list = QListWidget()
        self._list.setFrameShape(QListWidget.Shape.NoFrame)
        self._list.setSpacing(4)
        self._list.itemActivated.connect(self._on_activated)
        self._list.itemClicked.connect(self._on_activated)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_copy_menu)
        layout.addWidget(self._empty)
        layout.addWidget(self._list, 1)

    def refresh(self, limit: int = 50) -> None:
        self._items = self._service.history(limit)
        self._list.clear()
        self._empty.setVisible(not self._items)
        self._list.setVisible(bool(self._items))
        now = datetime.now()
        for info in self._items:
            self._add_row(info, now)

    def _add_row(self, info: RecordingInfo, now: datetime) -> None:
        widget = HistoryRowWidget(history_row_for(info, now))
        item = QListWidgetItem(self._list)
        item.setSizeHint(QSize(0, 88))
        item.setData(Qt.ItemDataRole.UserRole, info.recording_id)
        self._list.setItemWidget(item, widget)
        self._runner.submit(
            lambda: self._thumbs.thumbnail_for(info.path),
            on_result=lambda path: self._apply_thumbnail(widget, path),
        )

    @staticmethod
    def _apply_thumbnail(widget: HistoryRowWidget, path: object) -> None:
        if path is None:
            return
        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            widget.set_thumbnail(pixmap)

    def row_count(self) -> int:
        return self._list.count()

    def _on_activated(self, item: QListWidgetItem) -> None:
        match = self.info_for(item)
        if match is not None:
            self.open_requested.emit(match)

    def info_for(self, item: QListWidgetItem | None) -> RecordingInfo | None:
        if item is None:
            return None
        recording_id = item.data(Qt.ItemDataRole.UserRole)
        return next((i for i in self._items if i.recording_id == recording_id), None)

    def _show_copy_menu(self, pos: QPoint) -> None:
        # Right-click a row: the same copy options as the player, without opening it first.
        info = self.info_for(self._list.itemAt(pos))
        if info is None or self._clipboard is None or self._theme is None:
            return
        menu = self._clipboard.build_menu(self._theme, lambda: info, self)
        menu.exec(self._list.viewport().mapToGlobal(pos))
