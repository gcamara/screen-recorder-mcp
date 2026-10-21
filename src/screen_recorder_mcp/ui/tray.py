"""System tray icon: New recording, Open, Stop recording (only while active), Quit."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from screen_recorder_mcp.ui.theme import DARK


def brand_icon(size: int = 64) -> QIcon:
    """Violet ring with a magenta record dot: the app and tray icon, drawn so no asset file is needed."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(DARK.bg))
    painter.setPen(QPen(QColor(DARK.accent), size * 0.09))
    margin = size * 0.1
    painter.drawEllipse(QRectF(margin, margin, size - 2 * margin, size - 2 * margin))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(DARK.failure))
    inner = size * 0.3
    painter.drawEllipse(QRectF(inner, inner, size - 2 * inner, size - 2 * inner))
    painter.end()
    return QIcon(pixmap)


class AppTray(QSystemTrayIcon):
    """Example: tray = AppTray(window); tray.new_requested.connect(controller_begin); tray.show()"""

    new_requested = Signal()
    open_requested = Signal()
    stop_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(brand_icon(), parent)
        self.setToolTip("Screen Recorder")
        self._menu = QMenu()
        self._new = self._add("New recording", self.new_requested)
        self._open = self._add("Open", self.open_requested)
        self._stop = self._add("Stop recording", self.stop_requested)
        self._menu.addSeparator()
        self._quit = self._add("Quit", self.quit_requested)
        self._stop.setVisible(False)
        self.setContextMenu(self._menu)
        self.activated.connect(self._on_activated)

    def _add(self, text: str, signal: Signal) -> QAction:
        action = self._menu.addAction(text)
        action.triggered.connect(signal)
        return action

    def add_recent_menu(self, menu: QMenu) -> None:
        """Insert a "Recent recordings" submenu right after "Open"."""
        self._menu.insertMenu(self._stop, menu)

    def set_recording(self, active: bool) -> None:
        self._stop.setVisible(active)
        self._new.setEnabled(not active)
        self.setToolTip("Screen Recorder - recording" if active else "Screen Recorder")

    def menu_labels(self) -> list[str]:
        """Visible, enabled-or-not menu entries in order (for tests)."""
        return [a.text() for a in self._menu.actions() if a.isVisible() and not a.isSeparator()]

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.open_requested.emit()
