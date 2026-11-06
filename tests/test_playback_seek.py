"""Scrubbing the player: dragging or clicking the slider seeks, also while paused, and playback resumes from there."""

from __future__ import annotations

import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from screen_recorder_mcp.service import RecordingInfo
from screen_recorder_mcp.ui.playback_view import PlaybackView
from screen_recorder_mcp.ui.qt_bridge import BackgroundRunner
from screen_recorder_mcp.ui.theme_controller import ThemeController
from tests.ui_fakes import FakeDialogs, UiFakeRecorderService

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _pump_until(app: QApplication, condition, seconds: float = 4.0) -> bool:  # type: ignore[no-untyped-def]
    end = time.monotonic() + seconds
    while not condition() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    return bool(condition())


def _pump(app: QApplication, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture()
def view(qapp: QApplication, theme: ThemeController, tmp_path: Path):  # type: ignore[no-untyped-def]
    video = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1286x842:rate=60:duration=20",
         "-pix_fmt", "yuv420p", str(video)], check=True,
    )  # fmt: skip
    playback = PlaybackView(UiFakeRecorderService(tmp_path), theme, BackgroundRunner(), FakeDialogs())
    playback.resize(900, 600)
    playback.show()
    info = RecordingInfo("r1", video, datetime(2026, 11, 6), 20.0, 1286, 842, False, "Clip", "user")
    playback.show_recording(info)
    assert _pump_until(qapp, lambda: playback._slider.maximum() > 0 and playback._video.has_frame())  # noqa: SLF001
    yield playback
    playback.stop_playback()  # a leftover decoding player starves later tests' worker threads
    playback.close()
    playback.deleteLater()
    _pump(qapp, 0.1)


def _slider_x(view: PlaybackView, fraction: float) -> QPoint:
    slider = view._slider  # noqa: SLF001
    return QPoint(round(slider.width() * fraction), slider.height() // 2)


def _drag_to(qapp: QApplication, view: PlaybackView, fraction: float) -> None:
    slider = view._slider  # noqa: SLF001
    handle = slider.style().subControlRect(
        slider.style().ComplexControl.CC_Slider, _option(slider), slider.style().SubControl.SC_SliderHandle, slider,
    ).center()  # fmt: skip
    QTest.mousePress(slider, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, handle)
    target = _slider_x(view, fraction)
    steps = 12
    for i in range(1, steps + 1):
        QTest.mouseMove(slider, QPoint(handle.x() + (target.x() - handle.x()) * i // steps, handle.y()))
        _pump(qapp, 0.02)
    QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, target)
    _pump(qapp, 0.5)


def _option(slider):  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QStyleOptionSlider

    option = QStyleOptionSlider()
    slider.initStyleOption(option)
    return option


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg missing")
def test_dragging_the_slider_while_paused_seeks_and_resumes_from_there(qapp: QApplication, view: PlaybackView) -> None:
    player = view._player  # noqa: SLF001
    assert player.position() < 1000
    before = view._video._image.copy()  # noqa: SLF001
    _drag_to(qapp, view, 0.75)
    assert _pump_until(qapp, lambda: view._video._image != before)  # noqa: SLF001  # the paused picture follows
    assert player.position() == pytest.approx(15000, abs=1500)
    assert view._slider.value() == pytest.approx(player.position(), abs=200)  # noqa: SLF001
    view.toggle_play()
    _pump(qapp, 0.6)
    assert player.position() >= 14500  # playing on from the slider, not from where it was paused


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg missing")
def test_clicking_the_slider_track_seeks_there(qapp: QApplication, view: PlaybackView) -> None:
    slider, player = view._slider, view._player  # noqa: SLF001
    QTest.mouseClick(slider, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _slider_x(view, 0.5))
    _pump(qapp, 0.5)
    assert player.position() == pytest.approx(10000, abs=1500)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg missing")
def test_seeking_back_while_playing(qapp: QApplication, view: PlaybackView) -> None:
    player = view._player  # noqa: SLF001
    view.toggle_play()
    _pump(qapp, 1.5)
    _drag_to(qapp, view, 0.9)
    assert player.position() >= 15000
    _drag_to(qapp, view, 0.1)
    assert player.position() == pytest.approx(2000, abs=1500)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg missing")
def test_seeking_after_the_end_shows_the_picture_and_stays_paused(qapp: QApplication, view: PlaybackView) -> None:
    from PySide6.QtMultimedia import QMediaPlayer

    player = view._player  # noqa: SLF001
    player.setPosition(19_000)
    view.toggle_play()
    assert _pump_until(qapp, lambda: player.playbackState() == QMediaPlayer.PlaybackState.StoppedState, 6.0)
    before = view._video._image.copy()  # noqa: SLF001
    _drag_to(qapp, view, 0.25)
    assert player.position() == pytest.approx(5000, abs=1500)
    assert player.playbackState() == QMediaPlayer.PlaybackState.PausedState
    assert _pump_until(qapp, lambda: view._video._image != before)  # noqa: SLF001


def test_value_at_maps_pixels_to_the_range() -> None:
    from screen_recorder_mcp.ui.seek_slider import value_at

    assert value_at(150, 0, 300, 0, 20_000) == 10_000
    assert value_at(-5, 0, 300, 0, 20_000) == 0
    assert value_at(900, 0, 300, 0, 20_000) == 20_000
