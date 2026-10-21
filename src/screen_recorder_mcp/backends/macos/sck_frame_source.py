"""Window/display video through ScreenCaptureKit.

A desktop-independent-window filter captures the window's own content, so
apps moved in front of it are never recorded (same guarantee as WGC on
Windows).
"""

from __future__ import annotations

from typing import Any

import CoreMedia
import Quartz
import ScreenCaptureKit as SCK
from AppKit import NSScreen

from screen_recorder_mcp.backends.macos import sck_content
from screen_recorder_mcp.backends.macos.sample_buffers import bgra_frame, is_complete_frame
from screen_recorder_mcp.backends.macos.sck_stream import CaptureStream
from screen_recorder_mcp.capture import ClosedCallback, FrameCallback

_MAX_CAPTURE_FPS = 60
_QUEUE_DEPTH = 5


class SckFrameSource:
    """Streams BGRA frames of one window (window_id) or display (display_id)."""

    def __init__(self, window_id: int | None, display_id: int | None, capture_cursor: bool) -> None:
        if (window_id is None) == (display_id is None):
            raise ValueError(
                f"window_id={window_id}, display_id={display_id}; expected exactly one of them"
            )
        self._window_id = window_id
        self._display_id = display_id
        self._capture_cursor = capture_cursor
        self._stream: CaptureStream | None = None

    def start(self, frame_sink: FrameCallback, closed_sink: ClosedCallback) -> None:
        content_filter, point_size = self._filter_and_size(sck_content.shareable_content())
        configuration = _video_configuration(point_size, _point_pixel_scale(content_filter), self._capture_cursor)

        def on_screen(sample_buffer: Any) -> None:
            if not is_complete_frame(sample_buffer):
                return
            frame = bgra_frame(sample_buffer)
            if frame is not None:
                frame_sink(frame)

        self._stream = CaptureStream(
            content_filter, configuration, screen=on_screen, audio=None,
            stopped=lambda _error: closed_sink(),
        )  # fmt: skip
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()

    def _filter_and_size(self, content: Any) -> tuple[Any, tuple[float, float]]:
        if self._window_id is not None:
            window = sck_content.find_sc_window(content, self._window_id)
            size = window.frame().size
            return SCK.SCContentFilter.alloc().initWithDesktopIndependentWindow_(window), (size.width, size.height)
        assert self._display_id is not None  # guaranteed by __init__
        display = sck_content.find_sc_display(content, self._display_id)
        content_filter = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(display, [])
        return content_filter, (float(display.width()), float(display.height()))


def _point_pixel_scale(content_filter: Any) -> float:
    # SCShareableContent.infoForFilter: is macOS 14+; on 13 fall back to the
    # sharpest attached screen so Retina content is never downscaled.
    info_for_filter = getattr(SCK.SCShareableContent, "infoForFilter_", None)
    if info_for_filter is not None:
        return float(info_for_filter(content_filter).pointPixelScale())
    return max(float(screen.backingScaleFactor()) for screen in NSScreen.screens())


def _video_configuration(point_size: tuple[float, float], scale: float, capture_cursor: bool) -> Any:
    """Fixed pixel size: SCK scales a resized window into it, keeping the encoder size constant."""
    configuration = SCK.SCStreamConfiguration.alloc().init()
    configuration.setWidth_(_even(point_size[0] * scale))
    configuration.setHeight_(_even(point_size[1] * scale))
    configuration.setPixelFormat_(Quartz.kCVPixelFormatType_32BGRA)
    configuration.setShowsCursor_(capture_cursor)
    configuration.setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, _MAX_CAPTURE_FPS))
    configuration.setQueueDepth_(_QUEUE_DEPTH)
    return configuration


def _even(value: float) -> int:
    rounded = int(round(value))
    return max(2, rounded - rounded % 2)
