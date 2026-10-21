"""Real server on 127.0.0.1 + RemoteRecorderService against a FakeRecorderService."""

import http.client
import json
import threading
from pathlib import Path
from typing import Iterator

import pytest

from screen_recorder_mcp.capture import AudioMode
from screen_recorder_mcp.control import server as control_server
from screen_recorder_mcp.control.client import RemoteRecorderService, discover_running_app
from screen_recorder_mcp.control.discovery import ControlDiscovery, discovery_path, read_discovery, write_discovery
from screen_recorder_mcp.control.server import ControlServerHandle, serve_control
from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.service import RecorderEvent, RecorderEventKind, RecordingRequest, RecordingState
from tests.fakes import FakeRecorderService


@pytest.fixture
def fake() -> FakeRecorderService:
    return FakeRecorderService()


@pytest.fixture
def handle(fake: FakeRecorderService, tmp_path: Path) -> Iterator[ControlServerHandle]:
    running = serve_control(fake, tmp_path)
    yield running
    running.stop()


@pytest.fixture
def remote(handle: ControlServerHandle, tmp_path: Path) -> RemoteRecorderService:
    discovery = discover_running_app(tmp_path)
    assert discovery is not None
    return RemoteRecorderService(discovery, poll_timeout_seconds=0.3)


