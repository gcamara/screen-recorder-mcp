"""Ring buffer of recorder events with sequence numbers, so clients can long-poll."""

from __future__ import annotations

import threading
from collections import deque

_BUFFER_SIZE = 500


class EventBus:
    """Thread-safe; `publish` is called from the service's threads, `poll` from HTTP threads."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._events: deque[tuple[int, object]] = deque(maxlen=_BUFFER_SIZE)
        self._last_seq = 0
        self._closed = False

    @property
    def last_seq(self) -> int:
        with self._condition:
            return self._last_seq

    def publish(self, event_json: object) -> None:
        with self._condition:
            self._last_seq += 1
            self._events.append((self._last_seq, event_json))
            self._condition.notify_all()

    def close(self) -> None:
        """Wakes every waiting poll so server shutdown never hangs on a long-poll."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def poll(self, after: int | None, timeout_seconds: float) -> tuple[list[dict[str, object]], int]:
        """Events with seq > after, waiting up to timeout_seconds when there are none.

        `after=None` means "only what happens from now": returns no events and the current seq.
        Example: events, last = bus.poll(after=last, timeout_seconds=20)
        """
        with self._condition:
            if after is None:
                return [], self._last_seq
            self._condition.wait_for(lambda: self._closed or self._last_seq > after, timeout_seconds)
            fresh = [{"seq": seq, "event": body} for seq, body in self._events if seq > after]
            return fresh, self._last_seq
