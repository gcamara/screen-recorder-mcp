"""Player volume: a speaker button whose slider slides out on hover; the wheel over it changes the level.

The level is remembered between runs (QSettings). It starts at DEFAULT_LEVEL rather than 100%,
because a screen recording with system audio played at full volume is startlingly loud.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QEnterEvent, QWheelEvent
from PySide6.QtMultimedia import QtAudio
from PySide6.QtWidgets import QHBoxLayout, QSlider, QWidget

from screen_recorder_mcp.ui.theme_controller import ThemeController
from screen_recorder_mcp.ui.widgets_common import IconButton

DEFAULT_LEVEL = 0.35
_WHEEL_STEP = 0.05
_HIDE_DELAY_MS = 700

LevelStore = tuple[Callable[[], float | None], Callable[[float], None]]  # load, save


def qsettings_store() -> LevelStore:
    settings = QSettings("screen-recorder-mcp", "player")

    def load() -> float | None:
        raw = settings.value("volume")
        try:
            return None if raw is None else float(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    return load, lambda level: settings.setValue("volume", level)


def output_volume(level: float) -> float:
    """Slider position (perceived loudness) → QAudioOutput volume (linear amplitude).

    Example: output_volume(0.5) < 0.5  # half the slider sounds about half as loud
    """
    return QtAudio.convertVolume(level, QtAudio.VolumeScale.LogarithmicVolumeScale,
                                 QtAudio.VolumeScale.LinearVolumeScale)  # fmt: skip


class VolumeControl(QWidget):
    """Example: control = VolumeControl(theme); control.changed.connect(lambda v, m: audio.setVolume(v))"""

    changed = Signal(float, bool)  # output volume 0..1, muted

    def __init__(self, theme: ThemeController, store: LevelStore | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._load, self._save = store or qsettings_store()
        stored = self._load()
        self._level = DEFAULT_LEVEL if stored is None else min(1.0, max(0.0, stored))
        self._muted = False
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 100)
        self._slider.setFixedWidth(90)
        self._slider.setValue(round(self._level * 100))
        self._slider.setToolTip("Volume")
        self._slider.valueChanged.connect(lambda value: self.set_level(value / 100))
        self._slider.hide()
        self._button = IconButton("volume", "Mute (scroll to change the volume)", theme, tint="text_muted")
        self._button.clicked.connect(self.toggle_mute)
        row.addWidget(self._slider)
        row.addWidget(self._button)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(_HIDE_DELAY_MS)
        self._hide_timer.timeout.connect(self._maybe_hide)
        self._sync_glyph()

    def level(self) -> float:
        return self._level

    def muted(self) -> bool:
        return self._muted

    def slider(self) -> QSlider:
        return self._slider

    def apply(self) -> None:
        """Emit the current state once, so the audio output starts at the remembered level."""
        self.changed.emit(output_volume(self._level), self._muted)

    def set_level(self, level: float) -> None:
        level = round(min(1.0, max(0.0, level)), 2)
        if level == self._level and not (self._muted and level > 0):
            return
        self._level = level
        self._muted = False  # moving the volume un-mutes, like every player does
        if self._slider.value() != round(level * 100):
            self._slider.blockSignals(True)
            self._slider.setValue(round(level * 100))
            self._slider.blockSignals(False)
        self._save(level)
        self._sync_glyph()
        self.apply()

    def toggle_mute(self) -> None:
        self._muted = not self._muted
        self._sync_glyph()
        self.apply()

    def _sync_glyph(self) -> None:
        self._button.set_glyph("volume-off" if self._muted or self._level == 0 else "volume")

    def enterEvent(self, event: QEnterEvent) -> None:
        self._hide_timer.stop()
        self._slider.show()
        super().enterEvent(event)

    def leaveEvent(self, event: object) -> None:
        self._hide_timer.start()
        super().leaveEvent(event)  # type: ignore[arg-type]

    def _maybe_hide(self) -> None:
        if not self._slider.isSliderDown() and not self.underMouse():
            self._slider.hide()

    def wheelEvent(self, event: QWheelEvent) -> None:
        notches = event.angleDelta().y() / 120
        if notches:
            self.set_level(self._level + notches * _WHEEL_STEP)
            event.accept()
