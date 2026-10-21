"""Pure UI logic: theme tokens, formatting, view models, selection geometry, hotkey parsing."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from screen_recorder_mcp.capture import AudioMode
from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.service import (
    RecorderEvent, RecorderEventKind, RecorderSettings, RecordingInfo, RecordingState, RecordingStatus,
)
from screen_recorder_mcp.ui import selection_geometry as geo
from screen_recorder_mcp.ui.formatting import (
    estimate_gif_bytes, format_bytes, format_clock, format_clock_tenths, format_created,
)
from screen_recorder_mcp.ui.os_integration import parse_hotkey
from screen_recorder_mcp.ui.theme import DARK, LIGHT, Palette, build_qss, palette_for
from screen_recorder_mcp.ui.view_models import (
    TrimModel, audio_mode_for, build_request, history_row_for, toggle_audio, follow_window, pill_home,
    toolbar_should_hide, toolbar_should_show, toolbar_view_for,
)


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("palette", [DARK, LIGHT])
def test_text_colours_meet_wcag_aa_on_every_surface(palette: Palette) -> None:
    for text in (palette.text, palette.text_muted, palette.text_faint):
        for surface in (palette.bg, palette.surface_raised, palette.surface_overlay):
            assert _contrast(text, surface) >= 4.5, (palette.name, text, surface)


def test_palette_resolution_and_qss_has_no_default_blue() -> None:
    assert palette_for("system", system_is_dark=False) is LIGHT
    assert palette_for("dark") is DARK
    with pytest.raises(ValueError, match="'neon'"):
        palette_for("neon")
    qss = build_qss(DARK)
    assert DARK.accent in qss and "#2563eb" not in qss.lower() and "#3b82f6" not in qss.lower()


def test_formatting() -> None:
    assert format_clock(65.4) == "01:05"
    assert format_clock(3725) == "1:02:05"
    assert format_clock_tenths(3.26) == "00:03.2"
    assert format_bytes(1_572_864) == "1.5 MB"
    assert estimate_gif_bytes(8, 640, 360, True) > estimate_gif_bytes(8, 640, 360, False)
    with pytest.raises(ValueError, match="-1"):
        format_clock(-1)
    now = datetime(2026, 10, 21, 12, 0)
    assert format_created(now.replace(hour=9), now) == "Today, 09:00"
    assert format_created(now - timedelta(days=1), now).startswith("Yesterday")


def _status(state: RecordingState, who: str = "user", elapsed: float = 0.0) -> RecordingStatus:
    return RecordingStatus(state=state, started_by=who, elapsed_seconds=elapsed)  # type: ignore[arg-type]


def test_toolbar_view_per_state() -> None:
    ready = toolbar_view_for(None)
    assert ready.show_start and not ready.show_stop and ready.audio_editable
    rec = toolbar_view_for(_status(RecordingState.RECORDING, elapsed=83))
    assert (rec.show_pause, rec.show_stop, rec.dot_pulses, rec.timer_text) == (True, True, True, "01:23")
    paused = toolbar_view_for(_status(RecordingState.PAUSED))
    assert paused.show_resume and not paused.show_pause and not paused.dot_pulses
    assert toolbar_view_for(_status(RecordingState.COUNTDOWN)).show_dot is False
    assert toolbar_view_for(_status(RecordingState.FINALIZING)).show_cancel is False
    assert toolbar_view_for(_status(RecordingState.RECORDING, "ai")).ai_badge is True


def test_ai_recordings_respect_the_toolbar_setting(tmp_path: Path) -> None:
    settings = RecorderSettings(output_dir=tmp_path)
    started_by_ai = RecorderEvent(RecorderEventKind.STARTED, _status(RecordingState.RECORDING, "ai"))
    assert toolbar_should_show(started_by_ai, settings)
    assert not toolbar_should_show(started_by_ai, replace(settings, show_toolbar_for_ai_recordings=False))
    by_user = RecorderEvent(RecorderEventKind.STARTED, _status(RecordingState.RECORDING))
    assert toolbar_should_show(by_user, replace(settings, show_toolbar_for_ai_recordings=False))
    assert toolbar_should_hide(RecorderEvent(RecorderEventKind.STOPPED, _status(RecordingState.IDLE)))
    assert not toolbar_should_hide(started_by_ai)


def test_audio_rules_and_request(tmp_path: Path) -> None:
    assert audio_mode_for(True, False) is AudioMode.MIC
    assert audio_mode_for(True, True) is AudioMode.SYSTEM
    assert toggle_audio(AudioMode.MIC, AudioMode.MIC) is AudioMode.NONE
    assert toggle_audio(AudioMode.MIC, AudioMode.SYSTEM) is AudioMode.SYSTEM
    with pytest.raises(ValueError, match="AudioMode.NONE"):
        toggle_audio(AudioMode.MIC, AudioMode.NONE)
    settings = RecorderSettings(output_dir=tmp_path, default_fps=60)
    request = build_request("monitor:1", Rect(1, 2, 3, 4), settings, AudioMode.NONE, 5)
    assert (request.fps, request.delay_seconds, request.started_by) == (60, 5, "user")
    with pytest.raises(ValueError, match="delay=4"):
        build_request("monitor:1", None, settings, AudioMode.NONE, 4)


def test_trim_model_clamps_handles() -> None:
    trim = TrimModel(duration=8.0)
    trim.set_start(7.9)
    assert trim.start == pytest.approx(7.5)  # keeps min_length
    trim.set_start(1.0)
    trim.set_end(0.2)
    assert trim.end == pytest.approx(1.5) and trim.is_trimmed and trim.length == pytest.approx(0.5)
    with pytest.raises(ValueError, match="duration=0"):
        TrimModel(duration=0)


def test_pill_home_is_top_centre_of_the_recorded_screen() -> None:
    assert pill_home(Rect(0, 0, 1920, 1080), (420, 60)) == (750, 12)
    assert pill_home(Rect(1920, 0, 2560, 1440), (420, 60)) == (1920 + 1070, 12)


def test_follow_window_converts_native_movement_to_logical() -> None:
    area, start = Rect(100, 100, 400, 300), Rect(125, 125, 500, 375)
    assert follow_window(area, start, Rect(150, 125, 500, 375), 1.25) == Rect(120, 100, 400, 300)
    assert follow_window(area, start, Rect(125, 125, 625, 375), 1.25) == Rect(100, 100, 500, 300)
    assert follow_window(area, start, start, 0) == area


def test_history_row_marks_ai(tmp_path: Path) -> None:
    info = RecordingInfo("r1", tmp_path / "a.mp4", datetime(2026, 10, 21, 9, 0), 7.2, 640, 360, False, "Chrome", "ai")
    row = history_row_for(info, datetime(2026, 10, 21, 12, 0))
    assert (row.ai_badge, row.duration_text, row.title) == (True, "00:07", "Chrome")
    assert "Today, 09:00" in row.subtitle


def test_selection_geometry() -> None:
    bounds = Rect(0, 0, 400, 300)
    rect = Rect(100, 100, 100, 100)
    assert geo.handle_at(rect, 100, 100) is geo.Handle.TOP_LEFT
    assert geo.handle_at(rect, 150, 150) is geo.Handle.BODY
    assert geo.handle_at(rect, 300, 300) is None
    assert geo.resize(rect, geo.Handle.RIGHT, 500, 0, bounds).width == 300  # clamped to bounds
    assert geo.resize(rect, geo.Handle.LEFT, 500, 0, bounds).width == geo.MIN_SIZE
    assert geo.nudge(rect, -500, 0, bounds).x == 0
    assert geo.drag_rect((10, 10), (12, 12), bounds) is None
    assert geo.window_at(150, 150, [Rect(0, 0, 10, 10), rect]) == 1


def test_dpi_mapping_and_monitor_matching() -> None:
    origin = Rect(1920, 0, 2560, 1440)
    local = Rect(100, 50, 200, 100)
    physical = geo.local_to_physical(local, origin, 1.25)
    assert physical == Rect(2045, 62, 250, 125)
    assert geo.physical_to_local(physical, origin, 1.25) == local
    monitors = [Rect(0, 0, 1920, 1080), Rect(1920, 0, 1920, 1080)]
    screens = [Rect(5, 5, 1920, 1080), Rect(1920, 0, 1920, 1080)]
    assert geo.match_monitors(screens, monitors) == [1, 2]  # same size twice -> two different monitors


def test_hotkey_parsing() -> None:
    spec = parse_hotkey("ctrl+shift+r")
    assert (spec.modifiers, spec.virtual_key) == (6, ord("R"))
    assert parse_hotkey("alt+F9").virtual_key == 0x78
    with pytest.raises(ValueError, match="'r'"):
        parse_hotkey("r")
