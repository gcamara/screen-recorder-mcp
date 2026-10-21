"""Owns the active palette, loads the bundled fonts and pushes the QSS to the QApplication."""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

from screen_recorder_mcp.ui.icons import icon_pixmap
from screen_recorder_mcp.ui.theme import (
    DARK,
    FONT_DIR,
    FONT_FILES,
    SANS_FAMILY,
    Palette,
    build_qss,
    palette_for,
)


def load_bundled_fonts() -> list[str]:
    """Register Geist / Geist Mono; returns families added (empty -> Segoe UI Variable fallback)."""
    families: list[str] = []
    for name in FONT_FILES:
        font_id = QFontDatabase.addApplicationFont(str(FONT_DIR / name))
        if font_id >= 0:
            families.extend(QFontDatabase.applicationFontFamilies(font_id))
    return sorted(set(families))


def write_glyphs(p: Palette) -> Path:
    """QSS can only reference images by file: render the combo chevron and the check tick to a temp folder."""
    folder = Path(tempfile.gettempdir()) / "screen-recorder-ui-glyphs" / p.name
    folder.mkdir(parents=True, exist_ok=True)
    icon_pixmap("chevron-down", p.text_muted, 14).save(str(folder / "chevron-down.png"))
    icon_pixmap("check", p.accent_ink, 12).save(str(folder / "check.png"))
    return folder


def system_is_dark(app: QApplication) -> bool:
    return app.styleHints().colorScheme() != Qt.ColorScheme.Light


class ThemeController(QObject):
    """Example: theme = ThemeController(app); theme.set_theme("light")  # emits changed(LIGHT)"""

    changed = Signal(object)

    def __init__(self, app: QApplication, name: str = "dark") -> None:
        super().__init__(app)
        self._app = app
        self._palette: Palette = DARK
        self.set_theme(name)

    @property
    def palette(self) -> Palette:
        return self._palette

    def set_theme(self, name: str) -> None:
        self._palette = palette_for(name, system_is_dark(self._app))
        self._app.setStyleSheet(build_qss(self._palette, write_glyphs(self._palette)))
        self._app.setPalette(self._qt_palette(self._palette))
        self._app.setFont(QFont(SANS_FAMILY, 10))
        self.changed.emit(self._palette)

    @staticmethod
    def _qt_palette(p: Palette) -> QPalette:
        qt = QPalette()
        for role, color in (
            (QPalette.ColorRole.Window, p.bg), (QPalette.ColorRole.WindowText, p.text),
            (QPalette.ColorRole.Base, p.surface_raised), (QPalette.ColorRole.Text, p.text),
            (QPalette.ColorRole.Button, p.surface_raised), (QPalette.ColorRole.ButtonText, p.text),
            (QPalette.ColorRole.Highlight, p.accent), (QPalette.ColorRole.HighlightedText, p.accent_ink),
        ):
            qt.setColor(role, QColor(color))
        return qt
