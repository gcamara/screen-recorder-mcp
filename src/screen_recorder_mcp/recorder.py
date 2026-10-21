"""One recording session: frame source → pacer → encoder, plus optional audio."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from screen_recorder_mcp.capture import AudioRecorder, FrameSource
from screen_recorder_mcp.encoder import VideoEncoder
from screen_recorder_mcp.frame_pacer import FramePacer, LatestFrameSlot
from screen_recorder_mcp.geometry import Rect, clamp_crop, even_floor
from screen_recorder_mcp.targets import CaptureTarget

_log = logging.getLogger(__name__)
_FIRST_FRAME_TIMEOUT_SECONDS = 5.0

Muxer = Callable[[Path, Path, Path], None]


@dataclass(frozen=True)
class RecordingPlan:
    target: CaptureTarget
    region: Rect | None
    fps: int
    output: Path
    video_only_path: Path
    audio_path: Path | None


@dataclass
class RecordingParts:
    """The swappable I/O pieces, injected so tests can use fakes."""

    frame_source: FrameSource
    encoder: VideoEncoder
    audio: AudioRecorder | None
    muxer: Muxer


class RecordingSession:
    """Start/stop lifecycle of a single recording.

    Example:
        session = RecordingSession(plan, parts)
        session.start(); ...; output_path = session.stop()
    """

    def __init__(self, plan: RecordingPlan, parts: RecordingParts) -> None:
        self.plan = plan
        self._parts = parts
        self._slot = LatestFrameSlot()
        self._stop_pacing = threading.Event()
        self._pacer_thread: threading.Thread | None = None
        self._pacer: FramePacer | None = None
        self.started_at = 0.0
        self.target_closed = threading.Event()

    def start(self) -> None:
        self._parts.frame_source.start(self._slot.put, self.target_closed.set)
        if not self._slot.first_frame.wait(_FIRST_FRAME_TIMEOUT_SECONDS):
            self._parts.frame_source.stop()
            raise RuntimeError(
                f"no frame from {self.plan.target.label} within {_FIRST_FRAME_TIMEOUT_SECONDS}s; "
                "is the window minimized or closed?"
            )
        self._start_pipeline(self._first_frame_size())

    def stop(self) -> Path:
        self._stop_pacing.set()
        if self._pacer_thread is not None:
            self._pacer_thread.join(timeout=10)
        try:
            self._parts.frame_source.stop()
        finally:
            # Always finalize ffmpeg: an unclosed stdin leaves an unplayable MP4
            # (e.g. when the OS capture API errors because the window closed).
            self._parts.encoder.close()
        self._stop_audio_and_mux()
        return self.plan.output

    def status(self) -> dict[str, object]:
        frames = 0 if self._pacer is None else self._pacer.frames_written
        return {
            "target": self.plan.target.as_dict(),
            "region": None if self.plan.region is None else self.plan.region.as_dict(),
            "elapsed_seconds": round(time.perf_counter() - self.started_at, 1),
            "frames_written": frames,
            "target_closed": self.target_closed.is_set(),
            "output": str(self.plan.output),
        }

    def _first_frame_size(self) -> tuple[int, int]:
        frame = self._slot.get()
        assert frame is not None  # guaranteed by first_frame.wait()
        return frame.shape[1], frame.shape[0]

    def _start_pipeline(self, frame_size: tuple[int, int]) -> None:
        region = _region_in_frame_pixels(self.plan.region, self.plan.target.rect, *frame_size)
        crop, output_size = _output_geometry(region, *frame_size)
        self._parts.encoder.open(*output_size, self.plan.fps)
        self._pacer = FramePacer(self._slot, self._parts.encoder.write, self.plan.fps, crop, output_size)
        self.started_at = time.perf_counter()
        self._start_audio_or_abort()
        self._pacer_thread = threading.Thread(
            target=self._pacer.run, args=(self._stop_pacing, self.started_at), daemon=True
        )
        self._pacer_thread.start()
        _log.info('{"event": "recording_started", "output": "%s"}', self.plan.output.as_posix())

    def _start_audio_or_abort(self) -> None:
        if self._parts.audio is None:
            return
        try:
            self._parts.audio.start()
        except Exception:
            # Don't leave capture and ffmpeg running for a session nobody can stop.
            self._parts.frame_source.stop()
            self._parts.encoder.close()
            raise

    def _stop_audio_and_mux(self) -> None:
        audio, audio_path = self._parts.audio, self.plan.audio_path
        if audio is None or audio_path is None:
            return
        audio.stop()
        self._parts.muxer(self.plan.video_only_path, audio_path, self.plan.output)
        self.plan.video_only_path.unlink(missing_ok=True)
        audio_path.unlink(missing_ok=True)


def _region_in_frame_pixels(
    region: Rect | None, target_rect: Rect, frame_width: int, frame_height: int
) -> Rect | None:
    """Map a region in desktop units onto frame pixels.

    Windows desktop units are already pixels (scale 1); macOS uses points, and
    a Retina frame has 2 pixels per point. Deriving the scale from the first
    frame avoids asking each OS for its backing scale factor.

    Example: _region_in_frame_pixels(Rect(10, 10, 50, 40), Rect(0, 0, 100, 80), 200, 160)
             -> Rect(20, 20, 100, 80)
    """
    if region is None or target_rect.width <= 0 or target_rect.height <= 0:
        return region
    scale_x = frame_width / target_rect.width
    scale_y = frame_height / target_rect.height
    return Rect(
        round(region.x * scale_x),
        round(region.y * scale_y),
        round(region.width * scale_x),
        round(region.height * scale_y),
    )


def _output_geometry(
    region: Rect | None, frame_width: int, frame_height: int
) -> tuple[Rect | None, tuple[int, int]]:
    if region is None:
        return None, (even_floor(frame_width), even_floor(frame_height))
    crop = clamp_crop(region, frame_width, frame_height)
    return crop, (crop.width, crop.height)
