import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from mcp.server.mcpserver import Image as McpImage
from PIL import Image

from screen_recorder_mcp.frames import (
    Frame, FrameExtractor, FrameMode, FrameRequest, FrameSet, build_contact_sheet, build_select_args,
    evenly_spaced, format_seconds, frames_dir_for, inline_jpeg, parse_frame_times, select_filter,
)
from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.service import RecordingRequest
from screen_recorder_mcp.tools import RecorderTools
from tests.fakes import FakeRecorderService


def write_png(path: Path, width: int = 320, height: int = 180, color: tuple[int, int, int] = (200, 30, 30)) -> Path:
    Image.new("RGB", (width, height), color).save(path)
    return path


class FakeFrameFfmpeg:
    """Writes `count` PNG frames into the output pattern's folder and logs showinfo lines like ffmpeg."""

    def __init__(self, times: list[float]) -> None:
        self.times = times
        self.commands: list[list[str]] = []

    def __call__(self, args: list[str]) -> "subprocess.CompletedProcess[bytes]":
        self.commands.append(args)
        out_dir = Path(args[-1]).parent
        lines = []
        for number, seconds in enumerate(self.times, start=1):
            write_png(out_dir / f"frame-{number:04d}.png")
            lines.append(f"[Parsed_showinfo_1 @ 0000] n:{number - 1} pts:{int(seconds * 1000)} pts_time:{seconds} duration:1")
        return subprocess.CompletedProcess(args, 0, stdout=b"", stderr="\n".join(lines).encode())


class FakeExtractor:
    def __init__(self, frames_dir: Path, seconds: list[float]) -> None:
        self.frames_dir = frames_dir
        self.seconds = seconds
        self.calls: list[tuple[Path, FrameRequest]] = []

    def sample(self, source: Path, request: FrameRequest) -> FrameSet:
        self.calls.append((source, request))
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        frames = [Frame(s, write_png(self.frames_dir / f"frame-{i:04d}.png")) for i, s in enumerate(self.seconds, 1)]
        return FrameSet(frames, len(frames), self.frames_dir)


def test_select_filter_per_mode() -> None:
    assert select_filter(FrameRequest(FrameMode.CHANGES)) == "mpdecimate,showinfo"
    assert select_filter(FrameRequest(FrameMode.SCENES, scene_threshold=0.2)) == (
        "select='eq(n\\,0)+gt(scene\\,0.2)',showinfo"
    )
    interval = select_filter(FrameRequest(FrameMode.INTERVAL, interval_seconds=0.5))
    assert "gte(t-prev_selected_t\\,0.5)" in interval and interval.endswith(",showinfo")


def test_select_filter_trims_to_the_requested_range() -> None:
    chain = select_filter(FrameRequest(FrameMode.CHANGES, start_seconds=1.5, end_seconds=3))
    assert chain == "trim=start=1.5:end=3,mpdecimate,showinfo"


def test_select_args_fast_seek_keeps_original_timestamps(tmp_path: Path) -> None:
    args = build_select_args("ffmpeg", Path("a.mp4"), FrameRequest(start_seconds=2.0), tmp_path)
    assert args[args.index("-ss") + 1] == "2.000" and args.index("-copyts") < args.index("-i")
    assert args[args.index("-loglevel") + 1] == "info" and args[-1].endswith("frame-%04d.png")
    assert "-ss" not in build_select_args("ffmpeg", Path("a.mp4"), FrameRequest(), tmp_path)


def test_parse_frame_times_reads_showinfo_lines_only() -> None:
    stderr = (
        "Input #0, mov,mp4 ... Duration: 00:00:04.00\n"
        "[Parsed_showinfo_1 @ 000001] n:   0 pts:      0 pts_time:0       duration:512\n"
        "[Parsed_showinfo_1 @ 000001] n:   1 pts:  23552 pts_time:1.533333 duration:512\n"
    )
    assert parse_frame_times(stderr) == [0.0, 1.533333]


