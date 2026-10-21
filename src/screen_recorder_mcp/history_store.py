"""Newest-first JSON history of finished recordings."""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from screen_recorder_mcp.json_file import read_json_object, write_json_atomic
from screen_recorder_mcp.service import RecordingInfo, StartedBy, to_jsonable

HISTORY_FILE_NAME = "history.json"


def info_from_json(raw: dict[str, object]) -> RecordingInfo:
    """Inverse of to_jsonable(RecordingInfo); raises KeyError/ValueError on a malformed row."""
    started_by: StartedBy = "ai" if raw["started_by"] == "ai" else "user"
    derived = raw.get("derived_from")
    return RecordingInfo(
        recording_id=str(raw["recording_id"]),
        path=Path(str(raw["path"])),
        created_at=datetime.fromisoformat(str(raw["created_at"])),
        duration_seconds=float(raw["duration_seconds"]),  # type: ignore[arg-type]
        width=int(raw["width"]),  # type: ignore[call-overload]
        height=int(raw["height"]),  # type: ignore[call-overload]
        has_audio=bool(raw["has_audio"]),
        target_label=str(raw["target_label"]),
        started_by=started_by,
        derived_from=None if derived is None else str(derived),
    )


class JsonHistoryStore:
    """Persists RecordingInfo rows; rows whose file vanished are dropped on load.

    Example: store = JsonHistoryStore(app_data_dir()); store.add(info); store.entries()[0]
    """

    def __init__(self, directory: Path) -> None:
        self._path = directory / HISTORY_FILE_NAME
        self._lock = threading.Lock()
        self._entries = self._load()

    def entries(self) -> list[RecordingInfo]:
        """Newest first; re-checks the disk so a deleted file disappears immediately."""
        with self._lock:
            self._prune_missing()
            return list(self._entries)

    def get(self, recording_id: str) -> RecordingInfo:
        for entry in self.entries():
            if entry.recording_id == recording_id:
                return entry
        raise LookupError(f"no recording {recording_id!r} in history; expected an id from history()")

    def add(self, info: RecordingInfo) -> None:
        with self._lock:
            self._entries.insert(0, info)
            self._save()

    def remove(self, recording_id: str) -> None:
        with self._lock:
            self._entries = [e for e in self._entries if e.recording_id != recording_id]
            self._save()

    def _load(self) -> list[RecordingInfo]:
        rows = read_json_object(self._path).get("recordings", [])
        parsed = [info for info in map(_parse_row, rows if isinstance(rows, list) else []) if info]
        return [info for info in parsed if info.path.exists()]

    def _prune_missing(self) -> None:
        alive = [entry for entry in self._entries if entry.path.exists()]
        if len(alive) == len(self._entries):
            return
        self._entries = alive
        self._save()

    def _save(self) -> None:
        write_json_atomic(self._path, {"recordings": [to_jsonable(e) for e in self._entries]})


def _parse_row(raw: object) -> RecordingInfo | None:
    if not isinstance(raw, dict):
        return None
    try:
        return info_from_json(raw)
    except (KeyError, ValueError, TypeError):
        return None  # one bad row must not hide the rest of the history
