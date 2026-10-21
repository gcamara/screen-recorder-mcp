"""One dimmed overlay per monitor (SnipOverlayView / "smoke"): rectangle, window and full-screen picking."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QPushButton, QWidget

from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.ui import selection_geometry as geo
from screen_recorder_mcp.ui.os_integration import exclude_from_capture
from screen_recorder_mcp.ui.selection_geometry import Handle
from screen_recorder_mcp.ui.theme import MONO_FAMILY
from screen_recorder_mcp.ui.theme_controller import ThemeController
from screen_recorder_mcp.ui.view_models import CaptureTargetMode, SelectionResult
from screen_recorder_mcp.ui.widgets_common import qcolor

_ARROWS = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
_HINTS = {
    CaptureTargetMode.RECTANGLE: "Drag to select an area to record",
    CaptureTargetMode.WINDOW: "Click a window to record it",
    CaptureTargetMode.FULLSCREEN: "Click to record this screen",
}


def to_qrect(rect: Rect) -> QRect:
    return QRect(rect.x, rect.y, rect.width, rect.height)


class MonitorOverlay(QWidget):
    """Covers one screen. Coordinates inside are widget-local logical px.

    Example: overlay = MonitorOverlay(screen, 1, Rect(0,0,1920,1080), windows, theme); overlay.show()
    """

    chosen = Signal(object)  # SelectionResult
    cancelled = Signal()

    def __init__(
        self, screen_logical: Rect, ratio: float, monitor_index: int | None, monitor_physical: Rect,
        windows: list[dict[str, object]], theme: ThemeController, start_label: str = "Start",
    ) -> None:
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setGeometry(to_qrect(screen_logical))
        self._screen, self._ratio, self._index, self._monitor = screen_logical, ratio, monitor_index, monitor_physical
        self._theme, self._mode = theme, CaptureTargetMode.RECTANGLE
        self._windows = self._local_windows(windows)
        self._selection: Rect | None = None
        self._limit: Rect | None = None
        self._limit_origin: Rect | None = None
        self._hover: tuple[Rect, str] | None = None
        self._press: tuple[Handle | None, Rect | None, QPoint] | None = None
        self._start = QPushButton(start_label, self)
        self._start.setProperty("role", "primary")
        self._start.setCursor(Qt.CursorShape.PointingHandCursor)
        self._start.clicked.connect(self._finish_rectangle)
        self._start.hide()

    # --- configuration ---------------------------------------------------
    def _local_windows(self, windows: list[dict[str, object]]) -> list[tuple[dict[str, object], Rect]]:
        local = [(w, geo.physical_to_local(Rect(**w["rect"]), self._monitor, self._ratio)) for w in windows]  # type: ignore[arg-type]
        return [(w, r) for w, r in local if r.width > 0 and r.height > 0]

    def set_mode(self, mode: CaptureTargetMode) -> None:
        self._mode = mode
        self._selection, self._hover, self._press = None, None, None
        self._start.hide()
        self.setCursor(Qt.CursorShape.CrossCursor if mode is CaptureTargetMode.RECTANGLE else Qt.CursorShape.PointingHandCursor)
        self.update()

    def limit_to(self, physical: Rect) -> None:
        """Restrict picking to a target's bounds (region picker); starts with it fully selected."""
        self._limit_origin = physical
        self._limit = geo.physical_to_local(physical, self._monitor, self._ratio)
        self._selection = self._limit
        self._place_start()
        self.update()

    def contains_physical(self, rect: Rect) -> bool:
        """True when the centre of `rect` (desktop physical px) lies on this overlay's monitor."""
        return geo.contains(self._monitor, rect.x + rect.width // 2, rect.y + rect.height // 2)

    def selection(self) -> Rect | None:
        return self._selection

    def set_selection(self, rect: Rect | None) -> None:
        self._selection = rect
        self._place_start()
        self.update()

    def _bounds(self) -> Rect:
        return self._limit or Rect(0, 0, self.width(), self.height())

    # --- mouse / keys ----------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._mode is not CaptureTargetMode.RECTANGLE:
            self._pick_hovered(pos)
            return
        handle = geo.handle_at(self._selection, pos.x(), pos.y()) if self._selection else None
        self._press = (handle, self._selection, pos)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        if self._mode is not CaptureTargetMode.RECTANGLE:
            self._update_hover(pos)
            return
        if self._press is None:
            self._update_cursor(pos)
            return
        self._drag_to(pos)

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:
        self._press = None
        self._place_start()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        if self._selection and geo.contains(self._selection, pos.x(), pos.y()):
            self._finish_rectangle()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.cancelled.emit()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._selection:
            self._finish_rectangle()
        elif key in _ARROWS and self._selection:
            step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            dx, dy = _ARROWS[key]
            self.set_selection(geo.nudge(self._selection, dx * step, dy * step, self._bounds()))

    # --- behaviour -------------------------------------------------------
    def _drag_to(self, pos: QPoint) -> None:
        handle, origin, anchor = self._press  # type: ignore[misc]
        if handle is not None and origin is not None:
            delta = pos - anchor
            self._selection = geo.resize(origin, handle, delta.x(), delta.y(), self._bounds())
        else:
            self._selection = geo.drag_rect((anchor.x(), anchor.y()), (pos.x(), pos.y()), self._bounds())
        self._start.hide()
        self.update()

    def _update_cursor(self, pos: QPoint) -> None:
        handle = geo.handle_at(self._selection, pos.x(), pos.y()) if self._selection else None
        shapes = {"tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
                  "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
                  "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
                  "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
                  "body": Qt.CursorShape.SizeAllCursor}
        self.setCursor(shapes.get(handle.value, Qt.CursorShape.CrossCursor) if handle else Qt.CursorShape.CrossCursor)

    def _update_hover(self, pos: QPoint) -> None:
        if self._mode is CaptureTargetMode.FULLSCREEN:
            self._hover = (Rect(0, 0, self.width(), self.height()), f"Screen {self._index or ''}".strip())
        else:
            found = geo.window_at(pos.x(), pos.y(), [r for _, r in self._windows])
            self._hover = None if found is None else (self._windows[found][1], str(self._windows[found][0]["title"]))
        self.update()

    def _place_start(self) -> None:
        if self._selection is None or self._mode is not CaptureTargetMode.RECTANGLE:
            self._start.hide()
            return
        self._start.adjustSize()
        rect, size = self._selection, self._start.size()
        x = min(rect.x + rect.width - size.width(), self.width() - size.width() - 8)
        y = rect.y + rect.height + 10
        if y + size.height() > self.height() - 8:
            y = max(8, rect.y + rect.height - size.height() - 10)  # no room below: sit inside
        self._start.move(max(8, x), y)
        self._start.show()
        self._start.raise_()

    def _pick_hovered(self, pos: QPoint) -> None:
        self._update_hover(pos)
        if self._hover is None:
            return
        if self._mode is CaptureTargetMode.FULLSCREEN:
            self._emit(CaptureTargetMode.FULLSCREEN, self._monitor_target(), None, self._hover[0], self._hover[1])
            return
        window = next(w for w, r in self._windows if r == self._hover[0] and str(w["title"]) == self._hover[1])
        self._emit(CaptureTargetMode.WINDOW, str(window["window_id"]), None, self._hover[0], self._hover[1])

    def _finish_rectangle(self) -> None:
        if self._selection is None:
            return
        physical = geo.local_to_physical(self._selection, self._monitor, self._ratio)
        origin = self._limit_origin or self._monitor  # region picker: relative to the target, not the monitor
        relative = Rect(physical.x - origin.x, physical.y - origin.y, physical.width, physical.height)
        target = "" if self._limit_origin else self._monitor_target()  # region picker: target already chosen
        self._emit(CaptureTargetMode.RECTANGLE, target, relative, self._selection, "Selected area")

    def _monitor_target(self) -> str:
        if self._index is None:
            raise LookupError(f"screen at {self._monitor} has no matching monitor in the backend's monitor list")
        return f"monitor:{self._index}"

    def _emit(self, mode: CaptureTargetMode, target: str, region: Rect | None, area: Rect, label: str) -> None:
        origin = self._screen
        global_area = Rect(origin.x + area.x, origin.y + area.y, area.width, area.height)
        self.chosen.emit(SelectionResult(mode, target, region, global_area, origin, label))

    # --- painting --------------------------------------------------------
    def showEvent(self, event: object) -> None:
        super().showEvent(event)  # type: ignore[arg-type]
        exclude_from_capture(self)

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hole = self._selection or (self._hover[0] if self._hover else None)
        self._paint_smoke(painter, hole)
        if hole is None:
            self._paint_hint(painter)
            return
        self._paint_frame(painter, hole)

    def _paint_smoke(self, painter: QPainter, hole: Rect | None) -> None:
        path = QPainterPath()
        path.addRect(QRectF(self.rect()))
        if hole is not None:
            path.addRect(QRectF(to_qrect(hole)))
        painter.fillPath(path, qcolor(self._theme.palette.scrim))
        if hole is not None:
            # alpha 2/255: invisible, but keeps the hole hit-testable on layered Windows surfaces.
            painter.fillRect(to_qrect(hole), QColor(0, 0, 0, 2))

    def _paint_frame(self, painter: QPainter, hole: Rect) -> None:
        palette = self._theme.palette
        painter.setPen(QPen(QColor(palette.accent), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(to_qrect(hole))
        if self._selection is not None and self._mode is CaptureTargetMode.RECTANGLE:
            self._paint_handles(painter, hole)
        self._paint_label(painter, hole)

    def _paint_handles(self, painter: QPainter, rect: Rect) -> None:
        painter.setPen(QPen(QColor(self._theme.palette.accent_ink), 1))
        painter.setBrush(QColor(self._theme.palette.accent_strong))
        for x, y in geo.handle_points(rect).values():
            painter.drawRoundedRect(QRectF(x - 5, y - 5, 10, 10), 3, 3)

    def _paint_label(self, painter: QPainter, rect: Rect) -> None:
        if self._selection is not None:
            text = f"{round(rect.width * self._ratio)} x {round(rect.height * self._ratio)}"
        else:
            text = self._hover[1] if self._hover else ""
        self._paint_pill(painter, text, rect.x + 8, max(8, rect.y - 30), mono=self._selection is not None)

    def _paint_hint(self, painter: QPainter) -> None:
        hint = _HINTS[self._mode]
        self._paint_pill(painter, hint, 0, self.height() // 2 - 14, center=True)

    def _paint_pill(self, painter: QPainter, text: str, x: int, y: int, mono: bool = False, center: bool = False) -> None:
        if not text:
            return
        font = QFont(MONO_FAMILY if mono else self.font().family(), 10)
        painter.setFont(font)
        width = painter.fontMetrics().horizontalAdvance(text) + 24
        left = (self.width() - width) // 2 if center else x
        palette = self._theme.palette
        painter.setPen(QPen(QColor(palette.border_strong), 1))
        painter.setBrush(QColor(palette.surface_overlay))
        painter.drawRoundedRect(QRectF(left, y, width, 26), 13, 13)
        painter.setPen(QColor(palette.text))
        painter.drawText(QRectF(left, y, width, 26), Qt.AlignmentFlag.AlignCenter, text)
