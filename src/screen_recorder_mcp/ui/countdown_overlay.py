"""Big centred number shown during the recording delay (COUNTDOWN events)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.ui.os_integration import exclude_from_capture
from screen_recorder_mcp.ui.theme import MONO_FAMILY
from screen_recorder_mcp.ui.theme_controller import ThemeController

_SIZE = 220


class CountdownOverlay(QWidget):
    """Example: overlay.show_count(3, Rect(0, 0, 1920, 1080))  # centred on that area"""

    def __init__(self, theme: ThemeController) -> None:
        super().__init__()
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.resize(_SIZE, _SIZE)
        self._theme = theme
        self._count = 0

    def show_count(self, count: int, area: Rect) -> None:
        if count <= 0:
            self.hide()
            return
        self._count = count
        self.move(area.x + (area.width - _SIZE) // 2, area.y + (area.height - _SIZE) // 2)
        self.show()
        self.update()

    def current_count(self) -> int:
        return self._count

    def showEvent(self, event: object) -> None:
        super().showEvent(event)  # type: ignore[arg-type]
        exclude_from_capture(self)

    def paintEvent(self, _event: object) -> None:
        palette = self._theme.palette
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        painter.setBrush(QColor(palette.surface_overlay))
        painter.setPen(QPen(QColor(palette.accent), 3))
        painter.drawEllipse(frame)
        painter.setPen(QColor(palette.text))
        painter.setFont(self._font())
        painter.drawText(frame, Qt.AlignmentFlag.AlignCenter, str(self._count))

    @staticmethod
    def _font() -> QFont:
        font = QFont(MONO_FAMILY)
        font.setPixelSize(110)
        font.setWeight(QFont.Weight.Medium)
        return font

