"""Cropping part of the picture while trimming: ffmpeg args, library, MCP tool, UI geometry."""

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF, QRectF

from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.history_store import JsonHistoryStore
from screen_recorder_mcp.media_tools import MediaTools, build_trim_args
from screen_recorder_mcp.recording_library import RecordingLibrary
from screen_recorder_mcp.service import RecordingRequest
from screen_recorder_mcp.tools import RecorderTools
from screen_recorder_mcp.ui.video_surface import (
    VideoSurface, fitted_rect, handle_at, moved_within, rect_between, resize_by_handle, to_video_point,
)
from tests.fakes import FakeCommandRunner, FakeMediaTools, FakeRecorderService

NOW = datetime(2026, 11, 1, 12, 0)


def make_library(tmp_path: Path) -> tuple[RecordingLibrary, FakeMediaTools]:
    media = FakeMediaTools(duration=4.0)  # probes as 100x80
    ids = iter(["id1", "id2"])
    library = RecordingLibrary(JsonHistoryStore(tmp_path), media, lambda: NOW, lambda: next(ids))  # type: ignore[arg-type]
    (tmp_path / "a.mp4").write_bytes(b"x")
    library.register("a", tmp_path / "a.mp4", "Notepad", "user")
    return library, media


def test_trim_args_add_a_crop_filter_and_refuse_stream_copy() -> None:
    args = build_trim_args("ffmpeg", Path("a.mp4"), 1, 3, Path("c.mp4"), copy=False, crop=Rect(10, 20, 300, 200))
    assert args[args.index("-vf") + 1] == "crop=300:200:10:20" and "libx264" in args
    with pytest.raises(ValueError, match="must re-encode"):
        build_trim_args("ffmpeg", Path("a.mp4"), 0, 3, Path("c.mp4"), copy=True, crop=Rect(0, 0, 2, 2))


def test_cropped_trim_from_zero_skips_the_stream_copy(tmp_path: Path) -> None:
    runner = FakeCommandRunner([])
    MediaTools("ffmpeg", "ffprobe", runner).trim(Path("a.mp4"), 0.0, 3.0, tmp_path / "c.mp4", Rect(0, 0, 50, 40))
    assert len(runner.commands) == 1 and "crop=50:40:0:0" in runner.commands[0]


def test_library_clamps_crop_to_the_frame_and_names_the_copy(tmp_path: Path) -> None:
    library, media = make_library(tmp_path)
    cropped = library.trim("a", 1.0, 3.0, Rect(60, 50, 99, 99))
    assert media.crops == [Rect(60, 50, 40, 30)]  # clipped to 100x80, even size
    assert cropped.path == tmp_path / "a-crop.mp4" and cropped.derived_from == "a"


def test_library_treats_a_full_frame_crop_as_no_crop(tmp_path: Path) -> None:
    library, media = make_library(tmp_path)
    trimmed = library.trim("a", 0.0, 2.0, Rect(0, 0, 100, 80))
    assert media.crops == [None] and trimmed.path.name == "a-trim.mp4"


def test_library_rejects_a_crop_outside_the_frame(tmp_path: Path) -> None:
    library, _ = make_library(tmp_path)
    with pytest.raises(ValueError, match="leaves no area"):
        library.trim("a", 0.0, 2.0, Rect(500, 500, 10, 10))


def test_trim_recording_tool_passes_crop_through() -> None:
    service = FakeRecorderService()
    service.start(RecordingRequest(target="Notepad"))
    info = service.stop()
    RecorderTools(lambda: service).trim_recording(info.recording_id, 0.0, 1.0, {"x": 1, "y": 2, "width": 30, "height": 40})
    RecorderTools(lambda: service).trim_recording(info.recording_id, 0.0, 1.0)
    assert service.trim_crops == [Rect(1, 2, 30, 40), None]


def test_fitted_rect_letterboxes_and_pillarboxes() -> None:
    assert fitted_rect(1920, 1080, 960, 600) == QRectF(0, 30, 960, 540)
    assert fitted_rect(1000, 1000, 400, 200) == QRectF(100, 0, 200, 200)
    assert fitted_rect(0, 0, 400, 200).isNull()


def test_widget_points_map_to_clamped_video_pixels() -> None:
    shown = QRectF(0, 30, 960, 540)
    assert to_video_point(QPointF(480, 300), shown, (1920, 1080)) == (960, 540)
    assert to_video_point(QPointF(-50, 0), shown, (1920, 1080)) == (0, 0)  # outside the picture: clamped
    assert to_video_point(QPointF(2000, 900), shown, (1920, 1080)) == (1920, 1080)


def test_drag_rect_and_move_stay_in_frame() -> None:
    assert rect_between((50, 40), (10, 10)) == Rect(10, 10, 40, 30)
    assert moved_within(Rect(10, 10, 50, 50), -30, 0, (100, 100)) == Rect(0, 10, 50, 50)
    assert moved_within(Rect(10, 10, 50, 50), 90, 90, (100, 100)) == Rect(50, 50, 50, 50)


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg missing")
def test_real_ffmpeg_cropped_trim(tmp_path: Path) -> None:
    source = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=3",
         "-pix_fmt", "yuv420p", str(source)], check=True,
    )  # fmt: skip
    tools = MediaTools("ffmpeg", "ffprobe")
    tools.trim(source, 0.5, 2.5, tmp_path / "c.mp4", Rect(40, 20, 160, 120))
    probe = tools.probe(tmp_path / "c.mp4")
    assert (probe.width, probe.height) == (160, 120)
    assert probe.duration_seconds == pytest.approx(2.0, abs=0.2)


def test_handles_are_found_nearest_first() -> None:
    box = QRectF(0, 0, 100, 50)
    assert handle_at(QPointF(3, 2), box) == "nw"
    assert handle_at(QPointF(50, 49), box) == "s"
    assert handle_at(QPointF(101, 25), box) == "e"
    assert handle_at(QPointF(50, 25), box) is None


def test_resize_by_handle_respects_frame_and_minimum() -> None:
    full = Rect(0, 0, 100, 80)
    assert resize_by_handle(full, "se", -40, -30, (100, 80), 16) == Rect(0, 0, 60, 50)
    assert resize_by_handle(full, "nw", 20, 10, (100, 80), 16) == Rect(20, 10, 80, 70)
    assert resize_by_handle(full, "e", -500, 0, (100, 80), 16) == Rect(0, 0, 16, 80)
    assert resize_by_handle(Rect(10, 10, 50, 50), "n", 0, -99, (100, 80), 16) == Rect(10, 0, 50, 60)


def test_surface_shows_full_frame_and_resets(qapp, theme) -> None:  # type: ignore[no-untyped-def]
    surface = VideoSurface(theme)
    seen: list[object] = []
    surface.crop_changed.connect(seen.append)
    surface.set_crop_editing(True, (1920, 1080))
    assert surface.crop() is None and surface._current_box() == Rect(0, 0, 1920, 1080)
    surface.set_crop(Rect(0, 0, 1920, 1080))
    assert surface.crop() is None  # a full-frame rectangle is "no crop"
    surface.set_crop(Rect(10, 10, 300, 200))
    surface.reset_crop()
    assert surface.crop() is None and seen[-2:] == [Rect(10, 10, 300, 200), None]
