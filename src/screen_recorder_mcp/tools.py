"""MCP tool bodies: thin translation between tool arguments and a RecorderService."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Callable, TypeVar

from mcp.server.mcpserver import Image

from screen_recorder_mcp.capture import AudioMode
from screen_recorder_mcp.frames import (
    FrameExtractor, FrameMode, FrameRequest, FrameSet, build_contact_sheet, format_seconds, inline_jpeg,
)
from screen_recorder_mcp.frame_sources import CaptureMode
from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.service import (
    RecorderService,
    RecordingInfo,
    RecordingRequest,
    RecordingState,
    to_jsonable,
)

JsonDict = dict[str, object]
EnumT = TypeVar("EnumT", bound=Enum)
ToolContent = list[str | Image]

# Claude downsizes anything past ~1568 px on the long edge, so wider images only cost tokens.
_MAX_INLINE_WIDTH = 1568
_MIN_INLINE_WIDTH = 64
_HISTORY_SCAN_LIMIT = 500


def parse_choice(enum_type: type[EnumT], raw: str, field_name: str) -> EnumT:
    """Enum from user text with a message that lists the valid choices.

    Example: parse_choice(AudioMode, "mic", "audio") -> AudioMode.MIC
    """
    try:
        return enum_type(raw)
    except ValueError:
        choices = [member.value for member in enum_type]
        raise ValueError(f"{field_name} must be one of {choices}, got {raw!r}") from None


def _info_dict(info: RecordingInfo) -> JsonDict:
    body = to_jsonable(info)
    assert isinstance(body, dict)
    size = info.path.stat().st_size if info.path.exists() else None
    return {**body, "output": str(info.path), "size_bytes": size}


def _frame_set_dict(source: Path, frame_set: FrameSet) -> JsonDict:
    return {
        "source": str(source),
        "found": frame_set.found,
        "returned": len(frame_set.frames),
        "directory": str(frame_set.directory),
        "frames": [
            {"index": i, "seconds": f.seconds, "time": format_seconds(f.seconds), "path": str(f.path)}
            for i, f in enumerate(frame_set.frames, start=1)
        ],
    }


def _clamp_width(width: int) -> int:
    return max(_MIN_INLINE_WIDTH, min(_MAX_INLINE_WIDTH, width))


class RecorderTools:
    """One method per MCP tool. `get_service` is called on every tool call (remote vs local)."""

    def __init__(
        self,
        get_service: Callable[[], RecorderService],
        get_extractor: Callable[[], FrameExtractor] | None = None,
    ) -> None:
        self._get_service = get_service
        self._get_extractor = get_extractor or _default_extractor

    def list_targets(self) -> JsonDict:
        """List recordable windows (front-most first) and monitors (`monitor:N`)."""
        return to_jsonable(self._get_service().list_targets())  # type: ignore[return-value]

    def pick_region(self, target: str) -> JsonDict:
        """Show a drag-to-crop overlay over the target so the USER selects the area.

        target: window title / process name / window_id, or `monitor:N`.
        Returns `region` relative to the target (null = user pressed Esc, record all).
        The OS may refuse to bring the app to the front; tell the user to keep the
        app visible before calling this, since the overlay is semi-transparent.
        """
        resolved, region = self._get_service().pick_region(target)
        return {"target": resolved, "region": None if region is None else region.as_dict()}

    def start_recording(
        self,
        target: str,
        region: dict[str, int] | None = None,
        fps: int = 30,
        audio: str = "none",
        capture_cursor: bool = True,
        output_dir: str | None = None,
        capture: str = "auto",
        delay_seconds: int = 0,
    ) -> JsonDict:
        """Start recording a window or monitor to MP4 (H.264).

        target: window title / process name / window_id, or `monitor:N` (see list_targets).
        region: optional {x, y, width, height} relative to the target, usually from pick_region.
        audio: "none" (default), "system" or "mic" — only when the user asked for audio.
        capture: "auto" (default: GPU capture, CPU if the GPU can't start), "gpu", or "cpu"
                 (Windows only; no GPU use at all, no cursor in the video).
        delay_seconds: countdown before capture starts (0 default; the user sees it on screen).
        The user sees the recording toolbar of the desktop app, if it is running.
        """
        request = RecordingRequest(
            target=target,
            region=None if region is None else Rect(**region),
            fps=fps,
            audio=parse_choice(AudioMode, audio, "audio"),
            capture_cursor=capture_cursor,
            capture=parse_choice(CaptureMode, capture, "capture"),
            output_dir=None if output_dir is None else Path(output_dir).expanduser(),
            started_by="ai",
            delay_seconds=delay_seconds,
        )
        return to_jsonable(self._get_service().start(request))  # type: ignore[return-value]

    def recording_status(self) -> JsonDict:
        """Report the running recording (state, elapsed time, countdown, whether the target closed)."""
        status = to_jsonable(self._get_service().status())
        assert isinstance(status, dict)
        return {"recording": status["state"] != RecordingState.IDLE.value, **status}

    def pause_recording(self) -> JsonDict:
        """Pause the running recording; paused time is not recorded."""
        return to_jsonable(self._get_service().pause())  # type: ignore[return-value]

    def resume_recording(self) -> JsonDict:
        """Resume a paused recording."""
        return to_jsonable(self._get_service().resume())  # type: ignore[return-value]

    def stop_recording(self) -> JsonDict:
        """Stop the running recording and return the final MP4 path (`output`)."""
        return _info_dict(self._get_service().stop())

    def cancel_recording(self) -> JsonDict:
        """Stop the running recording and delete the partial file."""
        self._get_service().cancel()
        return {"cancelled": True}

    def list_recordings(self, limit: int = 20) -> JsonDict:
        """Recording history, newest first, including recordings the user made by hand."""
        infos = self._get_service().history(limit)
        return {"recordings": [_info_dict(info) for info in infos]}

    def trim_recording(
        self, recording_id: str, start_seconds: float, end_seconds: float, crop: dict[str, int] | None = None,
        output_name: str | None = None, overwrite: bool = False,
    ) -> JsonDict:  # fmt: skip
        """Cut a recording to [start_seconds, end_seconds]; by default saves a new file and keeps the original.

        crop: optional {x, y, width, height} in video pixels to keep only that part of the picture
              (e.g. one panel of a window); frame_at with the same crop previews it.
        output_name: file name for the new copy, saved next to the original (default '<name>-trim.mp4').
        overwrite: replace the original file instead (same recording_id). Only when the user asked for it.
        """
        region = None if crop is None else Rect(**crop)
        info = self._get_service().trim(recording_id, start_seconds, end_seconds, region, output_name, overwrite)
        return _info_dict(info)

    def export_gif(self, recording_id: str, high_quality: bool = False, output: str | None = None) -> JsonDict:
        """Convert a recording to an animated GIF and return its path."""
        target = None if output is None else Path(output).expanduser()
        path = self._get_service().export_gif(recording_id, high_quality, target)
        return {"path": str(path)}

    def sample_frames(
        self,
        recording_id: str | None = None,
        path: str | None = None,
        mode: str = "changes",
        max_frames: int = 12,
        start_seconds: float | None = None,
        end_seconds: float | None = None,
        interval_seconds: float = 1.0,
        scene_threshold: float = 0.1,
        timestamps: list[float] | None = None,
        width: int = 800,
        inline: bool = True,
    ) -> ToolContent:
        """Look at a recording frame by frame: returns the frames as images plus their times.

        Source: recording_id (see list_recordings) or path to any video file; neither = the latest recording.
        mode: "changes" (default; skips frames identical to the last kept one, so idle time
              costs nothing), "scenes" (big changes only, scene_threshold 0..1),
              "interval" (one frame every interval_seconds) or "timestamps" (exact `timestamps`).
        max_frames: frames returned; more matches are thinned evenly ("found" says how many matched).
              Narrow start_seconds/end_seconds to look closer at one moment.
        width: max width of the inline images (token cost grows with size). Full-size PNGs stay
              on disk at the returned paths; inline=False returns only the paths.
        Start with contact_sheet for an overview: it is one image instead of many.
        """
        source = self._resolve_source(recording_id, path)
        request = FrameRequest(
            mode=parse_choice(FrameMode, mode, "mode"), interval_seconds=interval_seconds,
            scene_threshold=scene_threshold, timestamps=tuple(timestamps or ()),
            start_seconds=start_seconds, end_seconds=end_seconds, max_frames=max_frames,
        )  # fmt: skip
        frame_set = self._get_extractor().sample(source, request)
        content: ToolContent = [json.dumps(_frame_set_dict(source, frame_set))]
        if inline:
            limit = _clamp_width(width)
            content += [Image(data=inline_jpeg(f.path, limit), format="jpeg") for f in frame_set.frames]
        return content

    def contact_sheet(
        self,
        recording_id: str | None = None,
        path: str | None = None,
        mode: str = "changes",
        max_frames: int = 12,
        columns: int = 4,
        tile_width: int = 380,
        start_seconds: float | None = None,
        end_seconds: float | None = None,
        interval_seconds: float = 1.0,
        scene_threshold: float = 0.1,
    ) -> ToolContent:
        """One overview image: a grid of frames, each captioned with its number and time.

        Same source/mode/range arguments as sample_frames, except mode="timestamps". Cheapest way
        to see what happened over the whole recording; then use sample_frames or frame_at on the
        interesting moment for full detail.
        """
        source = self._resolve_source(recording_id, path)
        request = FrameRequest(
            mode=parse_choice(FrameMode, mode, "mode"), interval_seconds=interval_seconds,
            scene_threshold=scene_threshold, start_seconds=start_seconds, end_seconds=end_seconds,
            max_frames=max_frames,
        )  # fmt: skip
        if request.mode is FrameMode.TIMESTAMPS:
            raise ValueError("contact_sheet does not take mode='timestamps'; use sample_frames for exact times")
        frame_set = self._get_extractor().sample(source, request)
        sheet = build_contact_sheet(frame_set.frames, columns, tile_width, frame_set.directory / "sheet.png")
        body = {**_frame_set_dict(source, frame_set), "sheet": str(sheet)}
        return [json.dumps(body), Image(data=inline_jpeg(sheet, _MAX_INLINE_WIDTH), format="jpeg")]

    def frame_at(
        self,
        seconds: float,
        recording_id: str | None = None,
        path: str | None = None,
        crop: dict[str, int] | None = None,
        width: int = _MAX_INLINE_WIDTH,
    ) -> ToolContent:
        """One frame at `seconds`, optionally cropped to {x, y, width, height} in video pixels.

        Use it to zoom into a detail (small text, an icon) that a contact sheet shows too small.
        """
        source = self._resolve_source(recording_id, path)
        request = FrameRequest(mode=FrameMode.TIMESTAMPS, timestamps=(seconds,), max_frames=1)
        frame_set = self._get_extractor().sample(source, request)
        region = None if crop is None else Rect(**crop)
        image = Image(data=inline_jpeg(frame_set.frames[0].path, _clamp_width(width), region), format="jpeg")
        return [json.dumps(_frame_set_dict(source, frame_set)), image]

    def _resolve_source(self, recording_id: str | None, path: str | None) -> Path:
        if recording_id is not None and path is not None:
            raise ValueError("pass either recording_id or path, not both")
        if path is not None:
            return Path(path).expanduser()
        history = self._get_service().history(_HISTORY_SCAN_LIMIT)
        if recording_id is None:
            if not history:
                raise ValueError("no recordings yet; record one first or pass `path` to a video file")
            return history[0].path
        for info in history:
            if info.recording_id == recording_id:
                return info.path
        raise ValueError(f"recording_id {recording_id!r} not found; call list_recordings for valid ids")


def _default_extractor() -> FrameExtractor:
    # Imported lazily like the local service: tests and the remote path never need ffmpeg on PATH.
    from screen_recorder_mcp.session_factory import find_ffmpeg

    return FrameExtractor(find_ffmpeg())
