"""Turn the user's words ("chrome", "monitor:2", a window id) into a capture target."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.window_registry import WindowLister, find_window

MONITOR_PREFIX = "monitor:"


@dataclass(frozen=True)
class CaptureTarget:
    """A window (window_id set) or a monitor (monitor_index set, 1-based)."""

    label: str
    rect: Rect
    window_id: int | None = None
    monitor_index: int | None = None
    pid: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "rect": self.rect.as_dict(),
            "window_id": self.window_id,
            "monitor_index": self.monitor_index,
        }


def resolve_target(
    query: str, lister: WindowLister, monitors: Callable[[], list[Rect]]
) -> CaptureTarget:
    """Resolve `monitor:N` (1-based) or any window query accepted by find_window.

    Example: resolve_target("monitor:1", lister, backend.monitor_rects).monitor_index -> 1
    """
    if query.strip().lower().startswith(MONITOR_PREFIX):
        return _monitor_target(query, monitors())
    window = find_window(lister, query)
    return CaptureTarget(
        label=f"{window.title} ({window.process})",
        rect=window.rect,
        window_id=window.window_id,
        pid=window.pid,
    )


def _monitor_target(query: str, rects: list[Rect]) -> CaptureTarget:
    raw_index = query.strip()[len(MONITOR_PREFIX) :]
    if not raw_index.isdigit() or not 1 <= int(raw_index) <= len(rects):
        raise LookupError(f"{query!r} is not a monitor; expected monitor:1..monitor:{len(rects)}")
    index = int(raw_index)
    return CaptureTarget(label=f"monitor {index}", rect=rects[index - 1], monitor_index=index)
