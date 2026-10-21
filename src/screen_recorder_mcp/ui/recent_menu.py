"""Dropdown of the latest recordings, used by the main toolbar and the tray.

Rebuilt every time it opens, so recordings the AI just made (through the control
server) show up without a refresh.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QMenu, QWidget

from screen_recorder_mcp.service import RecorderService, RecordingInfo
from screen_recorder_mcp.ui.view_models import history_row_for

RECENT_LIMIT = 8


def recent_label(info: RecordingInfo, now: datetime) -> str:
    """One menu line: title, duration, when, and an AI tag.

    Example: recent_label(info, now) -> "MCP demo (python.exe)  ·  00:02  ·  Today 07:44  ·  AI"
    """
    row = history_row_for(info, now)
    when = row.subtitle.split("  -  ")[0]
    parts = [row.title, row.duration_text, when] + (["AI"] if row.ai_badge else [])
    return "  ·  ".join(parts)


class RecentRecordingsMenu(QMenu):
    """Example: menu = RecentRecordingsMenu(service, "Recent"); menu.open_requested.connect(show)"""

    open_requested = Signal(object)  # RecordingInfo
    show_all_requested = Signal()

    def __init__(self, service: RecorderService, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self._service = service
        self.aboutToShow.connect(self.rebuild)

    def rebuild(self) -> None:
        """Re-read the history; public so tests (and the tray) can populate it without showing."""
        self.clear()
        recordings = self._recent()
        if not recordings:
            self.addAction("No recordings yet").setEnabled(False)
            return
        now = datetime.now()
        for info in recordings:
            entry = self.addAction(recent_label(info, now))
            entry.triggered.connect(lambda _c=False, i=info: self.open_requested.emit(i))
        self.addSeparator()
        self.addAction("All recordings...").triggered.connect(self.show_all_requested.emit)

    def _recent(self) -> list[RecordingInfo]:
        try:
            return self._service.history(RECENT_LIMIT)
        except (OSError, RuntimeError):
            # A remote service that just went away must not break the menu.
            return []

    def entry_labels(self) -> list[str]:
        """Action texts in order, separators skipped (for tests)."""
        return [action.text() for action in self.actions() if not action.isSeparator()]
