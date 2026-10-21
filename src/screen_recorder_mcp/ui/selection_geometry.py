"""Pure rectangle maths for the selection overlay (no Qt): handles, resize, nudge, DPI, monitor matching."""

from __future__ import annotations

from enum import Enum
from typing import Callable

from screen_recorder_mcp.geometry import Rect

MIN_SIZE = 16


class Handle(str, Enum):
    TOP_LEFT = "tl"
    TOP = "t"
    TOP_RIGHT = "tr"
    RIGHT = "r"
    BOTTOM_RIGHT = "br"
    BOTTOM = "b"
    BOTTOM_LEFT = "bl"
    LEFT = "l"
    BODY = "body"


def contains(rect: Rect, x: int, y: int) -> bool:
    return rect.x <= x < rect.x + rect.width and rect.y <= y < rect.y + rect.height


def handle_points(rect: Rect) -> dict[Handle, tuple[int, int]]:
    """Centre of each of the eight resize handles."""
    left, top = rect.x, rect.y
    right, bottom = rect.x + rect.width, rect.y + rect.height
    mid_x, mid_y = left + rect.width // 2, top + rect.height // 2
    return {
        Handle.TOP_LEFT: (left, top), Handle.TOP: (mid_x, top), Handle.TOP_RIGHT: (right, top),
        Handle.RIGHT: (right, mid_y), Handle.BOTTOM_RIGHT: (right, bottom), Handle.BOTTOM: (mid_x, bottom),
        Handle.BOTTOM_LEFT: (left, bottom), Handle.LEFT: (left, mid_y),
    }


def handle_at(rect: Rect, x: int, y: int, tolerance: int = 8) -> Handle | None:
    """Which handle (or the body) is under the point; None when outside.

    Example: handle_at(Rect(10, 10, 100, 50), 10, 10) -> Handle.TOP_LEFT
    """
    for handle, (hx, hy) in handle_points(rect).items():
        if abs(x - hx) <= tolerance and abs(y - hy) <= tolerance:
            return handle
    return Handle.BODY if contains(rect, x, y) else None


def drag_rect(start: tuple[int, int], end: tuple[int, int], bounds: Rect) -> Rect | None:
    """Rect spanned by a drag, clamped to bounds; None while it is smaller than MIN_SIZE."""
    left = max(bounds.x, min(start[0], end[0]))
    top = max(bounds.y, min(start[1], end[1]))
    right = min(bounds.x + bounds.width, max(start[0], end[0]))
    bottom = min(bounds.y + bounds.height, max(start[1], end[1]))
    if right - left < MIN_SIZE or bottom - top < MIN_SIZE:
        return None
    return Rect(left, top, right - left, bottom - top)


def resize(rect: Rect, handle: Handle, dx: int, dy: int, bounds: Rect) -> Rect:
    """Move `handle` by (dx, dy) from the ORIGINAL rect; keeps MIN_SIZE and stays inside bounds."""
    if handle is Handle.BODY:
        return nudge(rect, dx, dy, bounds)
    left, top, right, bottom = rect.x, rect.y, rect.x + rect.width, rect.y + rect.height
    if "l" in handle.value:
        left = max(bounds.x, min(left + dx, right - MIN_SIZE))
    if "r" in handle.value:
        right = min(bounds.x + bounds.width, max(right + dx, left + MIN_SIZE))
    if handle.value.startswith("t"):
        top = max(bounds.y, min(top + dy, bottom - MIN_SIZE))
    if handle.value.startswith("b"):
        bottom = min(bounds.y + bounds.height, max(bottom + dy, top + MIN_SIZE))
    return Rect(left, top, right - left, bottom - top)


def nudge(rect: Rect, dx: int, dy: int, bounds: Rect) -> Rect:
    """Translate the rect, never leaving bounds (arrow-key nudging, Snipping Tool's KeyboardClipping)."""
    x = max(bounds.x, min(rect.x + dx, bounds.x + bounds.width - rect.width))
    y = max(bounds.y, min(rect.y + dy, bounds.y + bounds.height - rect.height))
    return Rect(x, y, rect.width, rect.height)


def window_at(x: int, y: int, windows: list[Rect]) -> int | None:
    """Index of the first (front-most) window rect containing the point."""
    for index, rect in enumerate(windows):
        if contains(rect, x, y):
            return index
    return None


def local_to_physical(rect: Rect, origin: Rect, ratio: float) -> Rect:
    """Widget-local logical px -> desktop physical px (origin = the monitor's physical rect)."""
    return Rect(
        origin.x + round(rect.x * ratio), origin.y + round(rect.y * ratio),
        round(rect.width * ratio), round(rect.height * ratio),
    )


def physical_to_local(rect: Rect, origin: Rect, ratio: float) -> Rect:
    return Rect(
        round((rect.x - origin.x) / ratio), round((rect.y - origin.y) / ratio),
        round(rect.width / ratio), round(rect.height / ratio),
    )


def match_monitors(screens_physical: list[Rect], monitors: list[Rect]) -> list[int | None]:
    """1-based backend monitor index for each Qt screen: exact rect, else same size, else None.

    Each backend monitor is used once, so two identical displays map to two indexes.
    Example: match_monitors([Rect(0,0,1920,1080)], [Rect(0,0,1920,1080)]) -> [1]
    """
    taken: set[int] = set()
    result: list[int | None] = []
    for screen in screens_physical:
        pick = _first_free(monitors, taken, lambda m: m == screen)
        if pick is None:
            pick = _first_free(monitors, taken, lambda m: (m.width, m.height) == (screen.width, screen.height))
        if pick is not None:
            taken.add(pick)
        result.append(pick)
    return result


def _first_free(monitors: list[Rect], taken: set[int], accepts: Callable[[Rect], bool]) -> int | None:
    for index, monitor in enumerate(monitors, 1):
        if index not in taken and accepts(monitor):
            return index
    return None
