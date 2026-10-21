"""Recent-recordings menu, copy actions, auto-copy and the New-mode regression."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from screen_recorder_mcp.service import RecordingRequest
from screen_recorder_mcp.ui.clipboard_actions import ClipboardActions
from screen_recorder_mcp.ui.controller import RecordingController
from screen_recorder_mcp.ui.history_view import HistoryView
from screen_recorder_mcp.ui.qt_bridge import BackgroundRunner
from screen_recorder_mcp.ui.recent_menu import RecentRecordingsMenu, recent_label
from screen_recorder_mcp.ui.shell import AppShell
from screen_recorder_mcp.ui.theme_controller import ThemeController
from screen_recorder_mcp.ui.view_models import CaptureTargetMode
from tests.test_ui_widgets import SCREEN
from tests.ui_fakes import (
    BrokenHistoryRecorderService,
    FakeDialogs,
    FakeThumbnails,
    GifStubRecorderService,
    UiFakeRecorderService,
)


def _pump(app: QApplication, seconds: float = 0.3) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def _shell(qapp: QApplication, theme: ThemeController, tmp_path: Path) -> AppShell:
    service = UiFakeRecorderService(tmp_path)
    return AppShell(qapp, service, theme, tmp_path, dialogs=FakeDialogs(), thumbnails=FakeThumbnails(),
                    with_hotkey=False)


def test_selected_mode_is_the_enum_for_every_combo_entry(
    qapp: QApplication, theme: ThemeController, tmp_path: Path
) -> None:
    # Regression: QComboBox returned "window" (str), so the overlay always behaved as Rectangle.
    window = _shell(qapp, theme, tmp_path).window
    for index, mode in enumerate(CaptureTargetMode):
        window._mode.setCurrentIndex(index)
        assert window.selected_mode() is mode


def test_begin_selection_coerces_plain_str_mode(qapp: QApplication, theme: ThemeController, tmp_path: Path) -> None:
    service = UiFakeRecorderService(tmp_path)
    controller = RecordingController(service, theme, BackgroundRunner(), screens=lambda: [SCREEN])
    controller.begin_selection("fullscreen", 0)  # type: ignore[arg-type]
    _pump(qapp)
    assert controller._session is not None and controller._session._mode is CaptureTargetMode.FULLSCREEN
    controller._session.close()
    controller.close()


def test_recent_label_has_title_duration_and_ai_tag(tmp_path: Path) -> None:
    service = UiFakeRecorderService(tmp_path)
    ai = next(i for i in service.history() if i.started_by == "ai")
    label = recent_label(ai, datetime.now())
    assert label.startswith(ai.target_label) and label.endswith("AI") and "  ·  " in label


def test_recent_menu_lists_history_and_opens_entry(qapp: QApplication, tmp_path: Path) -> None:
    service = UiFakeRecorderService(tmp_path)
    menu = RecentRecordingsMenu(service, "Recent")
    opened: list[object] = []
    menu.open_requested.connect(opened.append)
    menu.rebuild()
    labels = menu.entry_labels()
    assert len(labels) == len(service.history()) + 1 and labels[-1] == "All recordings..."
    menu.actions()[0].trigger()
    assert opened == [service.history()[0]]


def test_recent_menu_empty_and_unreachable_service(qapp: QApplication, tmp_path: Path) -> None:
    empty = RecentRecordingsMenu(UiFakeRecorderService(tmp_path / "a", seed_history=False))
    empty.rebuild()
    broken = RecentRecordingsMenu(BrokenHistoryRecorderService(tmp_path / "b"))
    broken.rebuild()
    assert empty.entry_labels() == ["No recordings yet"] == broken.entry_labels()


def test_copy_video_path_and_gif(qapp: QApplication, theme: ThemeController, tmp_path: Path) -> None:
    service = GifStubRecorderService(tmp_path)
    actions = ClipboardActions(service, BackgroundRunner())
    messages: list[str] = []
    actions.message.connect(messages.append)
    info = service.history()[0]
    actions.copy_video(info)
    assert QGuiApplication.clipboard().mimeData().urls()[0].toLocalFile() == str(info.path).replace("\\", "/")
    actions.copy_path(info)
    assert QGuiApplication.clipboard().text() == str(info.path)
    actions.copy_gif(info)
    _pump(qapp)
    copied = QGuiApplication.clipboard().mimeData().urls()[0].toLocalFile()
    assert copied.endswith(".gif") and messages[-1].startswith("GIF copied")
    menu = actions.build_menu(theme, lambda: info)  # keep a reference: Qt owns the actions
    labels = [a.text() for a in menu.actions()]
    assert labels == ["Copy video", "Copy as GIF", "Copy file path"]


def test_finished_recording_is_auto_copied(qapp: QApplication, theme: ThemeController, tmp_path: Path) -> None:
    service = UiFakeRecorderService(tmp_path, seed_history=False)
    controller = RecordingController(service, theme, BackgroundRunner(), screens=lambda: [SCREEN])
    notices: list[str] = []
    controller.notice.connect(notices.append)
    service.start(RecordingRequest(target="monitor:1"))
    _pump(qapp)
    info = service.stop()
    _pump(qapp)
    assert notices == ["Recording copied to clipboard"]
    assert QGuiApplication.clipboard().mimeData().urls()[0].toLocalFile() == str(info.path).replace("\\", "/")
    controller.close()


def test_history_row_resolves_info_for_context_menu(qapp: QApplication, theme: ThemeController, tmp_path: Path) -> None:
    service = UiFakeRecorderService(tmp_path)
    view = HistoryView(service, FakeThumbnails(), BackgroundRunner(), ClipboardActions(service, BackgroundRunner()), theme)
    view.refresh()
    assert view.info_for(view._list.item(2)) == service.history()[2] and view.info_for(None) is None


def test_tray_has_recent_submenu(qapp: QApplication, theme: ThemeController, tmp_path: Path) -> None:
    shell = _shell(qapp, theme, tmp_path)
    assert "Recent recordings" in shell.tray.menu_labels()
