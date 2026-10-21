"""Small shared widgets: theme-aware icon buttons, labels with roles, QColor from palette strings."""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QToolButton, QWidget

from screen_recorder_mcp.ui.icons import icon
from screen_recorder_mcp.ui.theme import Palette
from screen_recorder_mcp.ui.theme_controller import ThemeController


def qcolor(value: str) -> QColor:
    """Parse "#rrggbb" or the palette's "rgba(r,g,b,a)" (a in 0..1) strings."""
    if not value.startswith("rgba("):
        return QColor(value)
    red, green, blue, alpha = (part.strip() for part in value[5:-1].split(","))
    return QColor(int(red), int(green), int(blue), round(float(alpha) * 255))


class IconButton(QToolButton):
    """A flat tool button whose glyph re-tints when the theme changes.

    Example: IconButton("gear", "Settings", theme, tint="text_muted")
    """

    def __init__(
        self, name: str, tooltip: str, theme: ThemeController, tint: str = "text",
        size: int = 20, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._name, self._tint, self._size = name, tint, size
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setProperty("flat", True)
        self.setIconSize(QSize(size, size))
        theme.changed.connect(self._retint)
        self._retint(theme.palette)

    def set_glyph(self, name: str, tint: str | None = None) -> None:
        self._name = name
        self._tint = tint or self._tint
        self._retint(self._palette)

    def _retint(self, palette: Palette) -> None:
        self._palette = palette
        self.setIcon(icon(self._name, getattr(palette, self._tint), self._size))


def role_label(text: str, role: str, parent: QWidget | None = None) -> QLabel:
    """QLabel styled by the `role` property in the QSS (muted, eyebrow, title, mono, badge-ai)."""
    label = QLabel(text, parent)
    label.setProperty("role", role)
    return label
