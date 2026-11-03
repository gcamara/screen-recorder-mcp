"""Player volume: remembered level, hover slider, wheel steps, mute."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent

from screen_recorder_mcp.ui.volume_control import DEFAULT_LEVEL, VolumeControl, output_volume


class MemoryStore:
    def __init__(self, level: float | None = None) -> None:
        self.level = level
        self.saved: list[float] = []

    def load(self) -> float | None:
        return self.level

    def save(self, level: float) -> None:
        self.saved.append(level)


def make(theme, level: float | None = None) -> tuple[VolumeControl, MemoryStore, list[tuple[float, bool]]]:  # type: ignore[no-untyped-def]
    store = MemoryStore(level)
    control = VolumeControl(theme, (store.load, store.save))
    seen: list[tuple[float, bool]] = []
    control.changed.connect(lambda volume, muted: seen.append((volume, muted)))
    return control, store, seen


def wheel(control: VolumeControl, notches: int) -> None:
    event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(), QPoint(0, 120 * notches), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)  # fmt: skip
    control.wheelEvent(event)


def test_starts_quiet_or_at_the_remembered_level(qapp, theme) -> None:  # type: ignore[no-untyped-def]
    fresh, _, _ = make(theme)
    assert fresh.level() == DEFAULT_LEVEL and fresh.slider().value() == 35
    remembered, _, seen = make(theme, 0.8)
    remembered.apply()
    assert remembered.level() == 0.8 and seen == [(output_volume(0.8), False)]


def test_slider_and_wheel_change_and_save_the_level(qapp, theme) -> None:  # type: ignore[no-untyped-def]
    control, store, seen = make(theme, 0.5)
    control.slider().setValue(20)
    wheel(control, 2)
    wheel(control, -10)
    assert store.saved == [0.2, 0.3, 0.0] and control.level() == 0.0
    assert seen[-1] == (0.0, False) and control.slider().value() == 0


def test_mute_toggles_and_moving_the_volume_unmutes(qapp, theme) -> None:  # type: ignore[no-untyped-def]
    control, _, seen = make(theme, 0.5)
    control.toggle_mute()
    assert control.muted() and seen[-1] == (output_volume(0.5), True)
    wheel(control, 1)
    assert not control.muted() and control.level() == 0.55


def test_slider_is_hidden_until_hover(qapp, theme) -> None:  # type: ignore[no-untyped-def]
    control, _, _ = make(theme)
    control.show()
    assert not control.slider().isVisible()
    control.enterEvent(None)  # type: ignore[arg-type]
    assert control.slider().isVisible()


def test_perceived_scale_is_quieter_than_linear() -> None:
    assert output_volume(0.0) == 0.0 and output_volume(1.0) == 1.0 and 0 < output_volume(0.5) < 0.5
