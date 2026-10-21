"""JSON -> dataclass parsing: the inverse of service.to_jsonable for the wire protocol."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Mapping

from screen_recorder_mcp.capture import AudioMode
from screen_recorder_mcp.frame_sources import CaptureMode
from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.service import (
    RecorderEvent,
    RecorderEventKind,
    RecorderSettings,
    RecordingInfo,
    RecordingRequest,
    RecordingState,
    RecordingStatus,
    StartedBy,
    TargetList,
)

JsonObject = Mapping[str, Any]


def _require(raw: object, what: str) -> JsonObject:
    if not isinstance(raw, dict):
        raise ValueError(f"{what} must be a JSON object, got {raw!r}")
    return raw


def _optional_path(raw: object) -> Path | None:
    return None if raw is None else Path(str(raw))


def _started_by(raw: object) -> StartedBy:
    return "ai" if raw == "ai" else "user"


def rect_from_json(raw: object) -> Rect | None:
    """Rect from {"x","y","width","height"}; None stays None.

    Example: rect_from_json({"x": 0, "y": 0, "width": 4, "height": 2}) -> Rect(0, 0, 4, 2)
    """
    if raw is None:
        return None
    body = _require(raw, "region")
    missing = [key for key in ("x", "y", "width", "height") if key not in body]
    if missing:
        raise ValueError(f"region is missing {missing}; expected {{x, y, width, height}}, got {raw!r}")
    return Rect(int(body["x"]), int(body["y"]), int(body["width"]), int(body["height"]))


def request_from_json(raw: object) -> RecordingRequest:
    body = _require(raw, "recording request")
    if "target" not in body:
        raise ValueError(f"recording request needs 'target', got keys {sorted(body)}")
    return RecordingRequest(
        target=str(body["target"]),
        region=rect_from_json(body.get("region")),
        fps=int(body.get("fps", 30)),
        audio=AudioMode(body.get("audio", "none")),
        capture_cursor=bool(body.get("capture_cursor", True)),
        capture=CaptureMode(body.get("capture", "auto")),
        output_dir=_optional_path(body.get("output_dir")),
        started_by=_started_by(body.get("started_by")),
        delay_seconds=int(body.get("delay_seconds", 0)),
    )


def status_from_json(raw: object) -> RecordingStatus:
    body = _require(raw, "recording status")
    started_by = body.get("started_by")
    return RecordingStatus(
        state=RecordingState(body["state"]),
        recording_id=body.get("recording_id"),
        target_label=body.get("target_label"),
        region=rect_from_json(body.get("region")),
        elapsed_seconds=float(body.get("elapsed_seconds", 0.0)),
        countdown_remaining=int(body.get("countdown_remaining", 0)),
        audio=AudioMode(body.get("audio", "none")),
        started_by=None if started_by is None else _started_by(started_by),
        output=_optional_path(body.get("output")),
        target_closed=bool(body.get("target_closed", False)),
    )


def info_from_json(raw: object) -> RecordingInfo:
    body = _require(raw, "recording info")
    return RecordingInfo(
        recording_id=str(body["recording_id"]),
        path=Path(str(body["path"])),
        created_at=datetime.fromisoformat(str(body["created_at"])),
        duration_seconds=float(body["duration_seconds"]),
        width=int(body["width"]),
        height=int(body["height"]),
        has_audio=bool(body["has_audio"]),
        target_label=str(body["target_label"]),
        started_by=_started_by(body.get("started_by")),
        derived_from=body.get("derived_from"),
    )


def settings_from_json(raw: object) -> RecorderSettings:
    body = dict(_require(raw, "settings"))
    body["output_dir"] = Path(str(body["output_dir"]))
    body["capture_mode"] = CaptureMode(body.get("capture_mode", "auto"))
    return RecorderSettings(**body)


def targets_from_json(raw: object) -> TargetList:
    body = _require(raw, "target list")
    return TargetList(windows=list(body.get("windows", [])), monitors=list(body.get("monitors", [])))


def event_from_json(raw: object) -> RecorderEvent:
    body = _require(raw, "event")
    info = body.get("info")
    return RecorderEvent(
        kind=RecorderEventKind(body["kind"]),
        status=status_from_json(body["status"]),
        info=None if info is None else info_from_json(info),
        message=body.get("message"),
    )
