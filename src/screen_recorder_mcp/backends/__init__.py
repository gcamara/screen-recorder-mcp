"""OS backends. Each one owns window listing, frame capture and audio for its OS.

Backends are imported lazily so a macOS-only dependency (pyobjc) is never
imported on Windows and vice versa.
"""

from __future__ import annotations

import sys
from functools import cache
from pathlib import Path
from typing import Protocol

from screen_recorder_mcp.capture import AudioMode, AudioRecorder, FrameSource
from screen_recorder_mcp.frame_sources import CaptureMode
from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.targets import CaptureTarget
from screen_recorder_mcp.window_registry import WindowLister


class PlatformBackend(Protocol):
    name: str
    windows: WindowLister

    def prepare_process(self) -> None:
        """Process-wide setup before any coordinate query (e.g. DPI awareness)."""

    def monitor_rects(self) -> list[Rect]: ...
    def bring_to_front(self, target: CaptureTarget) -> None: ...
    def frame_source(
        self, target: CaptureTarget, capture_cursor: bool, mode: CaptureMode
    ) -> FrameSource: ...
    def audio_recorder(
        self, mode: AudioMode, target: CaptureTarget, output: Path
    ) -> AudioRecorder: ...


@cache
def current_backend() -> PlatformBackend:
    """The backend for the running OS.

    Example: current_backend().name -> "windows" | "macos"
    """
    return backend_for(sys.platform)


def backend_for(platform: str) -> PlatformBackend:
    if platform == "win32":
        from screen_recorder_mcp.backends.windows.backend import WindowsBackend

        return WindowsBackend()
    if platform == "darwin":
        from screen_recorder_mcp.backends.macos.backend import MacBackend

        return MacBackend()
    raise NotImplementedError(f"platform {platform!r} is not supported; expected win32 or darwin")
