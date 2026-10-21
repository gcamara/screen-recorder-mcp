"""AppShell: wires window, tray, controller and hotkey around ANY RecorderService.

Shared by the real app (LocalRecorderService) and the preview (DemoRecorderService), so what the
preview shows is the real UI code, not a copy.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from screen_recorder_mcp.service import RecorderService
from screen_recorder_mcp.ui.controller import RecordingController
from screen_recorder_mcp.ui.dialogs import Dialogs, QtDialogs
from screen_recorder_mcp.ui.main_window import MainWindow
from screen_recorder_mcp.ui.os_integration import GlobalHotkey
from screen_recorder_mcp.ui.qt_bridge import BackgroundRunner
from screen_recorder_mcp.ui.theme_controller import ThemeController
from screen_recorder_mcp.ui.thumbnails import ThumbnailCache, ThumbnailMaker
from screen_recorder_mcp.ui.tray import AppTray, brand_icon
from screen_recorder_mcp.ui.view_models import CaptureTargetMode


class AppShell(QObject):
    """Example: shell = AppShell(app, service, theme, Path(cache)); shell.show_window()"""

    def __init__(
        self, app: QApplication, service: RecorderService, theme: ThemeController, cache_dir: Path,
        dialogs: Dialogs | None = None, thumbnails: ThumbnailMaker | None = None, with_hotkey: bool = True,
    ) -> None:
        super().__init__(app)
        self._app, self._service, self.theme = app, service, theme
        self.runner = BackgroundRunner(self)
        self.controller = RecordingController(service, theme, self.runner, parent=self)
        self.window = MainWindow(
            service, self.controller, theme, self.runner, thumbnails or ThumbnailCache(cache_dir / "thumbs"),
            dialogs or QtDialogs(),
        )
        self.tray = AppTray(self.window)
        self.hotkey = GlobalHotkey(app, self.new_recording) if with_hotkey else None
        self._connect()
        theme.set_theme(service.get_settings().theme)
        app.setWindowIcon(brand_icon())

    def _connect(self) -> None:
        window, tray, controller = self.window, self.tray, self.controller
        window.new_recording_requested.connect(self._begin)
        window.quit_requested.connect(self.quit)
        tray.new_requested.connect(self.new_recording)
        tray.open_requested.connect(self.show_window)
        tray.stop_requested.connect(controller.stop_recording)
        tray.quit_requested.connect(self.quit)
        controller.active_changed.connect(tray.set_recording)
        controller.settings_changed.connect(self._on_settings)

    def start(self) -> None:
        """Show tray + window and register the global hotkey from settings."""
        self.tray.show()
        self.window.show()
        if self.hotkey is not None:
            self.hotkey.register(self._service.get_settings().hotkey)

    def _begin(self, mode: CaptureTargetMode, delay: int) -> None:
        self.controller.begin_selection(mode, delay)

    def new_recording(self) -> None:
        """Hotkey / tray entry: same as pressing New with the window's current mode and delay."""
        self._begin(self.window.selected_mode(), self.window.selected_delay())

    def show_window(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def _on_settings(self, settings: object) -> None:
        self.theme.set_theme(getattr(settings, "theme"))
        if self.hotkey is not None:
            self.hotkey.register(getattr(settings, "hotkey"))

    def quit(self) -> None:
        if self.hotkey is not None:
            self.hotkey.unregister()
        self.controller.close()
        self.tray.hide()
        self.window.prepare_quit()
        self._app.quit()
