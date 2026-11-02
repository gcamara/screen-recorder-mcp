"""Finished recordings: history rows, delete, trim and GIF export."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from screen_recorder_mcp.geometry import Rect, clamp_crop, even_floor
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


def suggested_edit_name(source: Path, cropped: bool) -> str:
    """Default file name offered when saving an edit: a free '<stem>-trim|crop[N].mp4' next to source.

    Example: suggested_edit_name(Path("v/rec.mp4"), cropped=True) -> "rec-crop.mp4"
    """
    return unique_sibling(source, "crop" if cropped else "trim", ".mp4").name


def edit_output_path(source: Path, name: str) -> Path:
    """Validate a user-typed file name for an edit and place it next to the source.

    Example: edit_output_path(Path("v/rec.mp4"), "short") -> v/short.mp4
    """
    cleaned = name.strip()
    if not cleaned or any(sep in cleaned for sep in ("/", "\\", ":")):
        raise ValueError(f"file name {name!r} is invalid; expected a plain name like 'clip.mp4'")
    if not cleaned.lower().endswith(".mp4"):
        cleaned += ".mp4"
    target = source.with_name(cleaned)
    if target == source:
        raise ValueError(f"{cleaned} is the original recording; choose 'Overwrite original' to replace it")
    if target.exists():
        raise FileExistsError(f"{cleaned} already exists; choose another name")
    return target


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

    def trim(
        self, recording_id: str, start: float, end: float, crop: Rect | None = None,
        output_name: str | None = None, overwrite: bool = False,
    ) -> RecordingInfo:  # fmt: skip
        """Cut [start, end] seconds, and optionally a crop rectangle, into a new file and history row.

        `crop` is in video pixels; it is clipped to the frame and rounded to even.
        `output_name` names the new file (next to the original; default '<stem>-trim.mp4').
        `overwrite` replaces the original file instead and keeps its recording_id.
        """
        source = self._history.get(recording_id)
        validate_trim_range(start, end, source.duration_seconds)
        picture = None if crop is None else clamp_crop(crop, source.width, source.height)
        if picture == Rect(0, 0, even_floor(source.width), even_floor(source.height)):
            picture = None  # a full-frame crop is no crop; keep the cheaper stream copy
        if overwrite:
            return self._overwrite(source, start, end, picture)
        if output_name is None:
            output = unique_sibling(source.path, "crop" if picture else "trim", ".mp4")
        else:
            output = edit_output_path(source.path, output_name)
        self._media().trim(source.path, start, end, output, picture)
        return self.register(
            self._new_id(), output, source.target_label, source.started_by, derived_from=recording_id
        )

    def _overwrite(self, source: RecordingInfo, start: float, end: float, crop: Rect | None) -> RecordingInfo:
        """Render next to the original, then swap it in: a failed ffmpeg run never loses the original.

        Whoever plays the file must release it first (Windows refuses to replace an open file).
        """
        scratch = unique_sibling(source.path, "edit-tmp", ".mp4")
        try:
            self._media().trim(source.path, start, end, scratch, crop)
            scratch.replace(source.path)
        finally:
            scratch.unlink(missing_ok=True)
        self._history.remove(source.recording_id)
        return self.register(
            source.recording_id, source.path, source.target_label, source.started_by, derived_from=source.derived_from
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
