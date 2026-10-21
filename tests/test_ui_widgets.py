"""Widget-level behaviour on the offscreen platform: pill, settings, history, controller, tray, picker."""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from screen_recorder_mcp.capture import AudioMode
from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.service import RecordingRequest, RecordingState
from screen_recorder_mcp.ui.controller import RecordingController
from screen_recorder_mcp.ui.history_view import HistoryView
from screen_recorder_mcp.ui.qt_bridge import BackgroundRunner, GuiThreadCaller
from screen_recorder_mcp.ui.recording_toolbar import RecordingToolbar
from screen_recorder_mcp.ui.region_picker_qt import QtRegionPicker
from screen_recorder_mcp.ui.selection_session import ScreenInfo
from screen_recorder_mcp.ui.settings_view import SettingsView, settings_changes
from screen_recorder_mcp.ui.theme_controller import ThemeController
from screen_recorder_mcp.ui.tray import AppTray
from screen_recorder_mcp.ui.trim_bar import TrimBar
from screen_recorder_mcp.ui.view_models import TrimModel
from tests.ui_fakes import FakeDialogs, FakeThumbnails, UiFakeRecorderService

SCREEN = ScreenInfo(Rect(0, 0, 800, 600), 1.0)


def _pump(app: QApplication, seconds: float = 0.3) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def test_recording_pill_follows_status(qapp: QApplication, theme: ThemeController, tmp_path: Path) -> None:
    service = UiFakeRecorderService(tmp_path)
    pill = RecordingToolbar(theme)
    pill.show()
    assert not pill._start.isHidden() and pill._stop.isHidden()  # ready: Start only
    service.start(RecordingRequest(target="monitor:1", started_by="ai"))
    pill.apply_status(service.status())
    assert pill._start.isHidden() and not pill._stop.isHidden() and not pill._ai.isHidden()
    service.pause()
    pill.apply_status(service.status())
    assert pill._pause.isHidden() and not pill._resume.isHidden()
    pill.set_audio_mode(AudioMode.MIC)
    assert pill.audio_mode() is AudioMode.MIC


def test_audio_toggles_are_mutually_exclusive(qapp: QApplication, theme: ThemeController) -> None:
    pill = RecordingToolbar(theme)
    changes: list[AudioMode] = []
    pill.audio_changed.connect(changes.append)
    pill._mic.click()
    pill._system.click()
    pill._system.click()
    assert changes == [AudioMode.MIC, AudioMode.SYSTEM, AudioMode.NONE]


def test_controller_shows_pill_for_ai_recordings_only_when_enabled(
    qapp: QApplication, theme: ThemeController, tmp_path: Path
) -> None:
    service = UiFakeRecorderService(tmp_path)
    controller = RecordingController(service, theme, BackgroundRunner(), screens=lambda: [SCREEN])
    activity: list[bool] = []
    controller.active_changed.connect(activity.append)
    service.simulate_ai_start()
    _pump(qapp)
    assert controller.pill().isVisible() and activity == [True]
    service.stop()
    _pump(qapp)
    assert not controller.pill().isVisible() and activity == [True, False]
    service.update_settings({"show_toolbar_for_ai_recordings": False})
    _pump(qapp)
    service.simulate_ai_start()
    _pump(qapp)
    assert not controller.pill().isVisible()
    controller.close()


def test_controller_countdown_and_finish(qapp: QApplication, theme: ThemeController, tmp_path: Path) -> None:
    service = UiFakeRecorderService(tmp_path, seed_history=False)
    controller = RecordingController(service, theme, BackgroundRunner(), screens=lambda: [SCREEN])
    finished: list[object] = []
    controller.recording_finished.connect(finished.append)
    controller._area, controller._screen_rect = Rect(100, 100, 300, 200), SCREEN.logical
    service.start(RecordingRequest(target="monitor:1", delay_seconds=2))
    _pump(qapp)
    assert controller.countdown().isVisible() and controller.countdown().current_count() == 2
    service.tick(1.0)
    _pump(qapp)
    assert controller.countdown().current_count() == 1
    service.tick(1.0)
    _pump(qapp)
    assert service.status().state is RecordingState.RECORDING and not controller.countdown().isVisible()
    service.stop()
    _pump(qapp)
    assert len(finished) == 1
    controller.close()


def test_settings_view_saves_only_changed_fields(qapp: QApplication, tmp_path: Path) -> None:
    service = UiFakeRecorderService(tmp_path)
    view = SettingsView(service, FakeDialogs(folder=tmp_path / "out"))
    view.load()
    saved: list[object] = []
    view.settings_saved.connect(saved.append)
    view._fps.setCurrentText("60")
    view._mic.setChecked(True)
    view._system.setChecked(True)  # exclusive with the microphone
    assert service.get_settings().default_fps == 60 and service.get_settings().include_system_audio
    assert not service.get_settings().include_microphone and len(saved) == 3
    view._browse()
    assert service.get_settings().output_dir == tmp_path / "out"
    base = service.get_settings()
    assert settings_changes(base, replace(base, theme="light")) == {"theme": "light"}


def test_history_view_lists_rows_and_opens_one(qapp: QApplication, tmp_path: Path) -> None:
    service = UiFakeRecorderService(tmp_path)
    view = HistoryView(service, FakeThumbnails(), BackgroundRunner())
    opened: list[object] = []
    view.open_requested.connect(opened.append)
    view.refresh()
    assert view.row_count() == 3
    view._on_activated(view._list.item(1))
    assert opened[0].recording_id == service.history()[1].recording_id  # type: ignore[attr-defined]


