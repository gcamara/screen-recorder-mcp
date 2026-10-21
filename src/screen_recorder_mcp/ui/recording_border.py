"""Coloured frame around the area being recorded (Snipping Tool draws one too).

It sits OUTSIDE the recorded rectangle, ignores the mouse, and is excluded from screen
capture, so it can never end up in the video even for monitor/rectangle recordings.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.ui.os_integration import exclude_from_capture
from screen_recorder_mcp.ui.theme_controller import ThemeController

BORDER_WIDTH = 3


def frame_geometry(area: Rect, width: int = BORDER_WIDTH) -> Rect:
    """Window rect for the frame: the area grown by `width` on every side.

    Example: frame_geometry(Rect(100, 100, 400, 300), 3) -> Rect(97, 97, 406, 306)
    """
    return Rect(area.x - width, area.y - width, area.width + 2 * width, area.height + 2 * width)


class RecordingBorder(QWidget):
    """Example: border = RecordingBorder(theme); border.show_around(Rect(100, 100, 800, 450))"""

    def __init__(self, theme: ThemeController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._theme = theme
        self._paused = False
        self._area: Rect | None = None

    def show_around(self, area: Rect) -> None:
        """Place the frame around `area` (global logical px) and show it."""
        self.move_to(area)
        self.show()

    def move_to(self, area: Rect) -> None:
        if area == self._area:
            return
        self._area = area
        frame = frame_geometry(area)
        self.setGeometry(QRect(frame.x, frame.y, frame.width, frame.height))

    def area(self) -> Rect | None:
        return self._area

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self.update()

    def paintEvent(self, _event: object) -> None:
        palette = self._theme.palette
        painter = QPainter(self)
        pen = QPen(QColor(palette.warning if self._paused else palette.failure), BORDER_WIDTH)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        half = BORDER_WIDTH / 2
        painter.drawRect(QRectF(half, half, self.width() - BORDER_WIDTH, self.height() - BORDER_WIDTH))

    def showEvent(self, event: object) -> None:
        super().showEvent(event)  # type: ignore[arg-type]
        exclude_from_capture(self)