def test_evenly_spaced_keeps_first_and_last() -> None:
    assert evenly_spaced(10, 4) == [0, 3, 6, 9]
    assert evenly_spaced(3, 12) == [0, 1, 2]
    assert evenly_spaced(5, 1) == [0]
    with pytest.raises(ValueError, match="max_frames=0 is invalid"):
        evenly_spaced(5, 0)


def test_frames_dir_never_reuses_an_earlier_sample(tmp_path: Path) -> None:
    source = tmp_path / "rec.mp4"
    assert frames_dir_for(source) == tmp_path / "rec-frames" / "1"
    (tmp_path / "rec-frames" / "1").mkdir(parents=True)
    assert frames_dir_for(source) == tmp_path / "rec-frames" / "2"


def test_format_seconds() -> None:
    assert format_seconds(83.25) == "1:23.250" and format_seconds(0) == "0:00.000"


def test_sample_thins_evenly_and_deletes_the_rest(tmp_path: Path) -> None:
    source = tmp_path / "rec.mp4"
    source.write_bytes(b"x")
    runner = FakeFrameFfmpeg([0.0, 0.5, 1.0, 1.5, 2.0])
    frame_set = FrameExtractor("ffmpeg", runner).sample(source, FrameRequest(max_frames=3))
    assert [f.seconds for f in frame_set.frames] == [0.0, 1.0, 2.0] and frame_set.found == 5
    assert sorted(p.name for p in frame_set.directory.glob("*.png")) == [f.path.name for f in frame_set.frames]


def test_sample_rejects_missing_recording(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="gone.mp4 does not exist"):
        FrameExtractor("ffmpeg", FakeFrameFfmpeg([])).sample(tmp_path / "gone.mp4", FrameRequest())


def test_timestamps_mode_runs_one_seek_per_moment(tmp_path: Path) -> None:
    source = tmp_path / "rec.mp4"
    source.write_bytes(b"x")
    commands: list[list[str]] = []

    def runner(args: list[str]) -> "subprocess.CompletedProcess[bytes]":
        commands.append(args)
        write_png(Path(args[-1]))
        return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

    request = FrameRequest(FrameMode.TIMESTAMPS, timestamps=(0.5, 2.25))
    frame_set = FrameExtractor("ffmpeg", runner).sample(source, request)
    assert [c[c.index("-ss") + 1] for c in commands] == ["0.500", "2.250"]
    assert [f.seconds for f in frame_set.frames] == [0.5, 2.25]


def test_inline_jpeg_crops_then_fits_width(tmp_path: Path) -> None:
    frame = write_png(tmp_path / "f.png", 1920, 1080)
    with Image.open(io.BytesIO(inline_jpeg(frame, 800))) as small:
        assert small.size == (800, 450) and small.format == "JPEG"
    with Image.open(io.BytesIO(inline_jpeg(frame, 800, Rect(100, 100, 200, 50)))) as crop:
        assert crop.size == (200, 50)
    with pytest.raises(ValueError, match="lies outside the 1920x1080 frame"):
        inline_jpeg(frame, 800, Rect(5000, 0, 10, 10))


def test_contact_sheet_lays_out_a_captioned_grid(tmp_path: Path) -> None:
    frames = [Frame(i * 0.5, write_png(tmp_path / f"{i}.png", 640, 360)) for i in range(5)]
    sheet = build_contact_sheet(frames, columns=3, tile_width=200, output=tmp_path / "sheet.png")
    with Image.open(sheet) as image:
        # 3 columns x 2 rows of 200x112 tiles + 22 px captions, 4 px gaps around every tile.
        assert image.size == (3 * 200 + 4 * 4, 2 * (112 + 22) + 3 * 4)
    with pytest.raises(ValueError, match="needs at least one frame"):
        build_contact_sheet([], 3, 200, tmp_path / "empty.png")


