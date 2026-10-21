"""Orchestrates the overlays of every monitor plus the floating mode toolbar for one selection."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication

from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.service import TargetList
from screen_recorder_mcp.ui import selection_geometry as geo
from screen_recorder_mcp.ui.selection_overlay import MonitorOverlay
from screen_recorder_mcp.ui.selection_toolbar import SelectionToolbar
from screen_recorder_mcp.ui.theme_controller import ThemeController
from screen_recorder_mcp.ui.view_models import CaptureTargetMode, SelectionResult


@dataclass(frozen=True)
class ScreenInfo:
    logical: Rect  # Qt virtual-desktop coordinates (device independent)
    ratio: float  # device pixel ratio

    @property
    def physical_size(self) -> Rect:
        return Rect(round(self.logical.x * self.ratio), round(self.logical.y * self.ratio),
                    round(self.logical.width * self.ratio), round(self.logical.height * self.ratio))


def screens_from_qt() -> list[ScreenInfo]:
    """Snapshot of the connected screens, primary first."""
    infos = []
    for screen in QGuiApplication.screens():
        g = screen.geometry()
        infos.append(ScreenInfo(Rect(g.x(), g.y(), g.width(), g.height()), screen.devicePixelRatio()))
    return infos


class SelectionSession(QObject):
    """One pick: shows smoke on all monitors and emits `selected` or `cancelled`.

    Example: s = SelectionSession(theme, targets, screens_from_qt()); s.selected.connect(go); s.show()
    """

    selected = Signal(object)  # SelectionResult
    cancelled = Signal()

    def __init__(
        self, theme: ThemeController, targets: TargetList, screens: list[ScreenInfo],
        mode: CaptureTargetMode = CaptureTargetMode.RECTANGLE, constrain_to: Rect | None = None,
        start_label: str = "Start", toolbar_screen: int = 0,
    ) -> None:
        super().__init__()
        self._theme, self._mode, self._constrain = theme, mode, constrain_to
        monitors = [Rect(**m["rect"]) for m in targets.monitors]  # type: ignore[arg-type]
        indexes = geo.match_monitors([s.physical_size for s in screens], monitors)
        self._overlays = self._build_overlays(screens, indexes, monitors, targets, start_label)
        self._toolbar = None if constrain_to else SelectionToolbar(theme, mode)
        self._toolbar_screen = screens[min(toolbar_screen, len(screens) - 1)].logical
        if self._toolbar:
            self._toolbar.mode_selected.connect(self.set_mode)
            self._toolbar.closed.connect(self._cancel)

    def _build_overlays(
        self, screens: list[ScreenInfo], indexes: list[int | None], monitors: list[Rect],
        targets: TargetList, start_label: str,
    ) -> list[MonitorOverlay]:
        overlays = []
        for screen, index in zip(screens, indexes):
            physical = monitors[index - 1] if index else screen.physical_size
            overlay = MonitorOverlay(screen.logical, screen.ratio, index, physical, targets.windows, self._theme, start_label)
            overlay.chosen.connect(self._finish)
            overlay.cancelled.connect(self._cancel)
            overlays.append(overlay)
        return overlays

    def show(self) -> None:
        for overlay in self._overlays:
            overlay.set_mode(self._mode)
        if self._constrain is not None:
            self._show_constrained(self._constrain)
        else:
            self._show_all()

    def _show_all(self) -> None:
        for overlay in self._overlays:
            overlay.show()
        if self._toolbar is not None:
            self._toolbar.show()
            screen = self._toolbar_screen
            self._toolbar.move(screen.x + (screen.width - self._toolbar.width()) // 2, screen.y + 24)
        self._overlays[0].activateWindow()

    def _show_constrained(self, bounds: Rect) -> None:
        host = next((o for o in self._overlays if o.contains_physical(bounds)), self._overlays[0])
        self._overlays = [host]
        host.limit_to(bounds)
        host.show()
        host.activateWindow()

    def set_mode(self, mode: CaptureTargetMode) -> None:
        self._mode = mode
        for overlay in self._overlays:
            overlay.set_mode(mode)
        if self._toolbar is not None:
            self._toolbar.set_mode(mode)

    def toolbar(self) -> SelectionToolbar | None:
        return self._toolbar

    def overlays(self) -> list[MonitorOverlay]:
        return list(self._overlays)

    def close(self) -> None:
        for overlay in self._overlays:
            overlay.hide()
        if self._toolbar is not None:
            self._toolbar.hide()

    def _finish(self, result: SelectionResult) -> None:
        self.close()
        self.selected.emit(result)

    def _cancel(self) -> None:
        self.close()
        self.cancelled.emit()
