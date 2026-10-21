"""Platform-neutral contracts every OS backend implements."""

from __future__ import annotations

from enum import Enum
from typing import Callable, Protocol

from screen_recorder_mcp.geometry import FrameArray

FrameCallback = Callable[[FrameArray], None]
ClosedCallback = Callable[[], None]


class FrameSource(Protocol):
    """Pushes BGRA frames of one window/monitor; only on change is fine (the pacer fills gaps)."""

    def start(self, frame_sink: FrameCallback, closed_sink: ClosedCallback) -> None: ...
    def stop(self) -> None: ...


class AudioMode(str, Enum):
    NONE = "none"
    SYSTEM = "system"
    MIC = "mic"


class AudioRecorder(Protocol):
    """Writes a WAV file between start() and stop(); stop() raises if capture failed."""

    def start(self) -> None: ...
    def stop(self) -> None: ...
