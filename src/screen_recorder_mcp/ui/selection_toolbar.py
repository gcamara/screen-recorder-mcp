"""Top-centre floating toolbar of the selection overlay: mode switcher, input feedback, Cancel."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QMenu, QToolButton, QWidget

from screen_recorder_mcp.input_overlay import KeyMode, OverlayOptions
from screen_recorder_mcp.ui.icons import icon
from screen_recorder_mcp.ui.os_integration import exclude_from_capture
from screen_recorder_mcp.ui.theme import Palette
from screen_recorder_mcp.ui.theme_controller import ThemeController
from screen_recorder_mcp.ui.view_models import MODE_LABELS, CaptureTargetMode

KEY_MENU = {KeyMode.OFF: "Off", KeyMode.SHORTCUTS: "Shortcuts only", KeyMode.ALL: "All keys (typed text too)"}
KEY_BUTTON = {KeyMode.OFF: "Keys", KeyMode.SHORTCUTS: "Keys: shortcuts", KeyMode.ALL: "Keys: all"}

_MODE_ICONS = {
    CaptureTargetMode.RECTANGLE: "rectangle",
    CaptureTargetMode.WINDOW: "window",
    CaptureTargetMode.FULLSCREEN: "monitor",
}


class SelectionToolbar(QWidget):
    """Example: bar = SelectionToolbar(theme); bar.mode_selected.connect(session.set_mode)

    Mouse toggles the cursor halo and click ripples together; Keys picks the keystroke mode.
    Both are shown here, at the moment of recording, because that is where people look for them.
    """

    mode_selected = Signal(object)  # CaptureTargetMode
    overlay_changed = Signal(object)  # OverlayOptions
    closed = Signal()

    def __init__(
        self, theme: ThemeController, mode: CaptureTargetMode = CaptureTargetMode.RECTANGLE,
        overlay: OverlayOptions | None = None,
    ) -> None:
        super().__init__()
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._theme = theme
        self._buttons: dict[CaptureTargetMode, QToolButton] = {}
        self._overlay = overlay or OverlayOptions()
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
        row.addWidget(self._divider())
        self._mouse = self._toggle_button("Mouse", "Highlight the pointer and show clicks in the video")
        self._mouse.setChecked(self._overlay.cursor_halo or self._overlay.show_clicks)
        self._mouse.toggled.connect(self._mouse_toggled)
        self._keys = self._toggle_button(KEY_BUTTON[self._overlay.show_keys], "Show pressed keys in the video")
        self._keys.setCheckable(False)
        self._keys.setMenu(self._keys_menu())
        self._keys.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        row.addWidget(self._mouse)
        row.addWidget(self._keys)
        row.addWidget(self._divider())
        cancel = self._toggle_button("Cancel", "Cancel (Esc)")
        cancel.setCheckable(False)
        cancel.setIcon(icon("close", self._theme.palette.text_muted, 16))
        cancel.clicked.connect(self.closed)
        row.addWidget(cancel)
        self.set_mode(mode)
        outer.addWidget(self._pill)

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFixedWidth(1)
        line.setStyleSheet(f"color: {self._theme.palette.border_strong};")
        return line

    def _toggle_button(self, text: str, tip: str) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tip)
        button.setCheckable(True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setIconSize(QSize(16, 16))
        button.setProperty("flat", True)
        return button

    def _keys_menu(self) -> QMenu:
        menu = QMenu(self._keys)
        group = QActionGroup(menu)
        self._key_actions: dict[KeyMode, QAction] = {}
        for mode, label in KEY_MENU.items():
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(mode is self._overlay.show_keys)
            action.triggered.connect(lambda _checked=False, m=mode: self.set_key_mode(m))
            group.addAction(action)
            self._key_actions[mode] = action
        return menu

    def overlay(self) -> OverlayOptions:
        return self._overlay

    def _mouse_toggled(self, on: bool) -> None:
        self._set_overlay(replace(self._overlay, cursor_halo=on, show_clicks=on))

    def set_key_mode(self, mode: KeyMode) -> None:
        self._key_actions[mode].setChecked(True)
        self._keys.setText(KEY_BUTTON[mode])
        self._set_overlay(replace(self._overlay, show_keys=mode))

    def _set_overlay(self, options: OverlayOptions) -> None:
        if options == self._overlay:
            return
        self._overlay = options
        self._restyle(self._theme.palette)
        self.overlay_changed.emit(options)

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
        if hasattr(self, "_mouse"):
            mouse_on = self._overlay.cursor_halo or self._overlay.show_clicks
            keys_on = self._overlay.show_keys is not KeyMode.OFF
            self._mouse.setIcon(icon("pointer", palette.accent_strong if mouse_on else palette.text_muted, 16))
            self._keys.setIcon(icon("keyboard", palette.accent_strong if keys_on else palette.text_muted, 16))
        self._pill.setStyleSheet(
            f'QFrame[role="pill"] {{ background: {palette.surface_overlay}; border: 1px solid {palette.border_strong};'
            f" border-radius: 22px; }}"
        )

    def showEvent(self, event: object) -> None:
        super().showEvent(event)  # type: ignore[arg-type]
        exclude_from_capture(self)

