"""Saving a trim/crop: new name (default) or overwrite, the dialog, and the MCP tool arguments."""

from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from screen_recorder_mcp.history_store import JsonHistoryStore
from screen_recorder_mcp.recording_library import RecordingLibrary, edit_output_path, suggested_edit_name
from screen_recorder_mcp.service import RecordingRequest
from screen_recorder_mcp.tools import RecorderTools
from screen_recorder_mcp.ui.save_edit_dialog import SaveChoice, SaveEditDialog
from tests.fakes import FakeMediaTools, FakeRecorderService

NOW = datetime(2026, 11, 1, 12, 0)


def make_library(tmp_path: Path) -> tuple[RecordingLibrary, FakeMediaTools]:
    media = FakeMediaTools(duration=4.0)
    ids = iter(["id1", "id2"])
    library = RecordingLibrary(JsonHistoryStore(tmp_path), media, lambda: NOW, lambda: next(ids))  # type: ignore[arg-type]
    (tmp_path / "rec.mp4").write_bytes(b"original")
    library.register("a", tmp_path / "rec.mp4", "Notepad", "user")
    return library, media


def test_suggested_name_skips_taken_files(tmp_path: Path) -> None:
    source = tmp_path / "rec.mp4"
    assert suggested_edit_name(source, cropped=False) == "rec-trim.mp4"
    (tmp_path / "rec-crop.mp4").write_bytes(b"x")
    assert suggested_edit_name(source, cropped=True) == "rec-crop-2.mp4"


def test_edit_output_path_validates_names(tmp_path: Path) -> None:
    source = tmp_path / "rec.mp4"
    source.write_bytes(b"x")
    assert edit_output_path(source, " short ") == tmp_path / "short.mp4"
    assert edit_output_path(source, "clip.MP4") == tmp_path / "clip.MP4"
    for bad in ("", "a/b", "..\\up", "c:x"):
        with pytest.raises(ValueError, match="invalid"):
            edit_output_path(source, bad)
    with pytest.raises(ValueError, match="Overwrite original"):
        edit_output_path(source, "rec")
    (tmp_path / "taken.mp4").write_bytes(b"x")
    with pytest.raises(FileExistsError, match="taken.mp4 already exists"):
        edit_output_path(source, "taken")


def test_library_saves_under_the_chosen_name(tmp_path: Path) -> None:
    library, _ = make_library(tmp_path)
    saved = library.trim("a", 1.0, 3.0, output_name="highlight")
    assert saved.path == tmp_path / "highlight.mp4" and saved.recording_id == "id1"
    assert (tmp_path / "rec.mp4").read_bytes() == b"original"


def test_library_overwrite_replaces_the_file_and_keeps_the_id(tmp_path: Path) -> None:
    library, media = make_library(tmp_path)
    saved = library.trim("a", 1.0, 3.0, overwrite=True)
    assert saved.recording_id == "a" and saved.path == tmp_path / "rec.mp4"
    assert (tmp_path / "rec.mp4").read_bytes() == b"trim"  # what the fake ffmpeg wrote
    assert media.trims[0][3].name == "rec-edit-tmp.mp4" and not media.trims[0][3].exists()
    assert [e.recording_id for e in library.entries(5)] == ["a"] and saved.duration_seconds == 2.0


def test_mcp_tool_forwards_name_and_overwrite() -> None:
    service = FakeRecorderService()
    service.start(RecordingRequest(target="Notepad"))
    info = service.stop()
    tools = RecorderTools(lambda: service)
    tools.trim_recording(info.recording_id, 0.0, 1.0, output_name="clip")
    tools.trim_recording(info.recording_id, 0.0, 1.0, overwrite=True)
    assert service.trim_outputs == [("clip", False), (None, True)]


def test_dialog_enter_saves_the_suggested_name(qapp, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "rec.mp4"
    source.write_bytes(b"x")
    dialog = SaveEditDialog(source, "rec-trim.mp4", "Keep 0:01 to 0:03")
    dialog.show()
    QTest.keyClick(dialog._name, Qt.Key.Key_Return)
    assert dialog.choice == SaveChoice("rec-trim.mp4", overwrite=False)


def test_dialog_typing_replaces_the_selected_stem(qapp, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "rec.mp4"
    source.write_bytes(b"x")
    dialog = SaveEditDialog(source, "rec-trim.mp4", "")
    dialog.show()
    QTest.keyClicks(dialog._name, "intro")
    QTest.keyClick(dialog._name, Qt.Key.Key_Return)
    assert dialog.choice == SaveChoice("intro.mp4", overwrite=False)


def test_dialog_keeps_open_on_a_taken_name_and_allows_overwrite(qapp, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "rec.mp4"
    source.write_bytes(b"x")
    (tmp_path / "rec-trim.mp4").write_bytes(b"x")
    dialog = SaveEditDialog(source, "rec-trim.mp4", "")
    dialog.show()
    dialog._accept_choice()
    assert dialog.choice is None and "already exists" in dialog._error.text()
    dialog._overwrite.setChecked(True)
    dialog._accept_choice()
    assert dialog.choice == SaveChoice(None, overwrite=True) and not dialog._name.isEnabled()
