"""Combine the silent MP4 and the WAV track into the final file."""

from __future__ import annotations

import subprocess
from pathlib import Path

from screen_recorder_mcp.proc import hidden_window_flags


def build_mux_args(ffmpeg: str, video: Path, audio: Path, output: Path) -> list[str]:
    """Copy the video, encode audio to AAC padded with silence to the video length.

    Example: build_mux_args("ffmpeg", Path("v.mp4"), Path("a.wav"), Path("o.mp4"))[-1] -> "o.mp4"
    """
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(video), "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-af", "apad", "-c:a", "aac", "-b:a", "160k", "-shortest",
        "-movflags", "+faststart", str(output),
    ]  # fmt: skip


def mux_audio(ffmpeg: str, video: Path, audio: Path, output: Path) -> None:
    result = subprocess.run(
        build_mux_args(ffmpeg, video, audio, output),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        creationflags=hidden_window_flags(),
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"ffmpeg mux of {video} + {audio} exited {result.returncode}: {stderr}")
