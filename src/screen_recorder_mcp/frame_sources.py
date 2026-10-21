"""OS-neutral FrameSource building blocks: timed polling and GPU→CPU fallback."""

from __future__ import annotations

import json
import logging
import threading
from enum import Enum
from typing import Callable

from screen_recorder_mcp.capture import ClosedCallback, FrameCallback, FrameSource
from screen_recorder_mcp.geometry import FrameArray

_log = logging.getLogger(__name__)

FrameGrabber = Callable[[], FrameArray | None]
AliveCheck = Callable[[], bool]


class CaptureMode(str, Enum):
    AUTO = "auto"  # GPU capture, CPU if the GPU can't start a session
    GPU = "gpu"
    CPU = "cpu"


class PollingFrameSource:
    """Calls a grabber every `interval` seconds on its own thread.

    For CPU capture APIs that return a frame on demand instead of pushing them.
    Example: PollingFrameSource(lambda: grab_window(hwnd), lambda: is_window(hwnd), 1 / 30)
    """

    def __init__(self, grab: FrameGrabber, is_alive: AliveCheck, interval: float) -> None:
        self._grab = grab
        self._is_alive = is_alive
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, frame_sink: FrameCallback, closed_sink: ClosedCallback) -> None:
        self._thread = threading.Thread(
            target=self._poll, args=(frame_sink, closed_sink), daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _poll(self, frame_sink: FrameCallback, closed_sink: ClosedCallback) -> None:
        while not self._stop.is_set():
            if not self._is_alive():
                closed_sink()
                return
            frame = self._grab()
            if frame is not None:
                frame_sink(frame)
            self._stop.wait(self._interval)


class FallbackFrameSource:
    """Starts `primary`; if it raises on start, uses `fallback` instead.

    Lets a recording go on through CPU capture when the GPU is out of memory
    or busy with something else.
    Example: FallbackFrameSource(WgcFrameSource(...), PollingFrameSource(...))
    """

    def __init__(self, primary: FrameSource, fallback: FrameSource) -> None:
        self._primary = primary
        self._fallback = fallback
        self.active: FrameSource | None = None

    def start(self, frame_sink: FrameCallback, closed_sink: ClosedCallback) -> None:
        try:
            self._primary.start(frame_sink, closed_sink)
            self.active = self._primary
        except Exception as error:
            _log.warning(json.dumps({"event": "gpu_capture_failed", "fallback": "cpu", "error": str(error)}))
            self._fallback.start(frame_sink, closed_sink)
            self.active = self._fallback

    def stop(self) -> None:
        if self.active is not None:
            self.active.stop()
