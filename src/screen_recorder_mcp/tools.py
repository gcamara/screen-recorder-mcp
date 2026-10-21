"""MCP tool bodies: thin translation between tool arguments and a RecorderService."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Callable, TypeVar

from screen_recorder_mcp.capture import AudioMode
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


class RecorderTools:
    """One method per MCP tool. `get_service` is called on every tool call (remote vs local)."""

    def __init__(self, get_service: Callable[[], RecorderService]) -> None:
        self._get_service = get_service

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

    def trim_recording(self, recording_id: str, start_seconds: float, end_seconds: float) -> JsonDict:
        """Cut a recording to [start_seconds, end_seconds]; saves a new file, the original is kept."""
        return _info_dict(self._get_service().trim(recording_id, start_seconds, end_seconds))

    def export_gif(self, recording_id: str, high_quality: bool = False, output: str | None = None) -> JsonDict:
        """Convert a recording to an animated GIF and return its path."""
        target = None if output is None else Path(output).expanduser()
        path = self._get_service().export_gif(recording_id, high_quality, target)
        return {"path": str(path)}
