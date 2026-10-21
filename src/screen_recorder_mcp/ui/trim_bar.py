"""Trim track with left/right handles and a playhead (Snipping Tool's TrimView), driven by TrimModel."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget

from screen_recorder_mcp.ui.theme_controller import ThemeController
from screen_recorder_mcp.ui.view_models import TrimModel
from screen_recorder_mcp.ui.widgets_common import qcolor

_HANDLE_W = 12
_TRACK_H = 36


class TrimBar(QWidget):
    """Example: bar = TrimBar(theme); bar.set_model(TrimModel(duration=8.0)); bar.trim_changed.connect(f)"""

    trim_changed = Signal(float, float)  # start, end (seconds)
    seek_requested = Signal(float)  # seconds, when the user clicks inside the kept range

    def __init__(self, theme: ThemeController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._model: TrimModel | None = None
        self._playhead = 0.0
        self._dragging: str | None = None
        self.setMinimumHeight(_TRACK_H + 12)
        self.setMouseTracking(True)
        theme.changed.connect(lambda _p: self.update())

    def sizeHint(self) -> QSize:
        return QSize(400, _TRACK_H + 12)

    def set_model(self, model: TrimModel) -> None:
        self._model = model
        self.update()

    def model(self) -> TrimModel | None:
        return self._model

    def set_playhead(self, seconds: float) -> None:
        self._playhead = seconds
        self.update()

    # --- geometry ---------------------------------------------------------
    def _x_of(self, seconds: float) -> float:
        assert self._model is not None
        usable = self.width() - 2 * _HANDLE_W
        return _HANDLE_W + usable * seconds / self._model.duration

    def _seconds_at(self, x: float) -> float:
        assert self._model is not None
        usable = max(1, self.width() - 2 * _HANDLE_W)
        return max(0.0, min(self._model.duration, (x - _HANDLE_W) / usable * self._model.duration))

    # --- interaction -----------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._model is None:
            return
        x = event.position().x()
        left, right = self._x_of(self._model.start), self._x_of(self._model.end)
        if abs(x - (left - _HANDLE_W / 2)) <= _HANDLE_W:
            self._dragging = "start"
        elif abs(x - (right + _HANDLE_W / 2)) <= _HANDLE_W:
            self._dragging = "end"
        else:
            self._dragging = "seek"
            self.seek_requested.emit(max(self._model.start, min(self._seconds_at(x), self._model.end)))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._model is None or self._dragging is None:
            return
        seconds = self._seconds_at(event.position().x())
        if self._dragging == "start":
            self._model.set_start(seconds)
        elif self._dragging == "end":
            self._model.set_end(seconds)
        else:
            self.seek_requested.emit(max(self._model.start, min(seconds, self._model.end)))
        self.trim_changed.emit(self._model.start, self._model.end)
        self.update()

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:
        self._dragging = None

    # --- painting --------------------------------------------------------
    def paintEvent(self, _event: object) -> None:
        if self._model is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        top = (self.height() - _TRACK_H) / 2
        self._paint_track(painter, top)
        self._paint_playhead(painter, top)

    def _paint_track(self, painter: QPainter, top: float) -> None:
        assert self._model is not None
        palette = self._theme.palette
        left, right = self._x_of(self._model.start), self._x_of(self._model.end)
        track = QRectF(_HANDLE_W, top, self.width() - 2 * _HANDLE_W, _TRACK_H)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(palette.surface_raised))
        painter.drawRoundedRect(track, 6, 6)
        painter.setBrush(qcolor(palette.accent_soft))
        painter.drawRect(QRectF(left, top, right - left, _TRACK_H))
        painter.setBrush(QColor(palette.accent))
        painter.drawRoundedRect(QRectF(left - _HANDLE_W, top, _HANDLE_W, _TRACK_H), 4, 4)
        painter.drawRoundedRect(QRectF(right, top, _HANDLE_W, _TRACK_H), 4, 4)
        painter.setPen(QPen(QColor(palette.accent_ink), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for x in (left - _HANDLE_W / 2, right + _HANDLE_W / 2):
            painter.drawLine(int(x), int(top + 11), int(x), int(top + _TRACK_H - 11))

    def _paint_playhead(self, painter: QPainter, top: float) -> None:
        x = self._x_of(min(self._playhead, self._model.duration))  # type: ignore[union-attr]
        painter.setPen(QPen(QColor(self._theme.palette.text), 2))
        painter.drawLine(int(x), int(top - 4), int(x), int(top + _TRACK_H + 4))
