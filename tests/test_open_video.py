"""Opening any video in the app: drag and drop, More > Open video..., Ctrl+O."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent, QKeySequence
from PySide6.QtWidgets import QApplication

from screen_recorder_mcp.ui.main_window import video_files
from screen_recorder_mcp.ui.shell import AppShell
from screen_recorder_mcp.ui.theme_controller import ThemeController
from tests.ui_fakes import FakeDialogs, FakeThumbnails, UiFakeRecorderService


def _pump_until(app: QApplication, condition, seconds: float = 3.0) -> None:  # type: ignore[no-untyped-def]
    end = time.monotonic() + seconds
    while not condition() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def _shell(qapp: QApplication, theme: ThemeController, tmp_path: Path) -> tuple[AppShell, FakeDialogs]:
    dialogs = FakeDialogs()
    shell = AppShell(qapp, UiFakeRecorderService(tmp_path), theme, tmp_path, dialogs=dialogs,
                     thumbnails=FakeThumbnails(), with_hotkey=False)  # fmt: skip
    return shell, dialogs


def test_video_files_keeps_local_videos_only() -> None:
    urls = [QUrl.fromLocalFile("C:/v/a.MP4"), QUrl.fromLocalFile("C:/v/notes.txt"), QUrl("https://x/b.mp4"),
            QUrl.fromLocalFile("C:/v/c.mkv")]  # fmt: skip
    assert [p.name for p in video_files(urls)] == ["a.MP4", "c.mkv"]


def test_dropping_a_new_video_adds_it_to_history_and_plays_it(qapp, theme, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    shell, _ = _shell(qapp, theme, tmp_path)
    video = tmp_path / "outside.mp4"
    video.write_bytes(b"stub")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(video))])
    event = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.NoButton,
                       Qt.KeyboardModifier.NoModifier)  # fmt: skip
    shell.window.dropEvent(event)
    _pump_until(qapp, lambda: shell.window.playback().current() is not None
                and shell.window.playback().current().path == video)  # fmt: skip
    assert shell.window.playback().current().path == video  # type: ignore[union-attr]
    assert shell._service.history(1)[0].path == video  # noqa: SLF001


def test_open_video_menu_reuses_a_known_recording(qapp, theme, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    shell, dialogs = _shell(qapp, theme, tmp_path)
    known = shell._service.history(5)[-1]  # noqa: SLF001
    count = len(shell._service.history(500))  # noqa: SLF001
    dialogs.video_to_open = known.path
    shell.window.choose_video()
    assert shell.window.playback().current() == known and len(shell._service.history(500)) == count  # noqa: SLF001
    assert dialogs.asked == ["open_video"]


def test_open_video_has_the_ctrl_o_shortcut(qapp, theme, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    shell, _ = _shell(qapp, theme, tmp_path)
    shortcuts = [a.shortcut() for a in shell.window.actions() if a.text() == "Open video..."]
    assert shortcuts == [QKeySequence(QKeySequence.StandardKey.Open)]
