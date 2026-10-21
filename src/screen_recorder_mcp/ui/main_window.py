"""Main window: Snipping Tool-style compact toolbar on top, latest recording / history / settings below."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QMainWindow, QMenu, QPushButton, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from screen_recorder_mcp.service import RecorderService, RecordingInfo
from screen_recorder_mcp.ui.controller import RecordingController
from screen_recorder_mcp.ui.dialogs import Dialogs
from screen_recorder_mcp.ui.history_view import HistoryView
from screen_recorder_mcp.ui.icons import icon, icon_pixmap
from screen_recorder_mcp.ui.playback_view import PlaybackView
from screen_recorder_mcp.ui.qt_bridge import BackgroundRunner
from screen_recorder_mcp.ui.settings_view import SettingsView
from screen_recorder_mcp.ui.theme import Palette
from screen_recorder_mcp.ui.theme_controller import ThemeController
from screen_recorder_mcp.ui.thumbnails import ThumbnailMaker
from screen_recorder_mcp.ui.view_models import DELAY_OPTIONS, MODE_LABELS, CaptureTargetMode
from screen_recorder_mcp.ui.widgets_common import IconButton, role_label

PAGE_HOME, PAGE_HISTORY, PAGE_SETTINGS = 0, 1, 2
_MODE_ICONS = {CaptureTargetMode.RECTANGLE: "rectangle", CaptureTargetMode.WINDOW: "window",
               CaptureTargetMode.FULLSCREEN: "monitor"}


class EmptyState(QWidget):
    """Shown on Home until the first recording exists."""

    new_clicked = Signal()

    def __init__(self, theme: ThemeController, hotkey: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.setSpacing(12)
        self._glyph = QLabel()
        self._glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._theme = theme
        theme.changed.connect(self._retint)
        self._retint(theme.palette)
        title = role_label("Record your screen", "title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = role_label(f"Press New, or {hotkey}, then pick an area, a window or a whole screen.", "muted")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        button = QPushButton("New recording")
        button.setProperty("role", "primary")
        button.clicked.connect(self.new_clicked)
        for widget in (self._glyph, title, hint):
            column.addWidget(widget)
        column.addWidget(button, 0, Qt.AlignmentFlag.AlignCenter)

    def _retint(self, palette: Palette) -> None:
        self._glyph.setPixmap(icon_pixmap("film", palette.accent, 48))


class MainWindow(QMainWindow):
    """Example: MainWindow(service, controller, theme, runner, thumbs, dialogs).show()"""

    new_recording_requested = Signal(object, int)  # CaptureTargetMode, delay seconds
    quit_requested = Signal()

    def __init__(
        self, service: RecorderService, controller: RecordingController, theme: ThemeController,
        runner: BackgroundRunner, thumbnails: ThumbnailMaker, dialogs: Dialogs,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Screen Recorder")
        self.resize(780, 640)
        self._service, self._controller, self._theme = service, controller, theme
        self._quitting = False
        self._build(runner, thumbnails, dialogs)
        controller.recording_finished.connect(self.show_recording)
        controller.history_changed.connect(self._history.refresh)
        controller.error.connect(self.toast)
        self._history.refresh()
        self._show_latest()

    # --- construction ------------------------------------------------------------
    def _build(self, runner: BackgroundRunner, thumbnails: ThumbnailMaker, dialogs: Dialogs) -> None:
        root = QWidget()
        root.setProperty("surface", "bg")
        column = QVBoxLayout(root)
        column.setContentsMargins(16, 12, 16, 12)
        column.setSpacing(12)
        column.addLayout(self._build_toolbar())
        self._pages = QStackedWidget()
        self._playback = PlaybackView(self._service, self._theme, runner, dialogs)
        self._empty = EmptyState(self._theme, self._service.get_settings().hotkey)
        self._empty.new_clicked.connect(self.request_new)
        self._home = QStackedWidget()
        self._home.addWidget(self._empty)
        self._home.addWidget(self._playback)
        self._history = HistoryView(self._service, thumbnails, runner)
        self._settings = SettingsView(self._service, dialogs)
        self._settings.load()
        for page in (self._home, self._history, self._settings):
            self._pages.addWidget(page)
        column.addWidget(self._pages, 1)
        self._toast = role_label("", "muted")
        column.addWidget(self._toast)
        self.setCentralWidget(root)
        self._playback.message.connect(self.toast)
        self._playback.recording_deleted.connect(self._after_delete)
        self._history.open_requested.connect(self.show_recording)
        self._settings.settings_saved.connect(lambda s: self._theme.set_theme(s.theme))

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self._new = QPushButton("New")
        self._new.setProperty("role", "primary")
        self._new.setMinimumWidth(84)
        self._new.clicked.connect(self.request_new)
        self._new.setIcon(icon("plus", self._theme.palette.accent_ink, 16))
        self._mode = self._mode_combo()
        self._delay = QComboBox()
        for label, _seconds in DELAY_OPTIONS:
            self._delay.addItem(label)
        self._delay.setToolTip("Delay before recording starts")
        row.addWidget(self._new)
        row.addWidget(self._mode)
        row.addWidget(self._delay)
        row.addStretch(1)
        self._nav = self._nav_buttons(row)
        row.addWidget(self._more_button())
        return row

    def _mode_combo(self) -> QComboBox:
        combo = QComboBox()
        for mode in CaptureTargetMode:
            combo.addItem(icon(_MODE_ICONS[mode], self._theme.palette.text_muted, 16), MODE_LABELS[mode], mode)
        combo.setToolTip("What to record")
        combo.setIconSize(QSize(16, 16))
        return combo

    def _nav_buttons(self, row: QHBoxLayout) -> dict[int, IconButton]:
        buttons: dict[int, IconButton] = {}
        for page, glyph, tip in ((PAGE_HOME, "home", "Latest recording"), (PAGE_HISTORY, "history", "History"),
                                 (PAGE_SETTINGS, "gear", "Settings")):
            button = IconButton(glyph, tip, self._theme, tint="text_muted")
            button.setCheckable(True)
            button.clicked.connect(lambda _c=False, p=page: self.go_to(p))
            buttons[page] = button
            row.addWidget(button)
        buttons[PAGE_HOME].setChecked(True)
        return buttons

    def _more_button(self) -> QToolButton:
        button = IconButton("dots", "More", self._theme, tint="text_muted")
        menu = QMenu(button)
        for text, page in (("History", PAGE_HISTORY), ("Settings", PAGE_SETTINGS)):
            action = QAction(text, menu)
            action.triggered.connect(lambda _c=False, p=page: self.go_to(p))
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction("Quit", self.quit_requested.emit)
        button.setMenu(menu)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        return button

    # --- behaviour -----------------------------------------------------------------
    def selected_mode(self) -> CaptureTargetMode:
        return self._mode.currentData()

    def selected_delay(self) -> int:
        return DELAY_OPTIONS[self._delay.currentIndex()][1]

    def request_new(self) -> None:
        mode, delay = self.selected_mode(), self.selected_delay()
        self.new_recording_requested.emit(mode, delay)

    def go_to(self, page: int) -> None:
        self._pages.setCurrentIndex(page)
        for index, button in self._nav.items():
            button.setChecked(index == page)
        if page == PAGE_HISTORY:
            self._history.refresh()
        if page == PAGE_SETTINGS:
            self._settings.load()

    def show_recording(self, info: RecordingInfo) -> None:
        self._playback.show_recording(info)
        self._home.setCurrentWidget(self._playback)
        self.go_to(PAGE_HOME)

    def _show_latest(self) -> None:
        latest = self._service.history(1)
        if latest:
            self.show_recording(latest[0])

    def _after_delete(self, _recording_id: str) -> None:
        self._history.refresh()
        self._home.setCurrentWidget(self._empty)
        self._show_latest()
        self.toast("Recording deleted")

    def toast(self, text: str) -> None:
        self._toast.setText(text)
        QTimer.singleShot(5000, lambda: self._toast.setText("") if self._toast.text() == text else None)

    def playback(self) -> PlaybackView:
        return self._playback

    def current_page(self) -> int:
        return self._pages.currentIndex()

    def prepare_quit(self) -> None:
        """Let the next close really close (otherwise closing only hides to the tray)."""
        self._quitting = True
        self._playback.stop_playback()

    def closeEvent(self, event: QCloseEvent) -> None:
        # Closing the window keeps the app (tray + control server) alive; Quit really exits.
        if self._quitting:
            event.accept()
            return
        self.hide()
        event.ignore()
