"""Window and display listing through Quartz (CoreGraphics).

Coordinates are points with a top-left origin, the same space Tk uses on
macOS, so the region picker overlays them directly.
"""

from __future__ import annotations

from typing import Any

import Quartz
from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication

from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.window_registry import WindowInfo

_NORMAL_WINDOW_LAYER = 0
_MIN_UNTITLED_SIDE = 64
_MAX_DISPLAYS = 16


class QuartzWindowLister:
    """On-screen, normal-layer windows, front-most first.

    Titles are empty unless the host app has the Screen Recording permission,
    so untitled windows are kept (big enough ones) and matched by owner name.
    """

    def list_windows(self) -> list[WindowInfo]:
        options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
        infos = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []
        windows = [_describe(info) for info in infos if _is_app_window(info)]
        return [window for window in windows if _is_recordable(window)]


def _is_app_window(info: dict[str, Any]) -> bool:
    return info.get(Quartz.kCGWindowLayer) == _NORMAL_WINDOW_LAYER


def _describe(info: dict[str, Any]) -> WindowInfo:
    bounds = info.get(Quartz.kCGWindowBounds) or {}
    return WindowInfo(
        window_id=int(info[Quartz.kCGWindowNumber]),
        title=str(info.get(Quartz.kCGWindowName) or ""),
        process=str(info.get(Quartz.kCGWindowOwnerName) or ""),
        rect=Rect(
            int(bounds.get("X", 0)),
            int(bounds.get("Y", 0)),
            int(bounds.get("Width", 0)),
            int(bounds.get("Height", 0)),
        ),
        pid=int(info.get(Quartz.kCGWindowOwnerPID, 0)),
    )


def _is_recordable(window: WindowInfo) -> bool:
    if window.title.strip():
        return True
    big_enough = min(window.rect.width, window.rect.height) >= _MIN_UNTITLED_SIDE
    return bool(window.process) and big_enough


def active_display_ids() -> list[int]:
    """Active displays, main display first (the order `monitor:N` uses).

    Example: active_display_ids() -> [1, 3]
    """
    error, display_ids, count = Quartz.CGGetActiveDisplayList(_MAX_DISPLAYS, None, None)
    if error:
        raise RuntimeError(f"CGGetActiveDisplayList failed with CGError {error}; expected 0")
    return [int(display_id) for display_id in display_ids[:count]]


def display_rect(display_id: int) -> Rect:
    bounds = Quartz.CGDisplayBounds(display_id)
    return Rect(
        int(bounds.origin.x),
        int(bounds.origin.y),
        int(bounds.size.width),
        int(bounds.size.height),
    )


def activate_app(pid: int) -> None:
    """Best effort: bring the owning app forward before the region overlay opens."""
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app is not None:
        app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)


def init_window_server_connection() -> None:
    # Touching CoreGraphics opens the window-server connection that
    # ScreenCaptureKit needs in a non-app (CLI) process. Deliberately no
    # NSApplication here: the Tk region picker creates its own.
    Quartz.CGMainDisplayID()
