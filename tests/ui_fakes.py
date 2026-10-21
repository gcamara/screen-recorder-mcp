"""Named fakes for the desktop UI tests (kept out of tests/fakes.py to avoid merge clashes)."""

from __future__ import annotations

from pathlib import Path

from screen_recorder_mcp.service import RecordingInfo
from screen_recorder_mcp.ui.demo_service import DemoRecorderService
from screen_recorder_mcp.ui.thumbnails import ThumbnailMaker


class UiFakeRecorderService(DemoRecorderService):
    """In-memory RecorderService; recordings point at a stub file, no ffmpeg involved.

    Example: service = UiFakeRecorderService(tmp_path); service.tick(1.0)
    """

    def __init__(self, work_dir: Path, seed_history: bool = True) -> None:
        stub = work_dir / "stub.mp4"
        work_dir.mkdir(parents=True, exist_ok=True)
        stub.write_bytes(b"not a real video")
        super().__init__(work_dir, seed_history=seed_history, sample=stub)
        self.calls: list[str] = []

    def stop(self) -> RecordingInfo:
        self.calls.append("stop")
        return super().stop()


class FakeThumbnails(ThumbnailMaker):
    """Never shells out to ffmpeg; returns no thumbnail."""

    def thumbnail_for(self, video: Path) -> Path | None:
        return None


class FakeDialogs:
    """Scripted answers for the modal prompts."""

    def __init__(self, save_to: Path | None = None, folder: Path | None = None, confirm: bool = True) -> None:
        self._save_to, self._folder, self._confirm = save_to, folder, confirm
        self.asked: list[str] = []

    def save_path(self, suggested_name: str, start_dir: Path) -> Path | None:
        self.asked.append(f"save:{suggested_name}")
        return self._save_to

    def choose_folder(self, start_dir: Path) -> Path | None:
        self.asked.append("folder")
        return self._folder

    def confirm(self, title: str, text: str) -> bool:
        self.asked.append(f"confirm:{title}")
        return self._confirm
