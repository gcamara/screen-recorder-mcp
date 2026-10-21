"""Entry point: `screen-recorder` builds the real service, exposes it to MCP and shows the UI."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

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
    load_bundled_fonts()
    theme = ThemeController(app)
    caller = GuiThreadCaller(app)
    service = build_service(theme, caller)
    from screen_recorder_mcp.control.server import serve_control

    handle = serve_control(service)
    log.info(json.dumps({"event": "control_server_started", "port": handle.port, "data_dir": str(app_data_dir())}))
    shell = AppShell(app, service, theme, app_data_dir())
    shell.start()
    try:
        return app.exec()
    finally:
        handle.stop()


if __name__ == "__main__":
    raise SystemExit(main())
