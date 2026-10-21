"""Thread bridges: service events, GUI-thread calls and background work all land on the GUI thread."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from screen_recorder_mcp.service import RecorderEventKind, RecordingRequest
from screen_recorder_mcp.ui.qt_bridge import BackgroundRunner, GuiThreadCaller, ServiceEventBridge
from tests.ui_fakes import UiFakeRecorderService


def _pump_until(app: QApplication, done: list[object], seconds: float = 3.0, at_least: int = 1) -> None:
    end = time.monotonic() + seconds
    while len(done) < at_least and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def test_events_from_worker_threads_arrive_on_the_gui_thread(qapp: QApplication, tmp_path: Path) -> None:
    service = UiFakeRecorderService(tmp_path)
    bridge = ServiceEventBridge(service)
    seen: list[tuple[RecorderEventKind, bool]] = []
    bridge.event_received.connect(lambda e: seen.append((e.kind, QThread.currentThread() is qapp.thread())))
    worker = threading.Thread(target=lambda: service.start(RecordingRequest(target="monitor:1")))
    worker.start()
    worker.join()
    _pump_until(qapp, seen)
    assert seen == [(RecorderEventKind.STARTED, True)]
    bridge.close()
    service.stop()
    qapp.processEvents()
    assert len(seen) == 1  # closed bridge no longer forwards


def test_gui_thread_caller_returns_values_and_reraises(qapp: QApplication) -> None:
    caller = GuiThreadCaller()
    results: list[object] = []

    def from_worker() -> None:
        results.append(caller.call(lambda: QThread.currentThread() is qapp.thread()))
        try:
            caller.call(lambda: 1 / 0)
        except ZeroDivisionError as error:
            results.append(error)

    worker = threading.Thread(target=from_worker)
    worker.start()
    end = time.monotonic() + 3
    while worker.is_alive() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    assert results[0] is True and isinstance(results[1], ZeroDivisionError)
    assert caller.call(lambda: 42) == 42  # already on the GUI thread: runs inline


def test_background_runner_delivers_result_and_error_on_gui_thread(qapp: QApplication) -> None:
    runner = BackgroundRunner()
    got: list[object] = []
    runner.submit(lambda: 7, on_result=lambda v: got.append((v, QThread.currentThread() is qapp.thread())))
    _pump_until(qapp, got)
    runner.submit(lambda: 1 / 0, on_error=lambda e: got.append(type(e).__name__))
    _pump_until(qapp, got, at_least=2)
    assert got == [(7, True), "ZeroDivisionError"]
