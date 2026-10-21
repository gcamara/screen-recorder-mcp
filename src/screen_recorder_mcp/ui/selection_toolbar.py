"""Top-centre floating toolbar of the selection overlay: mode switcher + close (RecordingSelectionView)."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QToolButton, QWidget

from screen_recorder_mcp.ui.icons import icon
from screen_recorder_mcp.ui.os_integration import exclude_from_capture
from screen_recorder_mcp.ui.theme import Palette
from screen_recorder_mcp.ui.theme_controller import ThemeController
from screen_recorder_mcp.ui.view_models import MODE_LABELS, CaptureTargetMode
from screen_recorder_mcp.ui.widgets_common import IconButton

_MODE_ICONS = {
    CaptureTargetMode.RECTANGLE: "rectangle",
    CaptureTargetMode.WINDOW: "window",
    CaptureTargetMode.FULLSCREEN: "monitor",
}


class SelectionToolbar(QWidget):
    """Example: bar = SelectionToolbar(theme); bar.mode_selected.connect(session.set_mode)"""

    mode_selected = Signal(object)  # CaptureTargetMode
    closed = Signal()

    def __init__(self, theme: ThemeController, mode: CaptureTargetMode = CaptureTargetMode.RECTANGLE) -> None:
        super().__init__()
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._theme = theme
        self._buttons: dict[CaptureTargetMode, QToolButton] = {}
        self._build(mode)
        theme.changed.connect(self._restyle)
        self._restyle(theme.palette)

    def _build(self, mode: CaptureTargetMode) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        self._pill = QFrame()
        self._pill.setProperty("role", "pill")
        row = QHBoxLayout(self._pill)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(4)
        group = QButtonGroup(self)
        for kind in CaptureTargetMode:
            button = self._mode_button(kind)
            group.addButton(button)
            row.addWidget(button)
        close = IconButton("close", "Close (Esc)", self._theme, tint="text_muted")
        close.clicked.connect(self.closed)
        row.addWidget(close)
        self.set_mode(mode)
        outer.addWidget(self._pill)

    def _mode_button(self, kind: CaptureTargetMode) -> QToolButton:
        button = QToolButton()
        button.setText(MODE_LABELS[kind])
        button.setCheckable(True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setIconSize(QSize(18, 18))
        button.setProperty("flat", True)
        button.setAccessibleName(f"{MODE_LABELS[kind]} mode")
        button.clicked.connect(lambda _checked=False, k=kind: self.mode_selected.emit(k))
        self._buttons[kind] = button
        return button

    def set_mode(self, mode: CaptureTargetMode) -> None:
        for kind, button in self._buttons.items():
            button.setChecked(kind is mode)
        self._restyle(self._theme.palette)

    def _restyle(self, palette: Palette) -> None:
        for kind, button in self._buttons.items():
            color = palette.accent_strong if button.isChecked() else palette.text_muted
            button.setIcon(icon(_MODE_ICONS[kind], color, 18))
        self._pill.setStyleSheet(
            f'QFrame[role="pill"] {{ background: {palette.surface_overlay}; border: 1px solid {palette.border_strong};'
            f" border-radius: 22px; }}"
        )

    def showEvent(self, event: object) -> None:
        super().showEvent(event)  # type: ignore[arg-type]
        exclude_from_capture(self)
