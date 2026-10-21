import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import pytest

from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.service import (
    RecorderEventKind, RecordingRequest, RecordingState, RecorderService,
)
from screen_recorder_mcp.service_local import LocalRecorderService
from tests.fakes import (
    ExplodingListener, FakeBackend, FakeMediaTools, FakeRegionPicker, FakeSessionBuilder,
    GatedWait, ListenerLog, instant_wait, window,
)

NOW = datetime(2026, 10, 21, 15, 30, 0)
KINDS = RecorderEventKind


class Rig:
    """A service wired to fakes, plus handles tests assert on."""

    def __init__(self, tmp_path: Path, **overrides: object) -> None:
        self.backend = FakeBackend([window(7, "Core demo", "python.exe")])
        self.picker = FakeRegionPicker(Rect(5, 5, 50, 40))
        self.builder = FakeSessionBuilder(bool(overrides.pop("fail_start", False)))
        self.media = FakeMediaTools()
        self.service = LocalRecorderService(
            self.backend, self.picker, tmp_path / "data",
            media_tools=self.media, session_builder=self.builder,  # type: ignore[arg-type]
            clock=lambda: NOW, countdown_wait=overrides.pop("wait", instant_wait),  # type: ignore[arg-type]
            default_output_dir=tmp_path / "videos",
        )
        self.log = ListenerLog()
        self.service.subscribe(self.log)

    def kinds(self) -> list[RecorderEventKind]:
        self.service.flush_events()
        return self.log.kinds()


def request(**fields: object) -> RecordingRequest:
    return RecordingRequest(target="Core demo", fps=10, **fields)  # type: ignore[arg-type]


def wait_until(condition: Callable[[], bool], timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.005)


def test_it_satisfies_the_recorder_service_protocol(tmp_path: Path) -> None:
    service: RecorderService = Rig(tmp_path).service
    assert service.status().state is RecordingState.IDLE


def test_list_targets_has_windows_and_monitors(tmp_path: Path) -> None:
    targets = Rig(tmp_path).service.list_targets()
    assert targets.windows[0]["title"] == "Core demo"
    assert targets.monitors == [{"target": "monitor:1", "rect": Rect(0, 0, 1920, 1080).as_dict()}]


