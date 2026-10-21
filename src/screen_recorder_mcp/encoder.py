"""ffmpeg subprocess that turns raw BGRA frames into an H.264 MP4."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import IO, Protocol

from screen_recorder_mcp.geometry import FrameArray
from screen_recorder_mcp.proc import hidden_window_flags


class VideoEncoder(Protocol):
    def open(self, width: int, height: int, fps: int) -> None: ...
    def write(self, frame: FrameArray) -> None: ...
    def close(self) -> None: ...


def build_ffmpeg_args(ffmpeg: str, width: int, height: int, fps: int, output: Path) -> list[str]:
    """Command line for a constant-fps rawvideo pipe → MP4.

    Example: build_ffmpeg_args("ffmpeg", 640, 480, 30, Path("a.mp4"))[-1] -> "a.mp4"
    """
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{width}x{height}",
        "-framerate", str(fps), "-i", "-",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(output),
    ]  # fmt: skip


class FfmpegEncoder:
    """Writes frames to ffmpeg's stdin; ffmpeg's stderr goes to a log file.

    ffmpeg must never write to our stdout: in an MCP stdio server stdout is the
    protocol channel.
    """

    def __init__(self, ffmpeg: str, output: Path, log_path: Path) -> None:
        self._ffmpeg = ffmpeg
        self._output = output
        self._log_path = log_path
        self._process: subprocess.Popen[bytes] | None = None
        self._log: IO[bytes] | None = None

    def open(self, width: int, height: int, fps: int) -> None:
        self._log = self._log_path.open("wb")
        self._process = subprocess.Popen(
            build_ffmpeg_args(self._ffmpeg, width, height, fps, self._output),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=self._log,
            creationflags=hidden_window_flags(),
        )

    def write(self, frame: FrameArray) -> None:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError(f"encoder for {self._output} written before open()")
        self._process.stdin.write(frame.tobytes())

    def close(self) -> None:
        if self._process is None:
            return
        if self._process.stdin is not None:
            self._process.stdin.close()
        exit_code = self._process.wait(timeout=120)
        if self._log is not None:
            self._log.close()
        if exit_code != 0:
            raise RuntimeError(
                f"ffmpeg exited {exit_code} for {self._output}; see {self._log_path}"
            )
