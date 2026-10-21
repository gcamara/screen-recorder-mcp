import json
import threading
from datetime import datetime
from pathlib import Path

import pytest

from screen_recorder_mcp.countdown import run_countdown
from screen_recorder_mcp.event_bus import EventBus
from screen_recorder_mcp.frame_sources import CaptureMode
from screen_recorder_mcp.history_store import JsonHistoryStore, info_from_json
from screen_recorder_mcp.json_file import read_json_object, write_json_atomic
from screen_recorder_mcp.recording_library import (
    RecordingLibrary, unique_sibling, validate_trim_range,
)
from screen_recorder_mcp.service import (
    RecorderEvent, RecorderEventKind, RecordingInfo, RecordingState, RecordingStatus, to_jsonable,
)
from screen_recorder_mcp.settings_store import JsonSettingsStore, validate_changes
from tests.fakes import ExplodingListener, FakeMediaTools, ListenerLog, instant_wait

NOW = datetime(2026, 10, 21, 15, 30, 0)


def make_info(path: Path, recording_id: str = "r1") -> RecordingInfo:
    return RecordingInfo(recording_id, path, NOW, 4.0, 100, 80, False, "Notepad", "user")


def make_event(kind: RecorderEventKind = RecorderEventKind.STARTED) -> RecorderEvent:
    return RecorderEvent(kind, RecordingStatus(state=RecordingState.IDLE))


def test_json_roundtrip_and_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "x.json"
    write_json_atomic(path, {"a": 1})
    assert read_json_object(path) == {"a": 1}
    path.write_text("{not json", encoding="utf-8")
    assert read_json_object(path) == {}
    assert read_json_object(tmp_path / "missing.json") == {}


def test_info_roundtrips_through_json(tmp_path: Path) -> None:
    info = make_info(tmp_path / "a.mp4")
    assert info_from_json(to_jsonable(info)) == info  # type: ignore[arg-type]


def test_history_is_newest_first_and_persisted(tmp_path: Path) -> None:
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "b.mp4").write_bytes(b"x")
    store = JsonHistoryStore(tmp_path)
    store.add(make_info(tmp_path / "a.mp4", "a"))
    store.add(make_info(tmp_path / "b.mp4", "b"))
    assert [e.recording_id for e in JsonHistoryStore(tmp_path).entries()] == ["b", "a"]


def test_history_drops_entries_whose_file_was_deleted(tmp_path: Path) -> None:
    gone, kept = tmp_path / "gone.mp4", tmp_path / "kept.mp4"
    gone.write_bytes(b"x")
    kept.write_bytes(b"x")
    store = JsonHistoryStore(tmp_path)
    store.add(make_info(gone, "gone"))
    store.add(make_info(kept, "kept"))
    gone.unlink()
    assert [e.recording_id for e in store.entries()] == ["kept"]
    assert [e.recording_id for e in JsonHistoryStore(tmp_path).entries()] == ["kept"]


def test_history_skips_malformed_rows_and_unknown_ids(tmp_path: Path) -> None:
    bad_rows = json.dumps({"recordings": [{"oops": 1}, 5]})
    (tmp_path / "history.json").write_text(bad_rows, encoding="utf-8")
    store = JsonHistoryStore(tmp_path)
    assert store.entries() == []
    with pytest.raises(LookupError, match="no recording 'zzz'"):
        store.get("zzz")


def test_history_remove(tmp_path: Path) -> None:
    (tmp_path / "a.mp4").write_bytes(b"x")
    store = JsonHistoryStore(tmp_path)
    store.add(make_info(tmp_path / "a.mp4", "a"))
    store.remove("a")
    assert store.entries() == []


def test_settings_default_to_injected_output_dir(tmp_path: Path) -> None:
    settings = JsonSettingsStore(tmp_path, tmp_path / "videos").get()
    assert settings.output_dir == tmp_path / "videos" and settings.default_fps == 30


