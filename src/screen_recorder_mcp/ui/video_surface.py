"""Software video surface: paints QVideoSink frames itself instead of using a native video overlay.

Why not QVideoWidget: it can hand frames to the GPU/native surface, which is wasted effort when VRAM
is busy and also makes widget.grab() (tests, screenshots) come back black.

Why not QVideoFrame.toImage(): it converts on the GPU, and with VRAM exhausted it took 76-94 ms per
frame (playback fell to ~10 fps with half-second stalls). YUV frames are converted with numpy on a
worker thread instead; the GUI thread only copies the planes (~1 ms) and draws.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath
from PySide6.QtMultimedia import QVideoFrame, QVideoFrameFormat, QVideoSink
from PySide6.QtWidgets import QSizePolicy, QWidget

from screen_recorder_mcp.ui.theme import RADIUS
from screen_recorder_mcp.ui.theme_controller import ThemeController
from screen_recorder_mcp.ui.yuv_convert import Plane, output_size, plane_view, yuv420_to_rgb32

_YUV420 = (QVideoFrameFormat.PixelFormat.Format_YUV420P, QVideoFrameFormat.PixelFormat.Format_YV12)


@dataclass(frozen=True)
class YuvPlanes:
    """Owned copies of one frame's planes, safe to hand to another thread."""

    y: Plane
    u: Plane
    v: Plane
    size: tuple[int, int]  # width, height to convert at (display size)


def copy_yuv420_planes(frame: QVideoFrame, target_width: int, target_height: int) -> YuvPlanes | None:
    """Copy a CPU YUV 4:2:0 frame's planes; None for any other format (caller falls back).

    Example: copy_yuv420_planes(frame, 760, 430).size -> (760, 428) for a 1080p frame
    """
    if frame.pixelFormat() not in _YUV420 or not frame.map(QVideoFrame.MapMode.ReadOnly):
        return None
    try:
        width, height = frame.width(), frame.height()
        planes = [
            plane_view(frame.bits(index), frame.bytesPerLine(index), rows, cols).copy()
            for index, rows, cols in ((0, height, width), (1, height // 2, width // 2), (2, height // 2, width // 2))
        ]
    finally:
        frame.unmap()
    y, u, v = planes
    if frame.pixelFormat() == QVideoFrameFormat.PixelFormat.Format_YV12:
        u, v = v, u  # YV12 stores V before U
    return YuvPlanes(y, u, v, output_size(width, height, target_width, target_height))


def planes_to_image(planes: YuvPlanes) -> QImage:
    """Convert on the calling thread; the QImage owns its pixels (copy) so the array can go."""
    pixels = yuv420_to_rgb32(planes.y, planes.u, planes.v, planes.size)
    height, width = pixels.shape[:2]
    return QImage(pixels.data, width, height, width * 4, QImage.Format.Format_RGB32).copy()


class FrameConverter(QObject):
    """One worker thread converting the newest frame; stale frames are dropped, never queued.

    Example: conv = FrameConverter(); conv.image_ready.connect(show); conv.submit(planes)
    """

    image_ready = Signal(QImage)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._pending: YuvPlanes | None = None
        self._busy = False

    def submit(self, planes: YuvPlanes) -> None:
        with self._lock:
            self._pending = planes
            if self._busy:
                return  # the running worker picks up the newest planes when it finishes
            self._busy = True
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self) -> None:
        while True:
            with self._lock:
                planes, self._pending = self._pending, None
                if planes is None:
                    self._busy = False
                    return
            image = planes_to_image(planes)
            try:
                self.image_ready.emit(image)  # queued to the GUI thread
            except RuntimeError:
                return  # the surface was destroyed (app quitting) while converting


class VideoSurface(QWidget):
    """Example: player.setVideoSink(surface.sink())"""

    def __init__(self, theme: ThemeController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._image: QImage | None = None
        self._sink = QVideoSink(self)
        self._sink.videoFrameChanged.connect(self._on_frame)
        self._converter = FrameConverter(self)
        self._converter.image_ready.connect(self._show_image)
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
        ratio = self.devicePixelRatioF()  # convert at physical display pixels, not logical
        planes = copy_yuv420_planes(frame, round(self.width() * ratio), round(self.height() * ratio))
        if planes is None:
            self._show_image(frame.toImage())  # uncommon formats: let Qt convert
            return
        self._converter.submit(planes)

    def _show_image(self, image: QImage) -> None:
        self._image = image
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
