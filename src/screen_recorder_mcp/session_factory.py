"""Wire the running OS's backend and ffmpeg into a RecordingSession."""

from __future__ import annotations

import shutil
from datetime import datetime
from functools import partial
from pathlib import Path

from screen_recorder_mcp.backends import PlatformBackend
from screen_recorder_mcp.capture import AudioMode
from screen_recorder_mcp.encoder import FfmpegEncoder
from screen_recorder_mcp.frame_sources import CaptureMode
from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.muxer import mux_audio
from screen_recorder_mcp.recorder import RecordingParts, RecordingPlan, RecordingSession
from screen_recorder_mcp.targets import CaptureTarget

DEFAULT_OUTPUT_DIR = Path.home() / "Videos" / "screen-recorder-mcp"
if not DEFAULT_OUTPUT_DIR.parent.exists():
    # macOS calls it ~/Movies; ~/Videos only exists on Windows.
    DEFAULT_OUTPUT_DIR = Path.home() / "Movies" / "screen-recorder-mcp"


def find_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError(
            "ffmpeg not found on PATH; expected ffmpeg (winget install ffmpeg / brew install ffmpeg)"
        )
    return ffmpeg


def plan_recording(
    target: CaptureTarget, region: Rect | None, fps: int, audio: AudioMode, output_dir: Path
) -> RecordingPlan:
    """Decide every file path up front so status() can report them.

    Example: plan_recording(target, None, 30, AudioMode.NONE, DEFAULT_OUTPUT_DIR).output
             -> ~/Videos/screen-recorder-mcp/rec-20261021-153000.mp4
    """
    if not 1 <= fps <= 120:
        raise ValueError(f"fps={fps} out of range; expected 1..120")
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"rec-{datetime.now():%Y%m%d-%H%M%S}"
    output = output_dir / f"{stem}.mp4"
    with_audio = audio is not AudioMode.NONE
    return RecordingPlan(
        target=target,
        region=region,
        fps=fps,
        output=output,
        video_only_path=output_dir / f"{stem}.video.mp4" if with_audio else output,
        audio_path=output_dir / f"{stem}.wav" if with_audio else None,
    )


def build_session(
    backend: PlatformBackend,
    plan: RecordingPlan,
    audio: AudioMode,
    capture_cursor: bool,
    capture_mode: CaptureMode = CaptureMode.AUTO,
) -> RecordingSession:
    ffmpeg = find_ffmpeg()
    log_path = plan.output.with_suffix(".ffmpeg.log")
    audio_recorder = None
    if plan.audio_path is not None:
        audio_recorder = backend.audio_recorder(audio, plan.target, plan.audio_path)
    parts = RecordingParts(
        frame_source=backend.frame_source(plan.target, capture_cursor, capture_mode),
        encoder=FfmpegEncoder(ffmpeg, plan.video_only_path, log_path),
        audio=audio_recorder,
        muxer=partial(mux_audio, ffmpeg),
    )
    return RecordingSession(plan, parts)
