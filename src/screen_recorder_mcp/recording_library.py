"""Finished recordings: history rows, delete, trim and GIF export."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from screen_recorder_mcp.history_store import JsonHistoryStore
from screen_recorder_mcp.media_tools import MediaTools, find_ffprobe
from screen_recorder_mcp.service import RecordingInfo, StartedBy
from screen_recorder_mcp.session_factory import find_ffmpeg

# Container durations differ from the probe by a frame or two; don't reject a trim end for that.
_END_SLACK_SECONDS = 0.1


def new_recording_id() -> str:
    """Short opaque id, e.g. '3f9c1a7b02de'."""
    return uuid.uuid4().hex[:12]


def validate_trim_range(start: float, end: float, duration: float) -> None:
    """Raise ValueError unless 0 <= start < end <= duration.

    Example: validate_trim_range(1.0, 3.0, 4.0) passes; (3.0, 1.0, 4.0) raises.
    """
    if start < 0 or end <= start or end > duration + _END_SLACK_SECONDS:
        raise ValueError(
            f"trim range start={start} end={end} is invalid for a {duration:.2f}s recording; "
            "expected 0 <= start < end <= duration"
        )


def unique_sibling(source: Path, tag: str, suffix: str) -> Path:
    """First free '<stem>[-tag][N]<suffix>' next to `source`.

    Example: unique_sibling(Path("a/rec.mp4"), "trim", ".mp4") -> a/rec-trim.mp4
             unique_sibling(Path("a/rec.mp4"), "", ".gif")     -> a/rec.gif
    """
    stem = f"{source.stem}-{tag}" if tag else source.stem
    candidate = source.with_name(f"{stem}{suffix}")
    counter = 2
    while candidate.exists():
        candidate = source.with_name(f"{stem}-{counter}{suffix}")
        counter += 1
    return candidate


class RecordingLibrary:
    """History plus the file operations on it.

    Example: library.register("ab12", Path("rec.mp4"), "Notepad", "user")
    """

    def __init__(
        self,
        history: JsonHistoryStore,
        media: MediaTools | None = None,
        clock: Callable[[], datetime] = datetime.now,
        id_factory: Callable[[], str] = new_recording_id,
    ) -> None:
        self._history = history
        self._media_tools = media
        self._clock = clock
        self._new_id = id_factory

    def register(
        self, recording_id: str, path: Path, target_label: str, started_by: StartedBy,
        derived_from: str | None = None,
    ) -> RecordingInfo:  # fmt: skip
        """ffprobe the file and put it at the top of the history."""
        probe = self._media().probe(path)
        info = RecordingInfo(
            recording_id=recording_id, path=path, created_at=self._clock(),
            duration_seconds=probe.duration_seconds, width=probe.width, height=probe.height,
            has_audio=probe.has_audio, target_label=target_label, started_by=started_by,
            derived_from=derived_from,
        )  # fmt: skip
        self._history.add(info)
        return info

    def entries(self, limit: int) -> list[RecordingInfo]:
        if limit < 1:
            raise ValueError(f"limit={limit} is invalid; expected an integer >= 1")
        return self._history.entries()[:limit]

    def delete(self, recording_id: str) -> None:
        info = self._history.get(recording_id)
        info.path.unlink(missing_ok=True)
        self._history.remove(recording_id)

    def trim(self, recording_id: str, start: float, end: float) -> RecordingInfo:
        """Cut [start, end] seconds into a NEW file and history row; the original stays."""
        source = self._history.get(recording_id)
        validate_trim_range(start, end, source.duration_seconds)
        output = unique_sibling(source.path, "trim", ".mp4")
        self._media().trim(source.path, start, end, output)
        return self.register(
            self._new_id(), output, source.target_label, source.started_by, derived_from=recording_id
        )

    def export_gif(self, recording_id: str, high_quality: bool, output: Path | None) -> Path:
        source = self._history.get(recording_id)
        target = output if output is not None else unique_sibling(source.path, "", ".gif")
        target.parent.mkdir(parents=True, exist_ok=True)
        self._media().export_gif(source.path, target, high_quality)
        return target

    def _media(self) -> MediaTools:
        if self._media_tools is None:
            self._media_tools = MediaTools(find_ffmpeg(), find_ffprobe())
        return self._media_tools
