from datetime import datetime
from pathlib import Path

import pytest

from screen_recorder_mcp.capture import AudioMode
from screen_recorder_mcp.control import codec
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
    TargetList,
    to_jsonable,
)

STATUS = RecordingStatus(
    state=RecordingState.PAUSED, recording_id="r1", target_label="Notepad", region=Rect(1, 2, 30, 40),
    elapsed_seconds=3.5, countdown_remaining=2, audio=AudioMode.MIC, started_by="ai", output=Path("a.mp4"),
)
INFO = RecordingInfo("r1", Path("a.mp4"), datetime(2026, 10, 21, 9, 30), 4.0, 640, 480, True, "Notepad", "ai", "r0")


def test_request_round_trip() -> None:
    request = RecordingRequest("Notepad", Rect(0, 0, 10, 10), 24, AudioMode.SYSTEM, False, CaptureMode.CPU,
                               Path("out"), "ai", 5)
    assert codec.request_from_json(to_jsonable(request)) == request


def test_request_defaults_and_missing_target() -> None:
    assert codec.request_from_json({"target": "x"}) == RecordingRequest("x")
    with pytest.raises(ValueError, match="needs 'target'"):
        codec.request_from_json({"fps": 30})


def test_status_info_event_round_trip() -> None:
    assert codec.status_from_json(to_jsonable(STATUS)) == STATUS
    assert codec.info_from_json(to_jsonable(INFO)) == INFO
    event = RecorderEvent(RecorderEventKind.STOPPED, STATUS, INFO, "done")
    assert codec.event_from_json(to_jsonable(event)) == event


def test_settings_and_targets_round_trip() -> None:
    settings = RecorderSettings(Path("v"), 60, True, capture_mode=CaptureMode.GPU, theme="dark")
    assert codec.settings_from_json(to_jsonable(settings)) == settings
    targets = TargetList(windows=[{"title": "a"}], monitors=[{"target": "monitor:1"}])
    assert codec.targets_from_json(to_jsonable(targets)) == targets


def test_rect_parsing_reports_missing_keys() -> None:
    assert codec.rect_from_json(None) is None
    assert codec.rect_from_json({"x": 1, "y": 2, "width": 3, "height": 4}) == Rect(1, 2, 3, 4)
    with pytest.raises(ValueError, match=r"missing \['height'\]"):
        codec.rect_from_json({"x": 1, "y": 2, "width": 3})
    with pytest.raises(ValueError, match="must be a JSON object"):
        codec.rect_from_json([1, 2, 3, 4])
