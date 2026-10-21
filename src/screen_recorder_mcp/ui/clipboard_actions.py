"""Copy a recording to the clipboard three ways, shared by the player, history and recent menu.

Snipping Tool copies the result automatically and offers copy again after; these are the
"again" paths: the video file, a GIF of it, or its path as text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMenu, QWidget

from screen_recorder_mcp.service import RecorderService, RecordingInfo
from screen_recorder_mcp.ui.file_actions import copy_file_to_clipboard
from screen_recorder_mcp.ui.icons import icon
from screen_recorder_mcp.ui.qt_bridge import BackgroundRunner
from screen_recorder_mcp.ui.theme_controller import ThemeController

InfoGetter = Callable[[], RecordingInfo | None]


class ClipboardActions(QObject):
    """Example: actions = ClipboardActions(service, runner); actions.copy_video(info)"""

    message = Signal(str)  # user-facing result, shown as a toast

    def __init__(self, service: RecorderService, runner: BackgroundRunner, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._service, self._runner = service, runner

    def copy_video(self, info: RecordingInfo) -> None:
        """Put the MP4 file itself on the clipboard (pastes into Explorer, Slack, mail)."""
        try:
            copy_file_to_clipboard(info.path)
        except FileNotFoundError as error:
            self.message.emit(str(error))
            return
        self.message.emit("Video copied to clipboard")

    def copy_path(self, info: RecordingInfo) -> None:
        """Put the file path as plain text on the clipboard (pastes into a terminal or chat)."""
        QGuiApplication.clipboard().setText(str(info.path))
        self.message.emit("Path copied to clipboard")

    def copy_gif(self, info: RecordingInfo) -> None:
        """Export a GIF next to the video (worker thread), then copy that file."""
        self.message.emit("Making GIF...")
        self._runner.submit(
            lambda: self._service.export_gif(info.recording_id, True),
            on_result=self._copy_exported_gif,
            on_error=lambda error: self.message.emit(f"GIF failed: {error}"),
        )

    def _copy_exported_gif(self, path: Path) -> None:
        try:
            copy_file_to_clipboard(Path(path))
        except FileNotFoundError as error:
            self.message.emit(str(error))
            return
        self.message.emit(f"GIF copied to clipboard ({Path(path).name})")

    def build_menu(self, theme: ThemeController, current: InfoGetter, parent: QWidget | None = None) -> QMenu:
        """Menu with the three copy actions for whatever `current()` returns when clicked.

        Example: button.setMenu(actions.build_menu(theme, lambda: self._info, button))
        """
        menu = QMenu(parent)
        muted = theme.palette.text_muted
        for glyph, text, action in (("copy", "Copy video", self.copy_video), ("gif", "Copy as GIF", self.copy_gif),
                                    ("folder", "Copy file path", self.copy_path)):
            entry = menu.addAction(icon(glyph, muted, 16), text)
            entry.triggered.connect(lambda _c=False, a=action: self._run(a, current))
        return menu

    @staticmethod
    def _run(action: Callable[[RecordingInfo], None], current: InfoGetter) -> None:
        info = current()
        if info is not None:
            action(info)
