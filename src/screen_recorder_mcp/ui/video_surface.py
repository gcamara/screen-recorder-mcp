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

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtMultimedia import QVideoFrame, QVideoFrameFormat, QVideoSink
from PySide6.QtWidgets import QSizePolicy, QWidget

from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.ui.theme import RADIUS
from screen_recorder_mcp.ui.theme_controller import ThemeController
from screen_recorder_mcp.ui.widgets_common import qcolor
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


_MIN_CROP_PIXELS = 16  # a smaller drag is a click: it clears the crop instead


def fitted_rect(image_width: int, image_height: int, box_width: int, box_height: int) -> QRectF:
    """Where an image drawn with KeepAspectRatio and centred lands inside a box.

    Example: fitted_rect(1920, 1080, 960, 600) -> QRectF(0, 30, 960, 540)
    """
    if image_width <= 0 or image_height <= 0:
        return QRectF()
    scale = min(box_width / image_width, box_height / image_height)
    width, height = image_width * scale, image_height * scale
    return QRectF((box_width - width) / 2, (box_height - height) / 2, width, height)


def to_video_point(point: QPointF, shown: QRectF, video_size: tuple[int, int]) -> tuple[int, int]:
    """Widget point → video pixel, clamped to the frame.

    Example: to_video_point(QPointF(480, 300), QRectF(0, 30, 960, 540), (1920, 1080)) -> (960, 540)
    """
    width, height = video_size
    x = (point.x() - shown.x()) / shown.width() * width
    y = (point.y() - shown.y()) / shown.height() * height
    return round(min(max(x, 0), width)), round(min(max(y, 0), height))


def rect_between(a: tuple[int, int], b: tuple[int, int]) -> Rect:
    """Normalised rectangle spanned by two corners, in any drag direction.

    Example: rect_between((50, 40), (10, 10)) -> Rect(10, 10, 40, 30)
    """
    return Rect(min(a[0], b[0]), min(a[1], b[1]), abs(a[0] - b[0]), abs(a[1] - b[1]))


def moved_within(rect: Rect, dx: int, dy: int, video_size: tuple[int, int]) -> Rect:
    """Shift a rectangle, stopping at the frame edges instead of shrinking it.

    Example: moved_within(Rect(10, 10, 50, 50), -30, 0, (100, 100)) -> Rect(0, 10, 50, 50)
    """
    x = min(max(rect.x + dx, 0), video_size[0] - rect.width)
    y = min(max(rect.y + dy, 0), video_size[1] - rect.height)
    return Rect(x, y, rect.width, rect.height)


HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
_HANDLE_CURSORS = {
    "nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
    "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
    "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor,
}  # fmt: skip
_HANDLE_GRAB_RADIUS = 12.0  # widget pixels around a handle that pick it up
_HANDLE_SIZE = 10.0


def handle_points(box: QRectF) -> dict[str, QPointF]:
    """Widget positions of the 8 resize handles of a box: corners and edge midpoints."""
    left, right, top, bottom = box.left(), box.right(), box.top(), box.bottom()
    mid_x, mid_y = box.center().x(), box.center().y()
    return {
        "nw": QPointF(left, top), "n": QPointF(mid_x, top), "ne": QPointF(right, top),
        "e": QPointF(right, mid_y), "se": QPointF(right, bottom), "s": QPointF(mid_x, bottom),
        "sw": QPointF(left, bottom), "w": QPointF(left, mid_y),
    }  # fmt: skip


def handle_at(point: QPointF, box: QRectF, radius: float = _HANDLE_GRAB_RADIUS) -> str | None:
    """The handle under a widget point, nearest first; None when the point is on none.

    Example: handle_at(QPointF(3, 2), QRectF(0, 0, 100, 50)) -> "nw"
    """
    best: tuple[float, str] | None = None
    for name, spot in handle_points(box).items():
        distance = max(abs(point.x() - spot.x()), abs(point.y() - spot.y()))
        if distance <= radius and (best is None or distance < best[0]):
            best = (distance, name)
    return None if best is None else best[1]


def resize_by_handle(
    rect: Rect, handle: str, dx: int, dy: int, video_size: tuple[int, int], min_size: int
) -> Rect:
    """Move the edges a handle controls by (dx, dy), keeping the frame bounds and a minimum size.

    Example: resize_by_handle(Rect(0, 0, 100, 80), "se", -40, -30, (100, 80), 16) -> Rect(0, 0, 60, 50)
    """
    left, top, right, bottom = rect.x, rect.y, rect.x + rect.width, rect.y + rect.height
    if "w" in handle:
        left = min(max(left + dx, 0), right - min_size)
    if "e" in handle:
        right = max(min(right + dx, video_size[0]), left + min_size)
    if "n" in handle:
        top = min(max(top + dy, 0), bottom - min_size)
    if "s" in handle:
        bottom = max(min(bottom + dy, video_size[1]), top + min_size)
    return Rect(left, top, right - left, bottom - top)


