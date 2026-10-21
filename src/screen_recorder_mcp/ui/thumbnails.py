"""First-frame thumbnails for the history list, cached on disk (via ffmpeg)."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Callable, Protocol

THUMB_WIDTH = 320

FfmpegRun = Callable[[list[str]], int]


class ThumbnailMaker(Protocol):
    def thumbnail_for(self, video: Path) -> Path | None: ...


def _run_ffmpeg(args: list[str]) -> int:
    return subprocess.run(["ffmpeg", *args], capture_output=True, check=False).returncode


class ThumbnailCache:
    """Example: ThumbnailCache(app_data_dir() / "thumbs").thumbnail_for(info.path) -> Path | None"""

    def __init__(self, cache_dir: Path, run: FfmpegRun = _run_ffmpeg) -> None:
        self._dir = cache_dir
        self._run = run

    def cache_path(self, video: Path) -> Path:
        """Keyed by path + mtime + size so an edited file gets a fresh thumbnail."""
        stat = video.stat()
        key = f"{video.resolve()}|{stat.st_mtime_ns}|{stat.st_size}".encode()
        return self._dir / f"{hashlib.sha1(key).hexdigest()[:20]}.png"  # noqa: S324 - cache key, not security

    def thumbnail_for(self, video: Path) -> Path | None:
        if not video.is_file():
            return None
        target = self.cache_path(video)
        if target.is_file():
            return target
        self._dir.mkdir(parents=True, exist_ok=True)
        code = self._run(["-y", "-loglevel", "error", "-ss", "0.2", "-i", str(video), "-frames:v", "1",
                          "-vf", f"scale={THUMB_WIDTH}:-2", str(target)])
        return target if code == 0 and target.is_file() else None
