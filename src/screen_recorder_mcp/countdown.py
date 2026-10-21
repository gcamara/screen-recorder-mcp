"""The delay before capture starts (the Snipping Tool's snip delay)."""

from __future__ import annotations

import threading
from typing import Callable

# Returns True when the wait was cut short by cancellation.
TickWait = Callable[[threading.Event, float], bool]


def wait_on_event(cancelled: threading.Event, seconds: float) -> bool:
    return cancelled.wait(seconds)


def run_countdown(
    seconds: int,
    on_tick: Callable[[int], None],
    cancelled: threading.Event,
    wait: TickWait = wait_on_event,
) -> bool:
    """Call on_tick(remaining) once per second; True if it ran to zero, False if cancelled.

    Example: run_countdown(3, print, threading.Event()) prints 3, 2, 1 then returns True
    """
    for remaining in range(seconds, 0, -1):
        if cancelled.is_set():
            return False
        on_tick(remaining)
        if wait(cancelled, 1.0):
            return False
    return not cancelled.is_set()
