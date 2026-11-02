"""Entry point: `screen-recorder` builds the real service, exposes it to MCP and shows the UI."""

from __future__ import annotations

import getpass
import json
import logging
import os
import sys
from pathlib import Path
from typing import Callable

from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from screen_recorder_mcp.service import RecorderService, app_data_dir
from screen_recorder_mcp.ui.qt_bridge import GuiThreadCaller
from screen_recorder_mcp.ui.region_picker_qt import QtRegionPicker
from screen_recorder_mcp.ui.shell import AppShell
from screen_recorder_mcp.ui.theme_controller import ThemeController, load_bundled_fonts

log = logging.getLogger(__name__)


def build_service(theme: ThemeController, caller: GuiThreadCaller) -> RecorderService:
    """Create the in-process service with the Qt region picker.

    Imported lazily: LocalRecorderService / the OS backend live on other branches and are only
    needed when the real app runs (the preview and tests use DemoRecorderService).
    """
    from screen_recorder_mcp.backends import current_backend
    from screen_recorder_mcp.service_local import LocalRecorderService

    holder: list[RecorderService] = []
    picker = QtRegionPicker(theme, caller, lambda: holder[0].list_targets())
    service: RecorderService = LocalRecorderService(current_backend(), picker)
    holder.append(service)
    return service


def ensure_log_stream(log_dir: Path) -> None:
    """Point stderr at `log_dir/app.log` when the app runs without a console.

    The GUI launcher (pythonw) starts with sys.stderr = None, so logging and
    uncaught tracebacks would be lost; send them to a file instead.
    Example: ensure_log_stream(app_data_dir())  # then logging.basicConfig(...)
    """
    if sys.stderr is not None:
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    sys.stderr = (log_dir / "app.log").open("a", encoding="utf-8", buffering=1)


def instance_key() -> str:
    """Local socket name shared by every launch of the app for this user."""
    return f"screen-recorder-mcp-{getpass.getuser()}"


def hand_over_to_running_instance(key: str) -> bool:
    """Ask an already running app to show its window; True means this launch should just exit.

    A second instance would fail to register the hotkey and fight over control.json, so a
    second double-click on the shortcut only brings the first window forward.
    """
    socket = QLocalSocket()
    socket.connectToServer(key)
    if not socket.waitForConnected(500):
        return False
    socket.write(b"show\n")
    socket.waitForBytesWritten(500)
    socket.disconnectFromServer()
    return True


def listen_for_later_launches(key: str, on_show: Callable[[], None]) -> QLocalServer:
    QLocalServer.removeServer(key)  # a crashed instance can leave the name behind
    server = QLocalServer()
    if not server.listen(key):
        log.warning(json.dumps({"event": "instance_listen_failed", "error": server.errorString()}))

    def accept() -> None:
        connection = server.nextPendingConnection()
        if connection is not None:
            connection.disconnectFromServer()
        on_show()

    server.newConnection.connect(accept)
    return server


def main() -> int:
    """Run the desktop app; returns the process exit code.

    Example: uv run screen-recorder
    """
    ensure_log_stream(app_data_dir())
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # Decode playback on the CPU: hardware decoding fails (and retries) when VRAM is full,
    # and a screen recording at desktop size is cheap to decode in software anyway.
    os.environ.setdefault("QT_FFMPEG_DECODING_HW_DEVICE_TYPES", "")
    app = QApplication(sys.argv)
    app.setApplicationName("Screen Recorder")
    app.setQuitOnLastWindowClosed(False)  # lives in the tray
    key = instance_key()
    if hand_over_to_running_instance(key):
        log.info(json.dumps({"event": "already_running", "action": "showed the existing window"}))
        return 0
    load_bundled_fonts()
    theme = ThemeController(app)
    caller = GuiThreadCaller(app)
    service = build_service(theme, caller)
    from screen_recorder_mcp.control.server import serve_control

    handle = serve_control(service)
    log.info(json.dumps({"event": "control_server_started", "port": handle.port, "data_dir": str(app_data_dir())}))
    shell = AppShell(app, service, theme, app_data_dir())
    instance_server = listen_for_later_launches(key, shell.show_window)  # noqa: F841 (kept alive for app.exec)
    shell.start()
    try:
        return app.exec()
    finally:
        handle.stop()


if __name__ == "__main__":
    raise SystemExit(main())
