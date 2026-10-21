"""Thread bridges between the (threaded, blocking) RecorderService and the Qt GUI thread.

- ServiceEventBridge: service listeners fire on worker threads; this re-emits them as a Qt signal
  that is delivered on the GUI thread.
- GuiThreadCaller: lets a non-GUI thread (control server, MCP) run a function on the GUI thread
  and wait for its result (used by the region picker).
- BackgroundRunner: runs blocking service calls off the GUI thread and reports back on it.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future
from typing import Callable, TypeVar

from PySide6.QtCore import QObject, Qt, QThread, Signal

from screen_recorder_mcp.service import RecorderEvent, RecorderService, Unsubscribe

T = TypeVar("T")


class ServiceEventBridge(QObject):
    """Example: bridge = ServiceEventBridge(service); bridge.event_received.connect(on_event)"""

    event_received = Signal(object)

    def __init__(self, service: RecorderService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._unsubscribe: Unsubscribe | None = service.subscribe(self._forward)

    def _forward(self, event: RecorderEvent) -> None:
        # Emitting from a foreign thread queues delivery to receivers living on the GUI thread.
        self.event_received.emit(event)

    def close(self) -> None:
        if self._unsubscribe is None:
            return
        self._unsubscribe()
        self._unsubscribe = None


class GuiThreadCaller(QObject):
    """Run callables on the thread this object lives on (create it on the GUI thread).

    Example: caller.call(lambda: overlay.exec_pick(bounds))  # safe from any thread
    """

    _requested = Signal(object, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._requested.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def call(self, task: Callable[[], T], timeout: float | None = None) -> T:
        if QThread.currentThread() is self.thread():
            return task()
        future: Future[T] = Future()
        self._requested.emit(task, future)
        return future.result(timeout)

    def _run(self, task: Callable[[], object], future: Future[object]) -> None:
        try:
            future.set_result(task())
        except BaseException as error:  # noqa: BLE001 - re-raised in the calling thread
            future.set_exception(error)


class BackgroundRunner(QObject):
    """Blocking work on a thread; callbacks always run on the GUI thread.

    Example: runner.submit(lambda: service.stop(), on_result=show_clip, on_error=show_error)
    """

    _finished = Signal(object, object, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._finished.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def submit(
        self,
        work: Callable[[], T],
        on_result: Callable[[T], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        threading.Thread(target=self._execute, args=(work, on_result, on_error), daemon=True).start()

    def _execute(
        self,
        work: Callable[[], object],
        on_result: Callable[..., None] | None,
        on_error: Callable[[Exception], None] | None,
    ) -> None:
        try:
            self._finished.emit(on_result, work(), None)
        except Exception as error:  # noqa: BLE001 - surfaced to on_error on the GUI thread
            self._finished.emit(on_error, None, error)

    def _deliver(
        self, callback: Callable[..., None] | None, result: object, error: Exception | None
    ) -> None:
        if callback is None:
            return
        callback(error if error is not None else result)