def test_pick_region_fronts_window_and_returns_target_and_region(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    target, region = rig.service.pick_region("Core demo")
    assert rig.backend.fronted == [7] and region == Rect(5, 5, 50, 40)
    assert target["window_id"] == 7 and rig.picker.bounds == [Rect(0, 0, 800, 600)]


def test_pick_region_on_monitor_skips_bring_to_front(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.service.pick_region("monitor:1")
    assert rig.backend.fronted == [] and rig.picker.bounds == [Rect(0, 0, 1920, 1080)]


def test_pick_region_unknown_target_is_lookup_error(tmp_path: Path) -> None:
    with pytest.raises(LookupError, match="no window matches 'nope'"):
        Rig(tmp_path).service.pick_region("nope")


def test_record_and_stop_registers_history(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    started = rig.service.start(request())
    assert started.state is RecordingState.RECORDING and started.output.parent == tmp_path / "videos"
    info = rig.service.stop()
    assert rig.service.status().state is RecordingState.IDLE
    assert rig.service.history() == [info] and info.target_label == "Core demo (python.exe)"
    assert info.recording_id == started.recording_id and info.duration_seconds == 4.0
    assert rig.kinds() == [KINDS.STARTED, KINDS.STOPPED, KINDS.HISTORY_CHANGED]


def test_request_output_dir_beats_settings(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    status = rig.service.start(request(output_dir=tmp_path / "elsewhere"))
    rig.service.stop()
    assert status.output.parent == tmp_path / "elsewhere"


def test_second_start_is_a_state_conflict(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.service.start(request())
    with pytest.raises(RuntimeError, match="cannot start while 'recording'; expected 'idle'"):
        rig.service.start(request())
    rig.service.stop()


def test_stop_or_pause_when_idle_name_the_state(tmp_path: Path) -> None:
    service = Rig(tmp_path).service
    with pytest.raises(RuntimeError, match="cannot stop while 'idle'"):
        service.stop()
    with pytest.raises(RuntimeError, match="cannot pause while 'idle'; expected 'recording'"):
        service.pause()
    with pytest.raises(RuntimeError, match="cannot resume while 'idle'; expected 'paused'"):
        service.resume()


def test_unknown_target_leaves_service_idle(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with pytest.raises(LookupError):
        rig.service.start(RecordingRequest(target="ghost"))
    assert rig.service.status().state is RecordingState.IDLE


@pytest.mark.parametrize("delay", [-1, 61])
def test_bad_delay_is_value_error_with_range(tmp_path: Path, delay: int) -> None:
    with pytest.raises(ValueError, match=f"delay_seconds={delay} is invalid; expected an integer 0..60"):
        Rig(tmp_path).service.start(request(delay_seconds=delay))


def test_pause_and_resume_change_state_and_emit(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.service.start(request())
    assert rig.service.pause().state is RecordingState.PAUSED
    assert rig.service.resume().state is RecordingState.RECORDING
    rig.service.stop()
    assert rig.kinds()[:3] == [KINDS.STARTED, KINDS.PAUSED, KINDS.RESUMED]


def test_stop_while_paused_still_saves(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.service.start(request())
    rig.service.pause()
    assert rig.service.stop().path.exists()


def test_paused_elapsed_time_is_frozen(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.service.start(request())
    rig.service.pause()
    frozen = rig.service.status().elapsed_seconds
    time.sleep(0.05)
    assert rig.service.status().elapsed_seconds == frozen
    rig.service.stop()


def test_countdown_emits_each_second_then_starts(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    status = rig.service.start(request(delay_seconds=3))
    assert status.state in (RecordingState.COUNTDOWN, RecordingState.RECORDING)
    wait_until(lambda: rig.service.status().state is RecordingState.RECORDING)
    countdowns = [e.status.countdown_remaining for e in rig.log.events if e.kind is KINDS.COUNTDOWN]
    assert rig.kinds() == [KINDS.COUNTDOWN] * 3 + [KINDS.STARTED]
    assert countdowns == [3, 2, 1] and rig.service.status().countdown_remaining == 0
    rig.service.stop()


def test_cancel_during_countdown_never_starts_capture(tmp_path: Path) -> None:
    gate = GatedWait()
    rig = Rig(tmp_path, wait=gate)
    rig.service.start(request(delay_seconds=5))
    wait_until(lambda: rig.service.status().countdown_remaining == 5)
    rig.service.cancel()
    assert rig.service.status().state is RecordingState.IDLE
    gate.release.set()
    time.sleep(0.05)
    assert rig.builder.calls == 0 and rig.kinds()[-1] is KINDS.CANCELLED


def test_stop_during_countdown_points_to_cancel(tmp_path: Path) -> None:
    gate = GatedWait()
    rig = Rig(tmp_path, wait=gate)
    rig.service.start(request(delay_seconds=5))
    with pytest.raises(RuntimeError, match="cannot stop while 'countdown'"):
        rig.service.stop()
    rig.service.cancel()


def test_countdown_capture_failure_is_reported_as_event(tmp_path: Path) -> None:
    rig = Rig(tmp_path, fail_start=True)
    rig.service.start(request(delay_seconds=1))
    wait_until(lambda: KINDS.FAILED in rig.kinds())
    failed = [e for e in rig.log.events if e.kind is KINDS.FAILED][0]
    assert "0x8007000E" in (failed.message or "") and rig.service.status().state is RecordingState.IDLE


def test_immediate_start_failure_raises_and_resets(tmp_path: Path) -> None:
    rig = Rig(tmp_path, fail_start=True)
    with pytest.raises(OSError, match="0x8007000E"):
        rig.service.start(request())
    assert rig.service.status().state is RecordingState.IDLE


def test_cancel_recording_deletes_partial_files(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    status = rig.service.start(request())
    rig.service.cancel()
    assert not status.output.exists() and rig.service.history() == []
    assert rig.service.status().state is RecordingState.IDLE and rig.kinds()[-1] is KINDS.CANCELLED


def test_cancel_when_idle_is_a_noop(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.service.cancel()
    assert rig.kinds() == []


def test_target_closed_event_and_status(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.service.start(request())
    rig.builder.sessions[0]._parts.frame_source.closed_sink()
    assert rig.service.status().target_closed is True and KINDS.TARGET_CLOSED in rig.kinds()
    rig.service.stop()


def test_listener_exception_does_not_break_recording(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.service.subscribe(ExplodingListener())
    rig.service.start(request())
    assert rig.service.stop().path.exists()
    assert KINDS.STOPPED in rig.kinds()


def test_unsubscribe_stops_events(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    off = rig.service.subscribe(ListenerLog())
    extra = ListenerLog()
    off2 = rig.service.subscribe(extra)
    off2()
    rig.service.update_settings({"theme": "dark"})
    rig.service.flush_events()
    off()
    assert extra.events == [] and rig.log.kinds() == [KINDS.SETTINGS_CHANGED]


def test_trim_adds_derived_entry_and_event(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.service.start(request())
    original = rig.service.stop()
    trimmed = rig.service.trim(original.recording_id, 1.0, 3.0)
    assert trimmed.derived_from == original.recording_id and trimmed.duration_seconds == 2.0
    assert [e.recording_id for e in rig.service.history()] == [trimmed.recording_id, original.recording_id]
    assert rig.kinds()[-1] is KINDS.HISTORY_CHANGED


def test_trim_rejects_range_outside_recording(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.service.start(request())
    info = rig.service.stop()
    with pytest.raises(ValueError, match="start=2.0 end=9.0"):
        rig.service.trim(info.recording_id, 2.0, 9.0)


def test_export_gif_quality_flag_and_path(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.service.start(request())
    info = rig.service.stop()
    gif = rig.service.export_gif(info.recording_id, high_quality=False)
    assert gif == info.path.with_suffix(".gif") and rig.media.gifs[0][2] is False


def test_delete_recording_removes_file_and_unknown_id_raises(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.service.start(request())
    info = rig.service.stop()
    rig.service.delete_recording(info.recording_id)
    assert not info.path.exists() and rig.service.history() == []
    with pytest.raises(LookupError, match="no recording"):
        rig.service.delete_recording(info.recording_id)


def test_history_survives_a_new_service_instance(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.service.start(request())
    info = rig.service.stop()
    assert Rig(tmp_path).service.history() == [info]


def test_settings_default_update_and_event(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    assert rig.service.get_settings().output_dir == tmp_path / "videos"
    updated = rig.service.update_settings({"default_fps": 60})
    assert updated.default_fps == 60 and rig.service.get_settings().default_fps == 60
    assert rig.kinds() == [KINDS.SETTINGS_CHANGED]


def test_settings_bad_update_raises_and_emits_nothing(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with pytest.raises(ValueError, match="unknown setting 'colour'"):
        rig.service.update_settings({"colour": "red"})
    assert rig.kinds() == []


def test_changed_output_dir_is_used_for_next_recording(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.service.update_settings({"output_dir": str(tmp_path / "new")})
    status = rig.service.start(request())
    rig.service.stop()
    assert status.output.parent == tmp_path / "new"
