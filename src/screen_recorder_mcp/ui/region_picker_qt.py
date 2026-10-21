"""RegionPicker (service.py protocol) backed by the Qt overlay.

The service calls pick_region from the control-server / MCP thread; the overlay needs the GUI
thread, so the call is marshalled with GuiThreadCaller and a nested event loop waits for the user.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEventLoop, QTimer

from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.service import TargetList
from screen_recorder_mcp.ui.qt_bridge import GuiThreadCaller
from screen_recorder_mcp.ui.selection_session import ScreenInfo, SelectionSession, screens_from_qt
from screen_recorder_mcp.ui.theme_controller import ThemeController
from screen_recorder_mcp.ui.view_models import SelectionResult

PICK_TIMEOUT_SECONDS = 120


class QtRegionPicker:
    """Example: service = LocalRecorderService(backend, QtRegionPicker(theme, caller, service_targets))"""

    def __init__(
        self, theme: ThemeController, caller: GuiThreadCaller, targets: Callable[[], TargetList],
        screens: Callable[[], list[ScreenInfo]] = screens_from_qt,
    ) -> None:
        self._theme, self._caller, self._targets, self._screens = theme, caller, targets, screens

    def pick_region(self, bounds: Rect) -> Rect | None:
        """Show the overlay over `bounds`; the crop comes back relative to bounds, None = whole target."""
        return self._caller.call(lambda: self._pick_on_gui_thread(bounds))

    def _pick_on_gui_thread(self, bounds: Rect) -> Rect | None:
        session = SelectionSession(
            self._theme, self._targets(), self._screens(), constrain_to=bounds, start_label="Crop to this area"
        )
        outcome: list[SelectionResult | None] = []
        loop = QEventLoop()
        session.selected.connect(lambda result: (outcome.append(result), loop.quit()))
        session.cancelled.connect(lambda: (outcome.append(None), loop.quit()))
        QTimer.singleShot(PICK_TIMEOUT_SECONDS * 1000, loop.quit)
        session.show()
        loop.exec()
        session.close()
        return outcome[0].region if outcome and outcome[0] else None
