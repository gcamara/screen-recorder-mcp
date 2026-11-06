"""Scrubber for the player: a click jumps to that spot, dragging seeks (throttled), the last position always lands."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider, QWidget

_DRAG_SEEK_MS = 40  # a seek decodes from the previous keyframe; one per mouse move would pile up


def value_at(position: float, span_start: float, span: float, minimum: int, maximum: int) -> int:
    """Slider value for a pixel position inside [span_start, span_start + span].

    Example: value_at(150, 0, 300, 0, 20_000) -> 10000
    """
    return QStyle.sliderValueFromPosition(minimum, maximum, round(position - span_start), max(1, round(span)))


class SeekSlider(QSlider):
    """Example: slider.seek_requested.connect(player.setPosition); slider.setRange(0, duration_ms)"""

    seek_requested = Signal(int)  # milliseconds

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._pending: int | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_DRAG_SEEK_MS)
        self._timer.timeout.connect(self._flush)
        self.sliderMoved.connect(self._queue)
        self.sliderReleased.connect(self._release)
        self.actionTriggered.connect(self._keyboard)

    def set_duration(self, milliseconds: int) -> None:
        self.setRange(0, milliseconds)
        self.setSingleStep(1000)
        self.setPageStep(5000)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > 0:
            self.setSliderPosition(self._value_under(event.position().x()))
            self._queue(self.sliderPosition())  # a plain click seeks even if it never turns into a drag
        super().mousePressEvent(event)  # the handle now sits under the cursor, so a drag can follow

    def _value_under(self, x: float) -> int:
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        style = self.style()
        groove = style.subControlRect(QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderGroove, self)
        handle = style.subControlRect(QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, self)
        half = handle.width() / 2
        return value_at(x - half, groove.x(), groove.width() - handle.width(), self.minimum(), self.maximum())

    def _queue(self, value: int) -> None:
        self._pending = value
        if not self._timer.isActive():
            self._timer.start()

    def _flush(self) -> None:
        if self._pending is not None:
            value, self._pending = self._pending, None
            self.seek_requested.emit(value)

    def _release(self) -> None:
        self._timer.stop()
        self._pending = self.sliderPosition()  # whatever the drag last showed is where playback continues
        self._flush()

    def _keyboard(self, _action: int) -> None:
        if not self.isSliderDown():
            QTimer.singleShot(0, lambda: self.seek_requested.emit(self.sliderPosition()))
