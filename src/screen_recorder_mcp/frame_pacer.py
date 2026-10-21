"""Constant-fps pacing over an event-driven frame source.

WGC only delivers a frame when the window content changes. Piping those
straight into a fixed-framerate encoder would shrink a static 10 s recording
to a fraction of a second, so the pacer re-sends the latest frame on a clock.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from screen_recorder_mcp.geometry import FrameArray, Rect, crop_frame, fit_frame

Clock = Callable[[], float]
Sleeper = Callable[[float], None]


class LatestFrameSlot:
    """Thread-safe single-slot mailbox; the capture thread overwrites it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame: FrameArray | None = None
        self.first_frame = threading.Event()

    def put(self, frame: FrameArray) -> None:
        with self._lock:
            self._frame = frame
        self.first_frame.set()

    def get(self) -> FrameArray | None:
        with self._lock:
            return self._frame


def frames_due(elapsed_seconds: float, fps: int, frames_written: int) -> int:
    """How many frames must be emitted now to keep pace with wall-clock time.

    Example: frames_due(1.0, 30, 25) -> 6  (frames 25..30 are due at t <= 1.0 s)
    """
    target = int(elapsed_seconds * fps) + 1
    return max(0, target - frames_written)


class FramePacer:
    """Emits the latest captured frame, cropped and size-locked, at `fps`."""

    def __init__(
        self,
        slot: LatestFrameSlot,
        emit: Callable[[FrameArray], None],
        fps: int,
        crop: Rect | None,
        output_size: tuple[int, int],
        clock: Clock = time.perf_counter,
        sleeper: Sleeper = time.sleep,
    ) -> None:
        self._slot = slot
        self._emit = emit
        self._fps = fps
        self._crop = crop
        self._width, self._height = output_size
        self._clock = clock
        self._sleeper = sleeper
        self.frames_written = 0

    def run(self, stop: threading.Event, origin: float) -> None:
        while not stop.is_set():
            self.tick(self._clock() - origin)
            self._sleeper(1.0 / (self._fps * 2))
        self.tick(self._clock() - origin)

    def tick(self, elapsed_seconds: float) -> None:
        frame = self._slot.get()
        if frame is None:
            return
        shaped = fit_frame(crop_frame(frame, self._crop), self._width, self._height)
        for _ in range(frames_due(elapsed_seconds, self._fps, self.frames_written)):
            self._emit(shaped)
            self.frames_written += 1
