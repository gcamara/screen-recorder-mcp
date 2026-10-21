"""Synchronous lookups over ScreenCaptureKit's async shareable-content API."""

from __future__ import annotations

import threading
from typing import Any, Callable

import ScreenCaptureKit as SCK

_TIMEOUT_SECONDS = 10.0
_PERMISSION_HINT = (
    "grant Screen Recording to the app running this MCP server (Terminal, iTerm, "
    "Claude, ...) in System Settings > Privacy & Security, then restart it"
)


def wait_for_completion(start: Callable[[Callable[..., None]], None], what: str) -> tuple[Any, ...]:
    """Run an ObjC completion-handler API and block until it calls back.

    Example: content, error = wait_for_completion(
                 lambda done: SCShareableContent.getShareableContent...(..., done), "content")
    """
    finished = threading.Event()
    received: list[tuple[Any, ...]] = []

    def completion(*args: Any) -> None:
        received.append(args)
        finished.set()

    start(completion)
    if not finished.wait(_TIMEOUT_SECONDS):
        raise TimeoutError(
            f"ScreenCaptureKit {what} gave no answer in {_TIMEOUT_SECONDS}s; a pending "
            f"permission dialog can cause this: {_PERMISSION_HINT}"
        )
    return received[0]


def shareable_content() -> Any:
    """Every display, window (on or off screen) and app ScreenCaptureKit can see."""
    content, error = wait_for_completion(
        lambda done: SCK.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
            True, False, done
        ),
        "shareable content",
    )
    if error is not None or content is None:
        raise PermissionError(f"ScreenCaptureKit refused to list content ({error}); {_PERMISSION_HINT}")
    return content


def find_sc_window(content: Any, window_id: int) -> Any:
    for window in content.windows():
        if int(window.windowID()) == window_id:
            return window
    raise LookupError(f"window_id {window_id} is not capturable; expected one from list_targets")


def find_sc_display(content: Any, display_id: int) -> Any:
    for display in content.displays():
        if int(display.displayID()) == display_id:
            return display
    raise LookupError(f"display {display_id} is not capturable by ScreenCaptureKit")


def find_sc_application(content: Any, pid: int) -> Any:
    for application in content.applications():
        if int(application.processID()) == pid:
            return application
    raise LookupError(f"no capturable app with pid {pid}; is it still running?")
