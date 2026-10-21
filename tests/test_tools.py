import asyncio
from pathlib import Path

import pytest

from screen_recorder_mcp.capture import AudioMode
from screen_recorder_mcp.frame_sources import CaptureMode
from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.region_picker import TkRegionPicker
from screen_recorder_mcp.server import INSTRUCTIONS, build_server
from screen_recorder_mcp.tools import RecorderTools, parse_choice
from tests.fakes import FakeRecorderService

EXPECTED_TOOLS = {
    "list_targets", "pick_region", "start_recording", "recording_status", "pause_recording",
    "resume_recording", "stop_recording", "cancel_recording", "list_recordings", "trim_recording", "export_gif",
}


@pytest.fixture
def fake() -> FakeRecorderService:
    return FakeRecorderService()


@pytest.fixture
def tools(fake: FakeRecorderService) -> RecorderTools:
    return RecorderTools(lambda: fake)


def test_server_registers_the_eleven_tools_and_instructions(fake: FakeRecorderService) -> None:
    server = build_server(lambda: fake)
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert names == EXPECTED_TOOLS
    assert "ASK the user" in INSTRUCTIONS and 'audio="none"' in INSTRUCTIONS and "delay_seconds" in INSTRUCTIONS


def test_server_call_tool_reaches_the_service(fake: FakeRecorderService) -> None:
    server = build_server(lambda: fake)
    asyncio.run(server.call_tool("start_recording", {"target": "Notepad", "delay_seconds": 3}))
    assert fake.requests[0].started_by == "ai" and fake.requests[0].delay_seconds == 3


def test_start_recording_maps_every_argument(tools: RecorderTools, fake: FakeRecorderService) -> None:
    status = tools.start_recording(
        "Notepad", {"x": 1, "y": 2, "width": 3, "height": 4}, 15, "mic", False, "~/clips", "cpu", 5
    )
    request = fake.requests[0]
    assert (request.region, request.fps, request.audio, request.capture) == (
        Rect(1, 2, 3, 4), 15, AudioMode.MIC, CaptureMode.CPU,
    )
    assert request.output_dir == Path("~/clips").expanduser() and not request.capture_cursor
    assert status["state"] == "recording" and status["started_by"] == "ai"


def test_start_recording_rejects_bad_audio_with_choices(tools: RecorderTools) -> None:
    with pytest.raises(ValueError, match=r"audio must be one of \['none', 'system', 'mic'\], got 'loud'"):
        tools.start_recording("Notepad", audio="loud")


def test_status_flags_recording_only_when_not_idle(tools: RecorderTools) -> None:
    assert tools.recording_status()["recording"] is False
    tools.start_recording("Notepad")
    assert tools.recording_status()["recording"] is True
    assert tools.pause_recording()["state"] == "paused"
    assert tools.resume_recording()["state"] == "recording"


def test_stop_returns_output_path_and_missing_file_has_no_size(tools: RecorderTools) -> None:
    tools.start_recording("Notepad")
    stopped = tools.stop_recording()
    assert stopped["output"] == str(Path("videos/rec.mp4")) and stopped["size_bytes"] is None
    assert stopped["duration_seconds"] == 12.5


def test_cancel_list_trim_gif_and_pick(tools: RecorderTools) -> None:
    tools.start_recording("Notepad")
    assert tools.cancel_recording() == {"cancelled": True}
    tools.start_recording("Notepad")
    recording_id = str(tools.stop_recording()["recording_id"])
    listed = tools.list_recordings(5)["recordings"]
    assert [item["recording_id"] for item in listed] == [recording_id]  # type: ignore[index]
    assert tools.trim_recording(recording_id, 1, 2)["derived_from"] == recording_id
    assert tools.export_gif(recording_id)["path"] == str(Path("videos/rec.gif"))
    assert tools.pick_region("Notepad")["region"] == {"x": 10, "y": 20, "width": 300, "height": 200}
    assert tools.list_targets()["windows"] == [{"window_id": 1, "title": "Notepad"}]


def test_parse_choice_accepts_valid_values() -> None:
    assert parse_choice(CaptureMode, "gpu", "capture") is CaptureMode.GPU


def test_tk_region_picker_delegates_to_the_injected_picker() -> None:
    seen: list[Rect] = []

    def fake_overlay(bounds: Rect) -> Rect | None:
        seen.append(bounds)
        return Rect(0, 0, 5, 5)

    assert TkRegionPicker(fake_overlay).pick_region(Rect(1, 1, 9, 9)) == Rect(0, 0, 5, 5)
    assert seen == [Rect(1, 1, 9, 9)]