@pytest.fixture
def recorded(tmp_path: Path) -> tuple[FakeRecorderService, FakeExtractor, RecorderTools]:
    service = FakeRecorderService()
    service.start(RecordingRequest(target="Notepad"))
    service.stop()
    extractor = FakeExtractor(tmp_path / "frames", [0.0, 1.25, 2.5])
    return service, extractor, RecorderTools(lambda: service, lambda: extractor)


def test_sample_frames_defaults_to_the_latest_recording(
    recorded: tuple[FakeRecorderService, FakeExtractor, RecorderTools],
) -> None:
    service, extractor, tools = recorded
    content = tools.sample_frames(mode="scenes", max_frames=3, width=100)
    body = json.loads(content[0])
    assert extractor.calls[0][0] == service.history(1)[0].path
    assert extractor.calls[0][1].mode is FrameMode.SCENES
    assert [f["time"] for f in body["frames"]] == ["0:00.000", "0:01.250", "0:02.500"]
    assert len(content) == 4 and all(isinstance(item, McpImage) for item in content[1:])


def test_sample_frames_without_inline_returns_only_paths(
    recorded: tuple[FakeRecorderService, FakeExtractor, RecorderTools],
) -> None:
    _, _, tools = recorded
    assert len(tools.sample_frames(inline=False)) == 1


def test_frame_source_errors_name_the_fix(recorded: tuple[FakeRecorderService, FakeExtractor, RecorderTools]) -> None:
    _, _, tools = recorded
    with pytest.raises(ValueError, match="not found; call list_recordings"):
        tools.sample_frames(recording_id="nope")
    with pytest.raises(ValueError, match="either recording_id or path"):
        tools.sample_frames(recording_id="a", path="b.mp4")
    with pytest.raises(ValueError, match="mode must be one of"):
        tools.sample_frames(mode="fast")
    with pytest.raises(ValueError, match="no recordings yet"):
        RecorderTools(FakeRecorderService).sample_frames()


def test_contact_sheet_and_frame_at_return_one_image(
    recorded: tuple[FakeRecorderService, FakeExtractor, RecorderTools], tmp_path: Path,
) -> None:
    _, _, tools = recorded
    sheet = tools.contact_sheet(path=str(tmp_path / "any.mp4"), columns=2)
    assert Path(json.loads(sheet[0])["sheet"]).exists() and isinstance(sheet[1], McpImage)
    zoom = tools.frame_at(1.0, crop={"x": 0, "y": 0, "width": 50, "height": 40})
    assert len(zoom) == 2 and isinstance(zoom[1], McpImage)
    with pytest.raises(ValueError, match="does not take mode='timestamps'"):
        tools.contact_sheet(mode="timestamps")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")
def test_real_ffmpeg_changes_mode_skips_static_frames(tmp_path: Path) -> None:
    # 2 s of a still colour, then 1 s of a moving test pattern, then 2 s still again.
    source = tmp_path / "src.mp4"
    still = "color=c=navy:size=320x240:rate=10:duration=2"
    moving = "testsrc=size=320x240:rate=10:duration=1"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", still, "-f", "lavfi", "-i", moving,
         "-f", "lavfi", "-i", still, "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1[v]",
         "-map", "[v]", "-pix_fmt", "yuv420p", str(source)], check=True,
    )  # fmt: skip
    extractor = FrameExtractor("ffmpeg")
    changes = extractor.sample(source, FrameRequest(FrameMode.CHANGES, max_frames=100))
    assert 8 <= changes.found <= 14  # ~10 moving frames plus the switches; the 40 still frames are gone
    assert all(1.9 <= f.seconds <= 3.2 for f in changes.frames[1:])
    window = extractor.sample(source, FrameRequest(FrameMode.INTERVAL, start_seconds=1.0, end_seconds=3.0))
    assert [round(f.seconds) for f in window.frames] == [1, 2]
