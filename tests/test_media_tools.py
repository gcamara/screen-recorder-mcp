import shutil
import subprocess
from pathlib import Path

import pytest

from screen_recorder_mcp.media_tools import (
    MediaTools, build_gif_args, build_palette_args, build_trim_args, find_ffprobe,
    gif_scale_filter, parse_probe_json,
)
from tests.fakes import FakeCommandRunner, probe_json


def make_tools(replies: list[tuple[int, bytes]]) -> tuple[MediaTools, FakeCommandRunner]:
    runner = FakeCommandRunner(replies)
    return MediaTools("ffmpeg", "ffprobe", runner), runner


def test_parse_probe_reads_duration_size_and_audio() -> None:
    probe = parse_probe_json(probe_json(4.02, 640, 480, audio=True).decode(), Path("a.mp4"))
    assert (probe.duration_seconds, probe.width, probe.height, probe.has_audio) == (4.02, 640, 480, True)


def test_parse_probe_rejects_file_without_video() -> None:
    with pytest.raises(RuntimeError, match="a.mp4 has no video stream"):
        parse_probe_json('{"streams": [{"codec_type": "audio"}]}', Path("a.mp4"))


def test_trim_args_copy_vs_reencode() -> None:
    copy = build_trim_args("ffmpeg", Path("a.mp4"), 0, 3, Path("t.mp4"), copy=True)
    exact = build_trim_args("ffmpeg", Path("a.mp4"), 1, 3, Path("t.mp4"), copy=False)
    assert copy[copy.index("-c") + 1] == "copy" and "-c:v" not in copy
    assert exact[exact.index("-ss") + 1] == "1.000" and exact[exact.index("-t") + 1] == "2.000"
    assert "libx264" in exact and exact[-1] == "t.mp4"


def test_gif_scale_filter_caps_width() -> None:
    assert gif_scale_filter(15, 960) == "fps=15,scale='min(960,iw)':-2:flags=lanczos"


def test_gif_quality_presets() -> None:
    high = build_palette_args("ffmpeg", Path("a.mp4"), Path("p.png"), True)
    low = build_palette_args("ffmpeg", Path("a.mp4"), Path("p.png"), False)
    assert "fps=15,scale='min(960,iw)'" in high[high.index("-vf") + 1]
    assert "fps=10,scale='min(480,iw)'" in low[low.index("-vf") + 1]


def test_gif_second_pass_applies_palette() -> None:
    args = build_gif_args("ffmpeg", Path("a.mp4"), Path("p.png"), Path("o.gif"), True)
    assert "paletteuse" in args[args.index("-filter_complex") + 1] and args[-1] == "o.gif"


def test_probe_failure_names_file_and_exit_code() -> None:
    tools, _ = make_tools([(1, b"")])
    with pytest.raises(RuntimeError, match="ffprobe of x.mp4 exited 1: boom"):
        tools.probe(Path("x.mp4"))


def test_trim_from_zero_uses_stream_copy_when_length_matches(tmp_path: Path) -> None:
    tools, runner = make_tools([(0, b""), (0, probe_json(3.0))])
    tools.trim(Path("a.mp4"), 0.0, 3.0, tmp_path / "t.mp4")
    assert len(runner.commands) == 2 and "copy" in runner.commands[0]


def test_trim_falls_back_to_reencode_when_copy_is_off(tmp_path: Path) -> None:
    tools, runner = make_tools([(0, b""), (0, probe_json(5.0)), (0, b"")])
    tools.trim(Path("a.mp4"), 0.0, 3.0, tmp_path / "t.mp4")
    assert "libx264" in runner.commands[-1]


def test_trim_from_middle_reencodes_straight_away(tmp_path: Path) -> None:
    tools, runner = make_tools([])
    tools.trim(Path("a.mp4"), 1.0, 3.0, tmp_path / "t.mp4")
    assert len(runner.commands) == 1 and "libx264" in runner.commands[0]


def test_trim_failure_reports_stderr(tmp_path: Path) -> None:
    tools, _ = make_tools([(2, b"")])
    with pytest.raises(RuntimeError, match="exited 2: boom"):
        tools.trim(Path("a.mp4"), 1.0, 3.0, tmp_path / "t.mp4")


def test_gif_runs_palette_pass_then_render_pass(tmp_path: Path) -> None:
    tools, runner = make_tools([])
    tools.export_gif(Path("a.mp4"), tmp_path / "o.gif", high_quality=False)
    first, second = runner.commands
    assert "palettegen" in first[first.index("-vf") + 1]
    assert "paletteuse" in second[second.index("-filter_complex") + 1]


def test_find_ffprobe_names_the_missing_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="ffprobe not found on PATH"):
        find_ffprobe()


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg missing"
)
def test_real_ffmpeg_trim_and_gif(tmp_path: Path) -> None:
    source = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=4",
         "-pix_fmt", "yuv420p", str(source)], check=True,
    )  # fmt: skip
    tools = MediaTools("ffmpeg", "ffprobe")
    tools.trim(source, 1.0, 3.0, tmp_path / "t.mp4")
    tools.export_gif(tmp_path / "t.mp4", tmp_path / "t.gif", high_quality=True)
    assert tools.probe(tmp_path / "t.mp4").duration_seconds == pytest.approx(2.0, abs=0.2)
    assert (tmp_path / "t.gif").read_bytes()[:3] == b"GIF"
