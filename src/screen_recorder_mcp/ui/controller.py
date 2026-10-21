"""RecordingController: glues selection, the recording pill, the countdown and the service together.

Views never talk to the overlays; they call `begin_selection` and listen to the controller's signals.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.service import (
    RecorderEvent,
    RecorderEventKind,
    RecorderService,
    RecordingInfo,
    RecordingState,
    RecordingStatus,
)
from screen_recorder_mcp.ui.countdown_overlay import CountdownOverlay
from screen_recorder_mcp.ui.file_actions import copy_file_to_clipboard
from screen_recorder_mcp.ui.qt_bridge import BackgroundRunner, ServiceEventBridge
from screen_recorder_mcp.ui.recording_toolbar import RecordingToolbar
from screen_recorder_mcp.ui.selection_session import ScreenInfo, SelectionSession, screens_from_qt
from screen_recorder_mcp.ui.theme_controller import ThemeController
from screen_recorder_mcp.ui.view_models import (
    CaptureTargetMode,
    SelectionResult,
    audio_mode_for,
    build_request,
    toolbar_position,
    toolbar_should_hide,
    toolbar_should_show,
)

_PILL_POLL_MS = 250


class RecordingController(QObject):
    recording_finished = Signal(object)  # RecordingInfo
    history_changed = Signal()
    settings_changed = Signal(object)  # RecorderSettings
    active_changed = Signal(bool)  # a recording is in progress (tray: "Stop recording")
    error = Signal(str)
    notice = Signal(str)  # non-error feedback, e.g. "copied to clipboard"

    def __init__(
        self, service: RecorderService, theme: ThemeController, runner: BackgroundRunner,
        screens: Callable[[], list[ScreenInfo]] = screens_from_qt, parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service, self._theme, self._runner, self._screens = service, theme, runner, screens
        self._settings = service.get_settings()
        self._area: Rect | None = None
        self._screen_rect: Rect | None = None
        self._session: SelectionSession | None = None
        self._pill = RecordingToolbar(theme)
        self._countdown = CountdownOverlay(theme)
        self._poll = QTimer(self)
        self._poll.setInterval(_PILL_POLL_MS)
        self._poll.timeout.connect(self._refresh_pill)
        self._bridge = ServiceEventBridge(service, self)
        self._bridge.event_received.connect(self.handle_event)
        self._wire_pill()

    def _wire_pill(self) -> None:
        self._pill.stop_clicked.connect(lambda: self._call(self._service.stop))
        self._pill.pause_clicked.connect(lambda: self._call(self._service.pause))
        self._pill.resume_clicked.connect(lambda: self._call(self._service.resume))
        self._pill.cancel_clicked.connect(lambda: self._call(self._service.cancel))

    def _call(self, action: Callable[[], object]) -> None:
        self._runner.submit(action, on_error=lambda e: self.error.emit(str(e)))

    # --- starting ----------------------------------------------------------------
    def begin_selection(self, mode: CaptureTargetMode, delay_seconds: int) -> None:
        """Open the overlays; picking something starts a recording with `delay_seconds` of countdown.

        Example: controller.begin_selection(CaptureTargetMode.WINDOW, 3)
        """
        if self._session is not None:
            return
        mode = CaptureTargetMode(mode)  # tray/hotkey/combo may hand over the plain str value
        self._runner.submit(self._service.list_targets, on_result=lambda t: self._open_session(t, mode, delay_seconds),
                            on_error=lambda e: self.error.emit(f"Could not list targets: {e}"))

    def _open_session(self, targets: object, mode: CaptureTargetMode, delay: int) -> None:
        screens = self._screens()
        cursor_screen = 0
        self._session = SelectionSession(self._theme, targets, screens, mode, toolbar_screen=cursor_screen)  # type: ignore[arg-type]
        self._session.selected.connect(lambda result: self._on_selected(result, delay))
        self._session.cancelled.connect(self._drop_session)
        self._session.show()

    def _drop_session(self) -> None:
        self._session = None

    def _on_selected(self, result: SelectionResult, delay: int) -> None:
        self._drop_session()
        self._area, self._screen_rect = result.area, result.screen
        audio = audio_mode_for(self._settings.include_microphone, self._settings.include_system_audio)
        request = build_request(result.target, result.region, self._settings, audio, delay)
        self._runner.submit(lambda: self._service.start(request), on_error=lambda e: self.error.emit(str(e)))

    # --- events ----------------------------------------------------------------
    def handle_event(self, event: RecorderEvent) -> None:
        """Runs on the GUI thread (via ServiceEventBridge). Public so tests can feed events."""
        kind = event.kind
        if kind is RecorderEventKind.SETTINGS_CHANGED:
            self._settings = self._service.get_settings()
            self.settings_changed.emit(self._settings)
        elif kind is RecorderEventKind.HISTORY_CHANGED:
            self.history_changed.emit()
        elif toolbar_should_show(event, self._settings):
            self._show_for(event.status)
        elif toolbar_should_hide(event):
            self._hide_pill(event)
        elif kind in (RecorderEventKind.PAUSED, RecorderEventKind.RESUMED):
            self._pill.apply_status(event.status)

    def _show_for(self, status: RecordingStatus) -> None:
        self._pill.set_audio_mode(status.audio)
        self._pill.apply_status(status)
        self._place_pill(status)
        self._pill.show()
        self._poll.start()
        self.active_changed.emit(True)
        self._update_countdown(status)

    def _update_countdown(self, status: RecordingStatus) -> None:
        if status.state is RecordingState.COUNTDOWN and self._area is not None:
            self._countdown.show_count(status.countdown_remaining, self._area)
            return
        self._countdown.hide()

    def _place_pill(self, status: RecordingStatus) -> None:
        hint = self._pill.sizeHint()
        size = (hint.width(), hint.height())
        if status.started_by == "ai" or self._area is None or self._screen_rect is None:
            # No picked area to avoid: dock bottom-centre, above the taskbar, clear of title bars.
            screen = self._screens()[0].logical
            self._pill.move(screen.x + (screen.width - size[0]) // 2, screen.y + screen.height - size[1] - 72)
            return
        self._pill.move(*toolbar_position(self._area, size, self._screen_rect))

    def _hide_pill(self, event: RecorderEvent) -> None:
        self._poll.stop()
        self._pill.hide()
        self._countdown.hide()
        self._area = None
        self.active_changed.emit(False)
        if event.kind is RecorderEventKind.FAILED:
            self.error.emit(event.message or "Recording failed")
        if event.kind is RecorderEventKind.TARGET_CLOSED:
            self.error.emit("The recorded window was closed")
        if event.info is not None:
            self._finish(event.info)

    def _finish(self, info: RecordingInfo) -> None:
        if self._settings.auto_copy_to_clipboard:
            self._auto_copy(info)
        self.history_changed.emit()
        self.recording_finished.emit(info)

    def _auto_copy(self, info: RecordingInfo) -> None:
        # Snipping Tool behaviour: the finished recording is already on the clipboard.
        try:
            copy_file_to_clipboard(info.path)
        except FileNotFoundError as error:
            self.error.emit(str(error))
            return
        self.notice.emit("Recording copied to clipboard")

    def _refresh_pill(self) -> None:
        self._pill.apply_status(self._service.status())

    # --- accessors for the shell ----------------------------------------------------
    def pill(self) -> RecordingToolbar:
        return self._pill

    def countdown(self) -> CountdownOverlay:
        return self._countdown

    def is_recording(self) -> bool:
        return self._service.status().state is not RecordingState.IDLE

    def stop_recording(self) -> None:
        self._call(self._service.stop)

    def close(self) -> None:
        self._bridge.close()
        self._pill.hide()
        self._countdown.hide()
