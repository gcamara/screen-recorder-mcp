"""ffprobe/ffmpeg operations on finished recordings: probe, trim, GIF export."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from screen_recorder_mcp.proc import hidden_window_flags

CommandRunner = Callable[[list[str]], "subprocess.CompletedProcess[bytes]"]

# Stream-copied cuts land on keyframes; accept them only if the length is this close.
_COPY_TOLERANCE_SECONDS = 0.25
_GIF_HIGH = (15, 960)  # (fps, max width)
_GIF_LOW = (10, 480)


@dataclass(frozen=True)
class MediaProbe:
    duration_seconds: float
    width: int
    height: int
    has_audio: bool


def run_command(args: list[str]) -> "subprocess.CompletedProcess[bytes]":
    """Production CommandRunner: no console window, output captured, never raises on exit code."""
    return subprocess.run(
        args, stdin=subprocess.DEVNULL, capture_output=True,
        creationflags=hidden_window_flags(), check=False,
    )  # fmt: skip


def find_ffprobe() -> str:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise RuntimeError("ffprobe not found on PATH; expected it next to ffmpeg")
    return ffprobe


def parse_probe_json(raw: str, source: Path) -> MediaProbe:
    """ffprobe `-print_format json -show_format -show_streams` output → MediaProbe.

    Example: parse_probe_json(ffprobe_stdout, Path("a.mp4")).duration_seconds -> 4.02
    """
    document = json.loads(raw)
    streams: list[dict[str, object]] = document.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise RuntimeError(f"{source} has no video stream; expected an MP4 recording")
    duration = float(document.get("format", {}).get("duration") or video.get("duration") or 0)
    return MediaProbe(
        duration_seconds=duration,
        width=int(video["width"]),  # type: ignore[call-overload]
        height=int(video["height"]),  # type: ignore[call-overload]
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


def build_trim_args(
    ffmpeg: str, source: Path, start: float, end: float, output: Path, copy: bool
) -> list[str]:
    """Cut [start, end] seconds; `copy` keeps the streams, otherwise re-encodes for exactness.

    Example: build_trim_args("ffmpeg", Path("a.mp4"), 1, 3, Path("t.mp4"), True)[-1] -> "t.mp4"
    """
    codec = ["-c", "copy"] if copy else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                                         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k"]  # fmt: skip
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{end - start:.3f}",
        *codec, "-movflags", "+faststart", str(output),
    ]  # fmt: skip


def gif_scale_filter(fps: int, max_width: int) -> str:
    """Shared by both GIF passes so the palette matches the frames it colours.

    Example: gif_scale_filter(15, 960) -> "fps=15,scale='min(960,iw)':-2:flags=lanczos"
    """
    return f"fps={fps},scale='min({max_width},iw)':-2:flags=lanczos"


def build_palette_args(ffmpeg: str, source: Path, palette: Path, high_quality: bool) -> list[str]:
    fps, width = _GIF_HIGH if high_quality else _GIF_LOW
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-an",
        "-vf", f"{gif_scale_filter(fps, width)},palettegen=stats_mode=diff", str(palette),
    ]  # fmt: skip


def build_gif_args(
    ffmpeg: str, source: Path, palette: Path, output: Path, high_quality: bool
) -> list[str]:
    fps, width = _GIF_HIGH if high_quality else _GIF_LOW
    graph = f"[0:v]{gif_scale_filter(fps, width)}[v];[v][1:v]paletteuse=dither=sierra2_4a"
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-i", str(palette),
        "-an", "-filter_complex", graph, "-loop", "0", str(output),
    ]  # fmt: skip


class MediaTools:
    """ffmpeg/ffprobe wrapper; the runner is injected so tests need no binaries.

    Example: MediaTools("ffmpeg", "ffprobe").trim(Path("a.mp4"), 1.0, 3.0, Path("t.mp4"))
    """

    def __init__(self, ffmpeg: str, ffprobe: str, runner: CommandRunner = run_command) -> None:
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe
        self._run = runner

    def probe(self, path: Path) -> MediaProbe:
        args = [self._ffprobe, "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path)]  # fmt: skip
        result = self._run(args)
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe of {path} exited {result.returncode}: {_stderr(result)}")
        return parse_probe_json(result.stdout.decode(errors="replace"), path)

    def trim(self, source: Path, start: float, end: float, output: Path) -> None:
        """Stream copy from 0 s (keyframe-exact); anywhere else, or if the copy is off, re-encode."""
        if start == 0.0 and self._try_copy(source, end, output):
            return
        self._must_run(build_trim_args(self._ffmpeg, source, start, end, output, copy=False), output)

    def export_gif(self, source: Path, output: Path, high_quality: bool) -> None:
        with tempfile.TemporaryDirectory(prefix="srm-gif-") as scratch:
            palette = Path(scratch) / "palette.png"
            self._must_run(build_palette_args(self._ffmpeg, source, palette, high_quality), output)
            self._must_run(build_gif_args(self._ffmpeg, source, palette, output, high_quality), output)

    def _try_copy(self, source: Path, end: float, output: Path) -> bool:
        result = self._run(build_trim_args(self._ffmpeg, source, 0.0, end, output, copy=True))
        if result.returncode != 0 or not output.exists():
            return False
        return abs(self.probe(output).duration_seconds - end) <= _COPY_TOLERANCE_SECONDS

    def _must_run(self, args: list[str], output: Path) -> None:
        result = self._run(args)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg for {output} exited {result.returncode}: {_stderr(result)}")


def _stderr(result: "subprocess.CompletedProcess[bytes]") -> str:
    return (result.stderr or b"").decode(errors="replace").strip()
