"""Delivers RecorderEvents to listeners on one worker thread."""

from __future__ import annotations

import logging
import queue
import threading

from screen_recorder_mcp.service import RecorderEvent, RecorderListener, Unsubscribe

_log = logging.getLogger(__name__)


class EventBus:
    """publish() never blocks or raises; a failing listener only costs its own event.

    Example:
        bus = EventBus(); off = bus.subscribe(print); bus.publish(event); off()
    """

    def __init__(self) -> None:
        self._listeners: list[RecorderListener] = []
        self._lock = threading.Lock()
        self._queue: queue.Queue[RecorderEvent | None] = queue.Queue()
        self._worker = threading.Thread(target=self._deliver_forever, daemon=True)
        self._worker.start()

    def subscribe(self, listener: RecorderListener) -> Unsubscribe:
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def publish(self, event: RecorderEvent) -> None:
        self._queue.put(event)

    def flush(self) -> None:
        """Block until every published event was delivered (tests, orderly shutdown)."""
        self._queue.join()

    def close(self) -> None:
        self._queue.put(None)
        self._worker.join(timeout=5)

    def _deliver_forever(self) -> None:
        while True:
            event = self._queue.get()
            if event is not None:
                self._deliver(event)
            self._queue.task_done()
            if event is None:
                return

    def _deliver(self, event: RecorderEvent) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception as error:  # one broken UI must not stop the others or the recording
                _log.warning(
                    '{"event": "listener_failed", "kind": "%s", "error": "%s"}', event.kind.value, error
                )
