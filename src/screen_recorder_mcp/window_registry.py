"""Find recordable top-level windows by title, process name or window id."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from screen_recorder_mcp.geometry import Rect


@dataclass(frozen=True)
class WindowInfo:
    """One top-level window. `window_id` is the HWND on Windows, the CGWindowID on macOS.

    `rect` is in the OS's desktop coordinates: physical pixels on Windows,
    points (top-left origin) on macOS.
    """

    window_id: int
    title: str
    process: str
    rect: Rect
    pid: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "window_id": self.window_id,
            "title": self.title,
            "process": self.process,
            "rect": self.rect.as_dict(),
        }


class WindowLister(Protocol):
    def list_windows(self) -> list[WindowInfo]: ...


def find_window(lister: WindowLister, query: str) -> WindowInfo:
    """Resolve a user's description of an app to exactly one window.

    Matches, in order: window id, exact title, process name (with or without
    .exe/.app), title substring, then process substring ("chrome" finds macOS's
    "Google Chrome"). Case-insensitive.

    Example: find_window(lister, "chrome") -> WindowInfo(process="chrome.exe", ...)
    """
    windows = lister.list_windows()
    needle = query.strip().lower()
    for matcher in (
        _by_window_id, _by_exact_title, _by_process, _by_title_substring, _by_process_substring
    ):
        matches = [window for window in windows if matcher(window, needle)]
        if matches:
            return _single(matches, query)
    raise LookupError(
        f"no window matches {query!r}; expected a window_id, a title or a process name "
        "from list_targets"
    )


def _single(matches: list[WindowInfo], query: str) -> WindowInfo:
    # Front-most wins: listers yield z-order, so index 0 is the one the user
    # most likely means. Ambiguity is only an error across processes.
    if len({window.process for window in matches}) > 1:
        titles = [
            f"{window.title} ({window.process}, window_id={window.window_id})"
            for window in matches
        ]
        raise LookupError(f"{query!r} is ambiguous, matches {titles}; pass the window_id instead")
    return matches[0]


def _by_window_id(window: WindowInfo, needle: str) -> bool:
    return needle.isdigit() and window.window_id == int(needle)


def _by_exact_title(window: WindowInfo, needle: str) -> bool:
    return window.title.lower() == needle


def _by_process(window: WindowInfo, needle: str) -> bool:
    process = window.process.lower()
    return process == needle or process == f"{needle}.exe" or process == f"{needle}.app"


def _by_title_substring(window: WindowInfo, needle: str) -> bool:
    return needle in window.title.lower()


def _by_process_substring(window: WindowInfo, needle: str) -> bool:
    return needle in window.process.lower()