class VideoSurface(QWidget):
    """Example: player.setVideoSink(surface.sink())

    Crop editing (Trim mode): set_crop_editing(True, (w, h)) shows a frame with 8 handles around
    the whole picture. Dragging a handle crops, dragging inside a crop moves it, dragging outside it
    draws a new one; reset_crop() goes back to the full frame. crop_changed reports the rectangle
    in video pixels, or None for the full frame.
    """

    crop_changed = Signal(object)  # Rect | None

    def __init__(self, theme: ThemeController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._image: QImage | None = None
        self._sink = QVideoSink(self)
        self._sink.videoFrameChanged.connect(self._on_frame)
        self._converter = FrameConverter(self)
        self._converter.image_ready.connect(self._show_image)
        self._crop_editing = False
        self._video_size = (0, 0)
        self._crop: Rect | None = None
        self._drag_anchor: tuple[int, int] | None = None
        self._drag_from: Rect | None = None  # the rectangle as it was when the drag began
        self._drag_kind = ""  # handle name, "move" or "draw"
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(220)
        self.setMouseTracking(True)  # cursor hints over the handles

    # --- crop editing ------------------------------------------------------
    def set_crop_editing(self, enabled: bool, video_size: tuple[int, int] = (0, 0)) -> None:
        self._crop_editing = enabled and video_size[0] > 0 and video_size[1] > 0
        self._video_size = video_size
        self._drag_anchor = self._drag_from = None
        self.unsetCursor()
        self.set_crop(None)

    def set_crop(self, crop: Rect | None) -> None:
        if crop == self._full_frame():
            crop = None  # dragging every edge back out means "no crop"
        self._crop = crop
        self.crop_changed.emit(crop)
        self.update()

    def reset_crop(self) -> None:
        self.set_crop(None)

    def crop(self) -> Rect | None:
        return self._crop

    def _full_frame(self) -> Rect:
        return Rect(0, 0, *self._video_size)

    def _current_box(self) -> Rect:
        """The rectangle the frame and handles are drawn around: the crop, or the whole picture."""
        return self._crop if self._crop is not None else self._full_frame()

    def _shown_rect(self) -> QRectF:
        return fitted_rect(*self._video_size, self.width(), self.height())

    def _widget_box(self, rect: Rect) -> QRectF:
        shown = self._shown_rect()
        scale_x, scale_y = shown.width() / self._video_size[0], shown.height() / self._video_size[1]
        return QRectF(shown.x() + rect.x * scale_x, shown.y() + rect.y * scale_y,
                      rect.width * scale_x, rect.height * scale_y)  # fmt: skip

    def _video_point(self, event: QMouseEvent) -> tuple[int, int]:
        return to_video_point(event.position(), self._shown_rect(), self._video_size)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self._crop_editing or event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        box = self._current_box()
        handle = handle_at(event.position(), self._widget_box(box))
        point = self._video_point(event)
        self._drag_anchor, self._drag_from = point, box
        if handle is not None:
            self._drag_kind = handle
        elif self._crop is not None and _contains(self._crop, point):
            self._drag_kind = "move"
        else:
            self._drag_kind = "draw"

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_anchor is None or self._drag_from is None:
            self._update_hover_cursor(event)
            super().mouseMoveEvent(event)
            return
        point = self._video_point(event)
        dx, dy = point[0] - self._drag_anchor[0], point[1] - self._drag_anchor[1]
        if self._drag_kind == "move":
            self._crop = moved_within(self._drag_from, dx, dy, self._video_size)
        elif self._drag_kind == "draw":
            self._crop = rect_between(self._drag_anchor, point)
        else:
            self._crop = resize_by_handle(self._drag_from, self._drag_kind, dx, dy, self._video_size, _MIN_CROP_PIXELS)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_anchor is None:
            super().mouseReleaseEvent(event)
            return
        kind, before = self._drag_kind, self._drag_from
        self._drag_anchor = self._drag_from = None
        crop = self._crop
        if kind == "draw" and (crop is None or min(crop.width, crop.height) < _MIN_CROP_PIXELS):
            crop = None if before == self._full_frame() else before  # a click is not a new crop
        self.set_crop(crop)

    def _update_hover_cursor(self, event: QMouseEvent) -> None:
        if not self._crop_editing:
            return
        handle = handle_at(event.position(), self._widget_box(self._current_box()))
        if handle is not None:
            self.setCursor(_HANDLE_CURSORS[handle])
        elif self._crop is not None and _contains(self._crop, self._video_point(event)):
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)

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
        if self._crop_editing:
            self._paint_crop(painter)

    def _paint_crop(self, painter: QPainter) -> None:
        """Frame + handles always (so cropping is discoverable); dim the outside only once cropped."""
        palette = self._theme.palette
        hole = self._widget_box(self._current_box())
        if self._crop is not None:
            smoke = QPainterPath()
            smoke.addRect(self._shown_rect())
            smoke.addRect(hole)  # odd-even fill: everything outside the hole is dimmed
            painter.fillPath(smoke, qcolor(palette.scrim))
        frame = hole.adjusted(1, 1, -1, -1)  # keep the 2 px pen inside the clip at full frame
        painter.setPen(QPen(QColor(palette.accent), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(frame)
        painter.setBrush(QColor(palette.accent))
        painter.setPen(QPen(QColor(palette.bg), 1))
        half = _HANDLE_SIZE / 2
        for spot in handle_points(frame).values():
            painter.drawRect(QRectF(spot.x() - half, spot.y() - half, _HANDLE_SIZE, _HANDLE_SIZE))
        if self._crop is not None:
            painter.setPen(QColor(palette.accent_strong))
            painter.drawText(hole.adjusted(10, 8, 0, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                             f"{self._crop.width} x {self._crop.height}")  # fmt: skip


def _contains(rect: Rect, point: tuple[int, int]) -> bool:
    return rect.x <= point[0] <= rect.x + rect.width and rect.y <= point[1] <= rect.y + rect.height
