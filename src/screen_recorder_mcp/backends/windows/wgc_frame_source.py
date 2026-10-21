"""Window frame capture through Windows Graphics Capture (windows-capture lib).

WGC captures the window's own surface, so another app moving in front of it
never shows up in the recording.
"""

from __future__ import annotations

import threading

from windows_capture import CaptureControl, Frame, InternalCaptureControl, WindowsCapture

from screen_recorder_mcp.capture import ClosedCallback, FrameCallback


class WgcFrameSource:
    """Streams BGRA frames of one window (or monitor) until stopped or closed."""

    def __init__(self, hwnd: int | None, monitor_index: int | None, capture_cursor: bool) -> None:
        self._hwnd = hwnd
        self._monitor_index = monitor_index
        self._capture_cursor = capture_cursor
        self._control: CaptureControl | None = None
        self._stop_requested = threading.Event()

    def start(self, frame_sink: FrameCallback, closed_sink: ClosedCallback) -> None:
        capture = WindowsCapture(
            cursor_capture=self._capture_cursor,
            draw_border=False,
            window_hwnd=self._hwnd,
            monitor_index=self._monitor_index,
        )
        stop_requested = self._stop_requested

        # windows-capture dispatches on the handler's __name__, hence these names.
        @capture.event
        def on_frame_arrived(frame: Frame, control: InternalCaptureControl) -> None:
            if stop_requested.is_set():
                control.stop()
                return
            # The buffer is native memory, only valid inside this callback.
            frame_sink(frame.frame_buffer.copy())

        @capture.event
        def on_closed() -> None:
            closed_sink()

        self._control = capture.start_free_threaded()

    def stop(self) -> None:
        self._stop_requested.set()
        if self._control is not None:
            self._control.stop()