def test_settings_update_persists_and_coerces(tmp_path: Path) -> None:
    store = JsonSettingsStore(tmp_path, tmp_path / "v")
    store.update({"capture_mode": "cpu", "default_fps": 60, "output_dir": str(tmp_path / "o")})
    reloaded = JsonSettingsStore(tmp_path, tmp_path / "v").get()
    assert reloaded.capture_mode is CaptureMode.CPU and reloaded.output_dir == tmp_path / "o"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"colour": 1}, "unknown setting 'colour'"),
        ({"default_fps": 500}, "'default_fps'=500 is invalid; expected an integer 1..120"),
        ({"default_fps": True}, "expected an integer 1..120"),
        ({"theme": "neon"}, "'theme'='neon' is invalid; expected one of system, dark, light"),
        ({"hotkey": "Ctrl Shift R"}, "expected keys joined by"),
        ({"capture_cursor": "yes"}, "expected true or false"),
        ({"capture_mode": "tpu"}, "one of auto, gpu, cpu"),
        ({"output_dir": ""}, "non-empty folder path"),
    ],
)
def test_settings_reject_bad_input_naming_key_and_shape(
    changes: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_changes(changes)


def test_settings_update_is_all_or_nothing(tmp_path: Path) -> None:
    store = JsonSettingsStore(tmp_path, tmp_path / "v")
    with pytest.raises(ValueError):
        store.update({"theme": "dark", "default_fps": 0})
    assert store.get().theme == "system"


def test_settings_ignore_stale_keys_in_file(tmp_path: Path) -> None:
    stored = '{"theme": "dark", "removed": 1, "default_fps": 9999}'
    (tmp_path / "settings.json").write_text(stored, encoding="utf-8")
    settings = JsonSettingsStore(tmp_path, tmp_path / "v").get()
    assert settings.theme == "dark" and settings.default_fps == 30


def test_event_bus_isolates_a_failing_listener() -> None:
    bus, log = EventBus(), ListenerLog()
    bus.subscribe(ExplodingListener())
    bus.subscribe(log)
    bus.publish(make_event())
    bus.publish(make_event(RecorderEventKind.STOPPED))
    bus.flush()
    assert log.kinds() == [RecorderEventKind.STARTED, RecorderEventKind.STOPPED]
    bus.close()


def test_event_bus_unsubscribe_stops_delivery() -> None:
    bus, log = EventBus(), ListenerLog()
    off = bus.subscribe(log)
    off()
    off()  # idempotent
    bus.publish(make_event())
    bus.flush()
    assert log.events == []
    bus.close()


def test_event_bus_delivers_off_the_publishing_thread() -> None:
    bus, seen = EventBus(), []
    bus.subscribe(lambda _event: seen.append(threading.current_thread()))
    bus.publish(make_event())
    bus.flush()
    assert seen and seen[0] is not threading.current_thread()
    bus.close()


def test_countdown_ticks_once_per_second_down_to_one() -> None:
    ticks: list[int] = []
    assert run_countdown(3, ticks.append, threading.Event(), instant_wait) is True
    assert ticks == [3, 2, 1]


def test_countdown_stops_when_cancelled_mid_way() -> None:
    cancelled, ticks = threading.Event(), []

    def cancel_after_first(remaining: int) -> None:
        ticks.append(remaining)
        cancelled.set()

    assert run_countdown(5, cancel_after_first, cancelled, instant_wait) is False
    assert ticks == [5]


def test_countdown_of_zero_is_immediately_done() -> None:
    assert run_countdown(0, lambda _r: None, threading.Event(), instant_wait) is True


def test_trim_range_validation_names_values() -> None:
    validate_trim_range(1.0, 3.0, 4.0)
    for start, end in ((-1, 2), (3, 1), (0, 9)):
        with pytest.raises(ValueError, match=f"start={start} end={end}"):
            validate_trim_range(start, end, 4.0)


def test_unique_sibling_avoids_existing_files(tmp_path: Path) -> None:
    source = tmp_path / "rec.mp4"
    assert unique_sibling(source, "trim", ".mp4") == tmp_path / "rec-trim.mp4"
    (tmp_path / "rec-trim.mp4").write_bytes(b"x")
    assert unique_sibling(source, "trim", ".mp4") == tmp_path / "rec-trim-2.mp4"
    assert unique_sibling(source, "", ".gif") == tmp_path / "rec.gif"


def make_library(tmp_path: Path) -> tuple[RecordingLibrary, FakeMediaTools]:
    media = FakeMediaTools(duration=4.0)
    ids = iter(["id1", "id2", "id3"])
    history = JsonHistoryStore(tmp_path)
    return RecordingLibrary(history, media, lambda: NOW, lambda: next(ids)), media  # type: ignore[arg-type]


def test_library_register_probes_and_lists_newest_first(tmp_path: Path) -> None:
    library, _ = make_library(tmp_path)
    for name in ("a", "b"):
        (tmp_path / f"{name}.mp4").write_bytes(b"x")
        library.register(name, tmp_path / f"{name}.mp4", "Notepad", "ai")
    entries = library.entries(10)
    assert [e.recording_id for e in entries] == ["b", "a"] and entries[0].duration_seconds == 4.0
    assert [e.recording_id for e in library.entries(1)] == ["b"]
    with pytest.raises(ValueError, match="limit=0"):
        library.entries(0)


def test_library_trim_creates_derived_entry_and_keeps_original(tmp_path: Path) -> None:
    library, media = make_library(tmp_path)
    (tmp_path / "a.mp4").write_bytes(b"x")
    library.register("a", tmp_path / "a.mp4", "Notepad", "user")
    trimmed = library.trim("a", 1.0, 3.0)
    assert trimmed.derived_from == "a" and trimmed.duration_seconds == 2.0
    assert trimmed.path == tmp_path / "a-trim.mp4" and (tmp_path / "a.mp4").exists()
    assert media.trims[0][1:3] == (1.0, 3.0)
    assert [e.recording_id for e in library.entries(5)] == ["id1", "a"]


def test_library_delete_removes_file_and_row(tmp_path: Path) -> None:
    library, _ = make_library(tmp_path)
    (tmp_path / "a.mp4").write_bytes(b"x")
    library.register("a", tmp_path / "a.mp4", "Notepad", "user")
    library.delete("a")
    assert not (tmp_path / "a.mp4").exists() and library.entries(5) == []
    with pytest.raises(LookupError, match="'a'"):
        library.delete("a")


def test_library_gif_defaults_next_to_source(tmp_path: Path) -> None:
    library, media = make_library(tmp_path)
    (tmp_path / "a.mp4").write_bytes(b"x")
    library.register("a", tmp_path / "a.mp4", "Notepad", "user")
    assert library.export_gif("a", True, None) == tmp_path / "a.gif"
    custom = tmp_path / "out" / "c.gif"
    assert library.export_gif("a", False, custom) == custom and media.gifs[1][2] is False
