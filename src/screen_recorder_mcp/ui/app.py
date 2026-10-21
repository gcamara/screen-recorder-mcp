"""Entry point: `screen-recorder` builds the real service, exposes it to MCP and shows the UI."""

from __future__ import annotations

import json
import logging
import sys

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


def main() -> int:
    """Run the desktop app; returns the process exit code.

    Example: uv run screen-recorder
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
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
