"""Software video surface: paints QVideoSink frames itself instead of using a native video overlay.

Why not QVideoWidget: it can hand frames to the GPU/native surface, which is wasted effort when VRAM
is busy and also makes widget.grab() (tests, screenshots) come back black.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath
from PySide6.QtMultimedia import QVideoFrame, QVideoSink
from PySide6.QtWidgets import QSizePolicy, QWidget

from screen_recorder_mcp.ui.theme import RADIUS
from screen_recorder_mcp.ui.theme_controller import ThemeController


class VideoSurface(QWidget):
    """Example: player.setVideoSink(surface.sink())"""

    def __init__(self, theme: ThemeController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._image: QImage | None = None
        self._sink = QVideoSink(self)
        self._sink.videoFrameChanged.connect(self._on_frame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(220)

    def sink(self) -> QVideoSink:
        return self._sink

    def has_frame(self) -> bool:
        return self._image is not None and not self._image.isNull()

    def clear(self) -> None:
        self._image = None
        self.update()

    def _on_frame(self, frame: QVideoFrame) -> None:
        if not frame.isValid():
            return
        self._image = frame.toImage()
        self.update()

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(self.rect()), RADIUS["lg"], RADIUS["lg"])
        painter.setClipPath(shape)
        painter.fillRect(self.rect(), QColor(self._theme.palette.bg))
        if not self.has_frame():
            return
        scaled = self._image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)  # type: ignore[union-attr]
        x, y = (self.width() - scaled.width()) // 2, (self.height() - scaled.height()) // 2
        painter.drawImage(QRectF(x, y, scaled.width(), scaled.height()), self._image)  # type: ignore[arg-type]