def test_trim_bar_drag_moves_handles(qapp: QApplication, theme: ThemeController) -> None:
    bar = TrimBar(theme)
    bar.resize(412, 60)
    model = TrimModel(duration=10.0)
    bar.set_model(model)
    bar.show()
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=QPoint(6, 30))  # left handle
    QTest.mouseMove(bar, QPoint(206, 30))
    QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=QPoint(206, 30))
    assert 4.8 < model.start < 5.2 and model.end == 10.0


def test_tray_menu_shows_stop_only_while_recording(qapp: QApplication) -> None:
    tray = AppTray()
    assert tray.menu_labels() == ["New recording", "Open", "Quit"]
    tray.set_recording(True)
    assert tray.menu_labels() == ["New recording", "Open", "Stop recording", "Quit"]


def test_region_picker_blocks_worker_thread_until_overlay_finishes(
    qapp: QApplication, theme: ThemeController, tmp_path: Path
) -> None:
    service = UiFakeRecorderService(tmp_path, seed_history=False)
    picker = QtRegionPicker(theme, GuiThreadCaller(), service.list_targets, screens=lambda: [SCREEN])
    out: list[object] = []
    worker = threading.Thread(target=lambda: out.append(picker.pick_region(Rect(100, 100, 400, 300))))
    # The pick runs a nested event loop on the GUI thread, so the "user" must act from a timer.
    user = QTimer()
    user.timeout.connect(_finish_open_overlay)
    user.start(50)
    worker.start()
    deadline = time.monotonic() + 5
    while worker.is_alive() and time.monotonic() < deadline:
        _pump(qapp, 0.05)
    user.stop()
    assert out == [Rect(0, 0, 400, 300)]  # whole constrained rect, relative to the target


def _finish_open_overlay() -> None:
    from screen_recorder_mcp.ui.selection_overlay import MonitorOverlay

    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, MonitorOverlay) and widget.isVisible():
            widget._finish_rectangle()


def test_ensure_log_stream_redirects_missing_stderr(tmp_path, monkeypatch) -> None:
    import sys

    from screen_recorder_mcp.ui.app import ensure_log_stream

    monkeypatch.setattr(sys, "stderr", None)
    ensure_log_stream(tmp_path)
    print("hello", file=sys.stderr)
    sys.stderr.close()
    assert (tmp_path / "app.log").read_text(encoding="utf-8").strip() == "hello"


def test_ensure_log_stream_keeps_existing_console(tmp_path) -> None:
    import sys

    from screen_recorder_mcp.ui.app import ensure_log_stream

    before = sys.stderr
    ensure_log_stream(tmp_path)
    assert sys.stderr is before and not (tmp_path / "app.log").exists()


def test_border_frames_area_from_outside(qapp: QApplication, theme: ThemeController) -> None:
    from screen_recorder_mcp.ui.recording_border import BORDER_WIDTH, RecordingBorder, frame_geometry

    assert frame_geometry(Rect(100, 100, 400, 300), 3) == Rect(97, 97, 406, 306)
    border = RecordingBorder(theme)
    border.show_around(Rect(100, 100, 400, 300))
    geometry = border.geometry()
    assert (geometry.x(), geometry.width()) == (100 - BORDER_WIDTH, 400 + 2 * BORDER_WIDTH)
    assert border.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    border.hide()


def test_pill_drag_moves_and_reports_position(qapp: QApplication, theme: ThemeController) -> None:
    pill = RecordingToolbar(theme)
    pill.move(200, 50)
    pill.show()
    reported: list[QPoint] = []
    pill.moved_by_user.connect(reported.append)
    QTest.mousePress(pill, Qt.MouseButton.LeftButton, pos=QPoint(4, 4))
    QTest.mouseMove(pill, QPoint(104, 44))
    QTest.mouseRelease(pill, Qt.MouseButton.LeftButton, pos=QPoint(104, 44))
    assert reported and pill.pos() != QPoint(200, 50)
    pill.hide()


def test_controller_puts_pill_top_centre_and_reuses_dragged_spot(
    qapp: QApplication, theme: ThemeController, tmp_path: Path
) -> None:
    service = UiFakeRecorderService(tmp_path, seed_history=False)
    controller = RecordingController(service, theme, BackgroundRunner(), screens=lambda: [SCREEN])
    controller._area, controller._screen_rect = Rect(100, 300, 300, 200), SCREEN.logical
    service.start(RecordingRequest(target="monitor:1"))
    _pump(qapp)
    pill = controller.pill()
    assert pill.y() == SCREEN.logical.y + 12 and abs(pill.x() + pill.width() // 2 - 400) <= 1
    assert controller._border.isVisible() and controller._border.area() == Rect(100, 300, 300, 200)
    service.stop()
    _pump(qapp)
    assert not controller._border.isVisible()
    controller._remember_pill_pos(QPoint(30, 400))
    controller._area = Rect(100, 300, 300, 200)
    service.start(RecordingRequest(target="monitor:1"))
    _pump(qapp)
    assert pill.pos() == QPoint(30, 400)
    service.stop()
    _pump(qapp)
    controller.close()
