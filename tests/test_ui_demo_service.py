"""The demo/fake service honours the RecorderService contract the UI relies on."""

from __future__ import annotations

from pathlib import Path

import pytest

from screen_recorder_mcp.service import RecorderEventKind, RecordingRequest, RecordingState
from tests.ui_fakes import UiFakeRecorderService


def test_countdown_then_recording_then_stop_adds_history(tmp_path: Path) -> None:
    service = UiFakeRecorderService(tmp_path, seed_history=False)
    kinds: list[RecorderEventKind] = []
    service.subscribe(lambda event: kinds.append(event.kind))
    service.start(RecordingRequest(target="monitor:1", delay_seconds=2))
    service.tick(1.0)
    assert service.status().countdown_remaining == 1
    service.tick(1.0)
    service.tick(2.5)
    assert service.status().state is RecordingState.RECORDING
    assert service.status().elapsed_seconds == 2.5
    info = service.stop()
    assert service.history()[0] == info and service.status().state is RecordingState.IDLE
    assert kinds == [RecorderEventKind.COUNTDOWN, RecorderEventKind.COUNTDOWN, RecorderEventKind.STARTED,
                     RecorderEventKind.STOPPED]


def test_state_conflicts_raise_with_offending_value(tmp_path: Path) -> None:
    service = UiFakeRecorderService(tmp_path, seed_history=False)
    with pytest.raises(RuntimeError, match="'idle'"):
        service.pause()
    service.start(RecordingRequest(target="monitor:1"))
    with pytest.raises(RuntimeError, match="'recording'"):
        service.start(RecordingRequest(target="monitor:1"))
    with pytest.raises(LookupError, match="nope"):
        service.delete_recording("nope")


def test_unsubscribe_and_settings_validation(tmp_path: Path) -> None:
    service = UiFakeRecorderService(tmp_path, seed_history=False)
    seen: list[object] = []
    unsubscribe = service.subscribe(seen.append)
    unsubscribe()
    service.update_settings({"default_fps": 15})
    assert seen == [] and service.get_settings().default_fps == 15
    with pytest.raises(ValueError, match="bogus"):
        service.update_settings({"bogus": 1})


def test_trim_validates_range_and_records_origin(tmp_path: Path) -> None:
    service = UiFakeRecorderService(tmp_path)
    source = service.history()[0]
    copy = service.trim(source.recording_id, 1.0, 3.0)
    assert copy.duration_seconds == 2.0 and copy.derived_from == source.recording_id
    with pytest.raises(ValueError, match="trim range"):
        service.trim(source.recording_id, 3.0, 1.0)
