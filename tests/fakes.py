"""Named fakes for the recorder's I/O boundaries."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from screen_recorder_mcp.capture import ClosedCallback, FrameCallback
from screen_recorder_mcp.geometry import FrameArray, Rect
from screen_recorder_mcp.window_registry import WindowInfo


def solid_frame(width: int, height: int, value: int = 7) -> FrameArray:
    return np.full((height, width, 4), value, dtype=np.uint8)


class FakeWindowLister:
    def __init__(self, windows: list[WindowInfo]) -> None:
        self._windows = windows

    def list_windows(self) -> list[WindowInfo]:
        return list(self._windows)


class FakeFrameSource:
    """Delivers one frame synchronously on start (or none, to simulate minimized)."""

    def __init__(self, first_frame: FrameArray | None) -> None:
        self._first_frame = first_frame
        self.stopped = False
        self.closed_sink: ClosedCallback | None = None

    def start(self, frame_sink: FrameCallback, closed_sink: ClosedCallback) -> None:
        self.closed_sink = closed_sink
        if self._first_frame is not None:
            frame_sink(self._first_frame)

    def stop(self) -> None:
        self.stopped = True


class FakeEncoder:
    def __init__(self) -> None:
        self.size: tuple[int, int, int] | None = None
        self.frames: list[FrameArray] = []
        self.closed = False

    def open(self, width: int, height: int, fps: int) -> None:
        self.size = (width, height, fps)

    def write(self, frame: FrameArray) -> None:
        self.frames.append(frame)

    def close(self) -> None:
        self.closed = True


class FakeAudioRecorder:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class FakeMuxer:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path, Path]] = []

    def __call__(self, video: Path, audio: Path, output: Path) -> None:
        self.calls.append((video, audio, output))


def window(window_id: int, title: str, process: str) -> WindowInfo:
    return WindowInfo(window_id=window_id, title=title, process=process, rect=Rect(0, 0, 800, 600))


class FailingStopFrameSource(FakeFrameSource):
    """OS capture API that errors on stop, as SCK does after the window closed."""

    def stop(self) -> None:
        self.stopped = True
        raise TimeoutError("capture stop gave no answer")


class FailingAudioRecorder(FakeAudioRecorder):
    def start(self) -> None:
        raise PermissionError("screen recording not granted")
