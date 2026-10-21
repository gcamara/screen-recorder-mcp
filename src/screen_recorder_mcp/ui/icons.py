"""Project-owned icon wrapper: stroke SVGs drawn on a 24px grid, tinted at render time.

Phosphor-style outlines kept inline so the icon library can be swapped behind `icon()`.
Example: button.setIcon(icon("pause", palette.text))
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# Inner SVG for each name; the wrapper adds stroke colour and round caps.
_PATHS: dict[str, str] = {
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8"/>',
    "dots": '<circle cx="5.5" cy="12" r="1.2" fill="currentColor"/><circle cx="12" cy="12" r="1.2" fill="currentColor"/><circle cx="18.5" cy="12" r="1.2" fill="currentColor"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "pause": '<path d="M9 5v14M15 5v14"/>',
    "play": '<path d="M8 5.5v13l10.5-6.5z" fill="currentColor"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-12M9 7V4h6v3"/>',
    "mic": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/>',
    "mic-off": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M4 4l16 16"/>',
    "speaker": '<path d="M4 10v4h4l5 4V6L8 10z"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/>',
    "speaker-off": '<path d="M4 10v4h4l5 4V6L8 10z"/><path d="M17 9.5l4 5M21 9.5l-4 5"/>',
    "rectangle": '<path d="M4 8V5h3M17 5h3v3M20 16v3h-3M7 19H4v-3"/>',
    "window": '<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M3.5 9h17"/>',
    "monitor": '<rect x="3" y="4.5" width="18" height="12" rx="2"/><path d="M9 20h6M12 16.5V20"/>',
    "folder": '<path d="M3.5 7a1.5 1.5 0 0 1 1.5-1.5h4l2 2.5h8A1.5 1.5 0 0 1 20.5 9.5v8A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5z"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
    "save": '<path d="M5 4h11l3 3v12a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1z"/><path d="M8 4v5h7V4M8 20v-6h8v6"/>',
    "scissors": '<circle cx="6" cy="7" r="2.5"/><circle cx="6" cy="17" r="2.5"/><path d="M8 8.5L20 18M8 15.5L20 6"/>',
    "gif": '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M10 10.5H8.5a1.5 1.5 0 0 0 0 3H10v-1.2M13.5 10v4M16 14v-4h2.5"/>',
    "history": '<path d="M4 12a8 8 0 1 0 2.5-5.8M4 5v4h4M12 8v4l3 2"/>',
    "home": '<path d="M4 11l8-6.5L20 11v8a1 1 0 0 1-1 1h-4v-5H9v5H5a1 1 0 0 1-1-1z"/>',
    "volume": '<path d="M4 10v4h4l5 4V6L8 10z"/>',
    "volume-off": '<path d="M4 10v4h4l5 4V6L8 10z"/><path d="M16.5 9.5l4 5M20.5 9.5l-4 5"/>',
    "back": '<path d="M15 5l-7 7 7 7"/>',
    "chevron-down": '<path d="M6 9.5l6 6 6-6"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "film": '<rect x="4" y="4" width="16" height="16" rx="2.5"/><path d="M9 4v16M15 4v16M4 9h5M15 9h5M4 15h5M15 15h5"/>',
}


def icon_names() -> list[str]:
    return sorted(_PATHS)


def _svg_for(name: str, color: str) -> bytes:
    if name not in _PATHS:
        raise KeyError(f"icon {name!r} is not one of {icon_names()}")
    body = _PATHS[name].replace("currentColor", color)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )
    return svg.encode()


def icon_pixmap(name: str, color: str, size: int = 20, ratio: float = 2.0) -> QPixmap:
    """Render one icon crisply at `size` logical px (device pixel ratio `ratio`).

    Example: label.setPixmap(icon_pixmap("mic", "#ecebf2", 16))
    """
    renderer = QSvgRenderer(QByteArray(_svg_for(name, color)))
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pixmap


def icon(name: str, color: str, size: int = 20) -> QIcon:
    return QIcon(icon_pixmap(name, color, size))