def raw_post(port: int, body: dict[str, object], token: str | None) -> tuple[int, dict[str, object]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {} if token is None else {"X-Control-Token": token}
    connection.request("POST", "/rpc", json.dumps(body), headers)
    reply = connection.getresponse()
    return reply.status, json.loads(reply.read())


def test_start_pause_resume_stop_and_history(remote: RemoteRecorderService, fake: FakeRecorderService) -> None:
    request = RecordingRequest("Notepad", Rect(1, 2, 30, 40), 24, AudioMode.MIC, started_by="ai", delay_seconds=3)
    assert remote.start(request).state == RecordingState.RECORDING
    assert fake.requests == [request]
    assert remote.pause().state == RecordingState.PAUSED
    assert remote.status().started_by == "ai"
    assert remote.resume().state == RecordingState.RECORDING
    info = remote.stop()
    assert info.started_by == "ai" and info.path == Path("videos/rec.mp4")
    assert remote.history(5) == [info]
    assert remote.status().state == RecordingState.IDLE


def test_targets_pick_region_trim_gif_delete(remote: RemoteRecorderService) -> None:
    assert remote.list_targets().monitors[0]["target"] == "monitor:1"
    assert remote.pick_region("Notepad") == ({"target": "Notepad", "window_id": 1}, Rect(10, 20, 300, 200))
    remote.start(RecordingRequest("Notepad"))
    info = remote.stop()
    trimmed = remote.trim(info.recording_id, 1.0, 3.0)
    assert trimmed.derived_from == info.recording_id and trimmed.duration_seconds == 2.0
    assert remote.export_gif(info.recording_id, True) == Path("videos/rec.hq.gif")
    assert remote.export_gif(info.recording_id, False, Path("x.gif")) == Path("x.gif")
    remote.delete_recording(trimmed.recording_id)
    assert [item.recording_id for item in remote.history()] == [info.recording_id]


def test_cancel_and_settings(remote: RemoteRecorderService) -> None:
    remote.start(RecordingRequest("Notepad"))
    remote.cancel()
    assert remote.status().state == RecordingState.IDLE and remote.history() == []
    assert remote.update_settings({"default_fps": 60, "output_dir": Path("clips")}).default_fps == 60
    assert remote.get_settings().output_dir == Path("clips")


def test_errors_keep_type_and_message(remote: RemoteRecorderService) -> None:
    with pytest.raises(LookupError, match="no window matches 'nope'"):
        remote.start(RecordingRequest("nope"))
    with pytest.raises(ValueError, match="delay_seconds must be >= 0, got -1"):
        remote.start(RecordingRequest("x", delay_seconds=-1))
    with pytest.raises(RuntimeError, match="no recording is running"):
        remote.stop()
    with pytest.raises(ValueError, match="unknown settings"):
        remote.update_settings({"bogus": 1})
    with pytest.raises(LookupError, match="no recording with id 'zzz'"):
        remote.trim("zzz", 0, 1)


def test_events_are_delivered_and_unsubscribe_stops_them(remote: RemoteRecorderService) -> None:
    received: list[RecorderEvent] = []
    got_stopped = threading.Event()

    def listener(event: RecorderEvent) -> None:
        received.append(event)
        if event.kind == RecorderEventKind.STOPPED:
            got_stopped.set()

    unsubscribe = remote.subscribe(listener)
    remote.start(RecordingRequest("Notepad"))
    remote.stop()
    assert got_stopped.wait(5)
    assert [event.kind for event in received] == [RecorderEventKind.STARTED, RecorderEventKind.STOPPED]
    assert received[-1].info is not None
    unsubscribe()
    remote.start(RecordingRequest("Notepad"))
    threading.Event().wait(0.8)
    assert len(received) == 2


def test_failure_event_carries_message(remote: RemoteRecorderService, fake: FakeRecorderService) -> None:
    got = threading.Event()
    seen: list[RecorderEvent] = []

    def listener(event: RecorderEvent) -> None:
        seen.append(event)
        got.set()

    unsubscribe = remote.subscribe(listener)
    fake.emit_failure("ffmpeg died")
    assert got.wait(5) and seen[0].message == "ffmpeg died"
    unsubscribe()


def test_requests_without_or_with_wrong_token_get_401(handle: ControlServerHandle) -> None:
    body = {"method": "status", "params": {}}
    assert raw_post(handle.port, body, None)[0] == 401
    assert raw_post(handle.port, body, "wrong")[0] == 401
    assert raw_post(handle.port, body, handle.token)[0] == 200


def test_non_loopback_clients_get_403(handle: ControlServerHandle, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(control_server, "_LOOPBACK_HOSTS", frozenset())
    status, reply = raw_post(handle.port, {"method": "status"}, handle.token)
    assert status == 403 and "non-loopback" in str(reply["error"])


def test_unknown_method_and_bad_params_are_value_errors(handle: ControlServerHandle) -> None:
    _, unknown = raw_post(handle.port, {"method": "format_disk"}, handle.token)
    assert unknown["error"]["type"] == "ValueError" and "format_disk" in unknown["error"]["message"]  # type: ignore[index]
    _, missing = raw_post(handle.port, {"method": "trim", "params": {}}, handle.token)
    assert "missing parameter 'recording_id'" in missing["error"]["message"]  # type: ignore[index]


def test_discovery_file_lifecycle(fake: FakeRecorderService, tmp_path: Path) -> None:
    running = serve_control(fake, tmp_path, app_version="9.9")
    stored = read_discovery(tmp_path)
    assert stored is not None and stored.port == running.port and stored.version == "9.9"
    assert len(stored.token) == 64 and stored.token == running.token
    running.stop()
    running.stop()  # idempotent
    assert not discovery_path(tmp_path).exists()
    assert discover_running_app(tmp_path) is None


def test_discover_returns_none_for_dead_pid_and_unresponsive_port(handle: ControlServerHandle, tmp_path: Path) -> None:
    live = read_discovery(tmp_path)
    assert live is not None
    write_discovery(tmp_path, ControlDiscovery(live.port, live.token, 2**22 + 7, live.version))
    assert discover_running_app(tmp_path) is None
    handle.stop()
    write_discovery(tmp_path, live)
    assert discover_running_app(tmp_path) is None


def test_discover_returns_none_for_wrong_token(handle: ControlServerHandle, tmp_path: Path) -> None:
    live = read_discovery(tmp_path)
    assert live is not None
    write_discovery(tmp_path, ControlDiscovery(live.port, "0" * 64, live.pid, live.version))
    assert discover_running_app(tmp_path) is None


def test_client_reports_unreachable_server(handle: ControlServerHandle, tmp_path: Path) -> None:
    discovery = discover_running_app(tmp_path)
    assert discovery is not None
    handle.stop()
    with pytest.raises(RuntimeError, match=f"127.0.0.1:{discovery.port} unreachable for 'status'"):
        RemoteRecorderService(discovery).status()
