"""Renders the documentation screenshots by grabbing OUR widgets (never the desktop or other apps)."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from screen_recorder_mcp.capture import AudioMode
from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.service import RecordingState, RecordingStatus, TargetList
from screen_recorder_mcp.ui.demo_service import DemoRecorderService
from screen_recorder_mcp.ui.gif_dialog import GifExportDialog
from screen_recorder_mcp.ui.main_window import PAGE_HISTORY, PAGE_SETTINGS
from screen_recorder_mcp.ui.recording_toolbar import RecordingToolbar
from screen_recorder_mcp.ui.selection_session import ScreenInfo, SelectionSession
from screen_recorder_mcp.ui.shell import AppShell
from screen_recorder_mcp.ui.theme_controller import ThemeController
from screen_recorder_mcp.ui.view_models import CaptureTargetMode

DESKTOP = Rect(0, 0, 1280, 720)


def pump(app: QApplication, seconds: float) -> None:
    """Let timers, decoders and ffmpeg thumbnail jobs run for a while."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def save(widget: QWidget, out: Path, name: str) -> Path:
    path = out / f"{name}.png"
    if not widget.grab().save(str(path)):
        raise OSError(f"could not write screenshot {str(path)!r}")
    return path


def fake_desktop() -> QPixmap:
    """Neutral stand-in wallpaper with two fake app windows, so the overlay has something to dim."""
    pixmap = QPixmap(DESKTOP.width, DESKTOP.height)
    painter = QPainter(pixmap)
    gradient = QLinearGradient(QPointF(0, 0), QPointF(DESKTOP.width, DESKTOP.height))
    gradient.setColorAt(0, QColor("#8f7fe8"))
    gradient.setColorAt(1, QColor("#3fb3a8"))
    painter.fillRect(pixmap.rect(), gradient)
    for x, y, w, h in ((90, 110, 620, 430), (560, 200, 600, 400)):
        painter.setBrush(QColor("#e9e7f2"))
        painter.setPen(QColor("#c9c6d8"))
        painter.drawRoundedRect(QRectF(x, y, w, h), 10, 10)
        painter.fillRect(QRectF(x + 1, y + 1, w - 2, 30), QColor("#cfcbe3"))
    painter.end()
    return pixmap


def selection_shot(theme: ThemeController, out: Path, mode: CaptureTargetMode) -> Path:
    targets = TargetList(
        windows=[{"window_id": 7, "title": "Project notes", "process": "notes.exe",
                  "rect": Rect(560, 200, 600, 400).as_dict()}],
        monitors=[{"target": "monitor:1", "rect": DESKTOP.as_dict()}],
    )
    session = SelectionSession(theme, targets, [ScreenInfo(DESKTOP, 1.0)], mode)
    session.show()
    overlay = session.overlays()[0]
    _prepare_overlay(overlay, mode)
    canvas = fake_desktop()
    painter = QPainter(canvas)
    painter.drawPixmap(0, 0, overlay.grab())
    toolbar = session.toolbar()
    if toolbar is not None:
        painter.drawPixmap((DESKTOP.width - toolbar.width()) // 2, 24, toolbar.grab())
    painter.end()
    session.close()
    path = out / f"selection-{mode.value}.png"
    canvas.save(str(path))
    return path


def _prepare_overlay(overlay: object, mode: CaptureTargetMode) -> None:
    if mode is CaptureTargetMode.RECTANGLE:
        overlay.set_selection(Rect(180, 150, 560, 320))  # type: ignore[attr-defined]
    elif mode is CaptureTargetMode.WINDOW:
        overlay._update_hover(QPoint(700, 300))  # type: ignore[attr-defined]


def pill_status(state: RecordingState, who: str, elapsed: float, countdown: int = 0) -> RecordingStatus:
    return RecordingStatus(state=state, recording_id="x", target_label="Demo", elapsed_seconds=elapsed,
                           countdown_remaining=countdown, started_by=who, audio=AudioMode.MIC)  # type: ignore[arg-type]


def toolbar_sheet(theme: ThemeController, out: Path) -> Path:
    """All pill states stacked on the app background: recording, paused, AI-started, countdown, ready."""
    states = [("Recording", pill_status(RecordingState.RECORDING, "user", 83.4)),
              ("Paused", pill_status(RecordingState.PAUSED, "user", 83.4)),
              ("Started by the AI", pill_status(RecordingState.RECORDING, "ai", 12.0)),
              ("Countdown", pill_status(RecordingState.COUNTDOWN, "user", 0, 3)),
              ("Ready", None)]
    pills = []
    for label, status in states:
        pill = RecordingToolbar(theme)
        pill.set_audio_mode(AudioMode.MIC if label == "Recording" else AudioMode.NONE)
        pill.apply_status(status)
        pill.show()
        pills.append(pill.grab())
    canvas = QPixmap(560, 40 + sum(p.height() + 30 for p in pills))
    canvas.fill(QColor(theme.palette.bg))
    painter = QPainter(canvas)
    y = 20
    for (label, _s), shot in zip(states, pills):
        painter.setPen(QColor(theme.palette.text_faint))
        painter.drawText(24, y + 12, label.upper())
        painter.drawPixmap(16, y + 18, shot)
        y += shot.height() + 30
    painter.end()
    path = out / "recording-toolbar.png"
    canvas.save(str(path))
    return path


def render_all(app: QApplication, service: DemoRecorderService, theme: ThemeController, work: Path, out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    _empty_shot(app, theme, work, out)
    shell = AppShell(app, service, theme, work / "cache", with_hotkey=False)
    window = shell.window
    window.show()
    window.playback().video().clear()
    pump(app, 2.5)
    save(window, out, "main-player")
    selection_shot(theme, out, CaptureTargetMode.RECTANGLE)
    selection_shot(theme, out, CaptureTargetMode.WINDOW)
    toolbar_sheet(theme, out)
    _trim_gif_pages(app, shell, out)
    return 0


def _empty_shot(app: QApplication, theme: ThemeController, work: Path, out: Path) -> None:
    empty = DemoRecorderService(work / "empty", seed_history=False)
    shell = AppShell(app, empty, theme, work / "empty-cache", with_hotkey=False)
    shell.window.show()
    pump(app, 0.5)
    save(shell.window, out, "main-empty")
    shell.controller.close()
    shell.window.prepare_quit()
    shell.window.close()


def _trim_gif_pages(app: QApplication, shell: AppShell, out: Path) -> None:
    window = shell.window
    playback = window.playback()
    playback.enter_trim()
    trim = playback._trim  # noqa: SLF001 - dev tool: pose the handles the way a user would
    trim.set_start(1.5)
    trim.set_end(6.0)
    playback._on_trim_changed(trim.start, trim.end)  # noqa: SLF001
    playback._trim_bar.update()  # noqa: SLF001
    pump(app, 0.4)
    save(window, out, "trim-mode")
    playback.exit_trim()
    info = playback.current()
    dialog = GifExportDialog(shell._service, info, shell.theme, shell.runner, lambda _n: None)  # noqa: SLF001
    dialog.show()
    pump(app, 0.3)
    save(dialog, out, "gif-dialog")
    dialog.close()
    window.go_to(PAGE_SETTINGS)
    pump(app, 0.3)
    save(window, out, "settings")
    window.go_to(PAGE_HISTORY)
    pump(app, 2.0)
    save(window, out, "history")



