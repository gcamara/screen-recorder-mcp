"""The always-on-top recording pill (Snipping Tool's RecordingOverlayToolbar).

Shows Start before recording; a pulsing dot + Geist Mono timer, Pause/Resume, Stop and Cancel while
recording; mic and system-audio toggles; an "AI" badge when the assistant started the recording.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QVariantAnimation, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QWidget

from screen_recorder_mcp.capture import AudioMode
from screen_recorder_mcp.service import RecordingStatus
from screen_recorder_mcp.ui.os_integration import exclude_from_capture
from screen_recorder_mcp.ui.theme import MONO_FAMILY, Palette
from screen_recorder_mcp.ui.theme_controller import ThemeController
from screen_recorder_mcp.ui.view_models import ToolbarView, toggle_audio, toolbar_view_for
from screen_recorder_mcp.ui.widgets_common import IconButton, role_label


class _RecordDot(QWidget):
    """Failure-magenta dot; pulses while recording (a "breathing" live indicator)."""

    def __init__(self, theme: ThemeController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._theme = theme
        self._alpha = 1.0
        self._pulse = QVariantAnimation(self, startValue=1.0, endValue=0.35, duration=900, loopCount=-1)
        self._pulse.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._pulse.valueChanged.connect(self._set_alpha)

    def set_pulsing(self, on: bool) -> None:
        if on and self._pulse.state() != QVariantAnimation.State.Running:
            self._pulse.start()
        if not on:
            self._pulse.stop()
            self._set_alpha(1.0)

    def _set_alpha(self, value: float) -> None:
        self._alpha = float(value)
        self.update()

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self._theme.palette.failure)
        color.setAlphaF(self._alpha)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(self.rect().adjusted(2, 2, -2, -2))


class RecordingToolbar(QWidget):
    start_clicked = Signal()
    pause_clicked = Signal()
    resume_clicked = Signal()
    stop_clicked = Signal()
    cancel_clicked = Signal()
    audio_changed = Signal(object)  # AudioMode
    moved_by_user = Signal(QPoint)  # top-left after a drag, so the next recording reuses it

    def __init__(self, theme: ThemeController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._theme, self._audio = theme, AudioMode.NONE
        self._drag_offset: QPoint | None = None
        self._build()
        self.setCursor(Qt.CursorShape.OpenHandCursor)  # buttons keep their own arrow cursor
        self.setToolTip("Drag to move")
        theme.changed.connect(self._restyle)
        self.apply_status(None)

    def _build(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        self._pill = QFrame(self)
        self._pill.setProperty("role", "pill")
        row = QHBoxLayout(self._pill)
        row.setContentsMargins(14, 6, 10, 6)
        row.setSpacing(6)
        self._dot = _RecordDot(self._theme)
        self._timer = role_label("00:00", "mono")
        self._timer.setStyleSheet(f'font-family: "{MONO_FAMILY}"; font-size: 16px; font-weight: 500;')
        self._ai = role_label("AI", "badge-ai")
        row.addWidget(self._dot)
        row.addWidget(self._timer)
        row.addSpacing(4)
        row.addWidget(self._ai)
        row.addSpacing(6)
        self._add_actions(row)
        outer.addWidget(self._pill)

    def _add_actions(self, row: QHBoxLayout) -> None:
        self._start = QPushButton("Start")
        self._start.setProperty("role", "primary")
        self._start.clicked.connect(self.start_clicked)
        self._pause = IconButton("pause", "Pause", self._theme)
        self._resume = IconButton("play", "Resume", self._theme)
        self._stop = IconButton("stop", "Stop and save", self._theme, tint="failure")
        self._cancel = IconButton("trash", "Cancel and delete", self._theme, tint="text_muted")
        self._mic = IconButton("mic-off", "Microphone", self._theme, tint="text_muted")
        self._system = IconButton("speaker-off", "System audio", self._theme, tint="text_muted")
        for button, signal in ((self._pause, self.pause_clicked), (self._resume, self.resume_clicked),
                               (self._stop, self.stop_clicked), (self._cancel, self.cancel_clicked)):
            button.clicked.connect(signal)
        self._mic.clicked.connect(lambda: self._toggle(AudioMode.MIC))
        self._system.clicked.connect(lambda: self._toggle(AudioMode.SYSTEM))
        for widget in (self._start, self._pause, self._resume, self._stop, self._cancel, self._mic, self._system):
            widget.setCursor(Qt.CursorShape.PointingHandCursor)  # the pill itself shows a grab hand
            row.addWidget(widget)

    def audio_mode(self) -> AudioMode:
        return self._audio

    def set_audio_mode(self, mode: AudioMode) -> None:
        self._audio = mode
        self._mic.set_glyph("mic" if mode is AudioMode.MIC else "mic-off", "accent" if mode is AudioMode.MIC else "text_muted")
        self._system.set_glyph("speaker" if mode is AudioMode.SYSTEM else "speaker-off", "accent" if mode is AudioMode.SYSTEM else "text_muted")

    def _toggle(self, which: AudioMode) -> None:
        self.set_audio_mode(toggle_audio(self._audio, which))
        self.audio_changed.emit(self._audio)

    def apply_status(self, status: RecordingStatus | None) -> None:
        """Re-layout the pill for a status (None = ready to start)."""
        view = toolbar_view_for(status)
        self._view = view
        self._timer.setText(self._timer_text(view, status))
        self._dot.setVisible(view.show_dot)
        self._dot.set_pulsing(view.dot_pulses)
        for widget, visible in ((self._start, view.show_start), (self._pause, view.show_pause),
                                (self._resume, view.show_resume), (self._stop, view.show_stop),
                                (self._cancel, view.show_cancel), (self._ai, view.ai_badge)):
            widget.setVisible(visible)
        for button in (self._mic, self._system):
            button.setEnabled(view.audio_editable)
        self._restyle(self._theme.palette)
        self.adjustSize()

    @staticmethod
    def _timer_text(view: ToolbarView, status: RecordingStatus | None) -> str:
        if view.phase == "countdown" and status is not None:
            return f"Starting in {status.countdown_remaining}"
        return "Saving..." if view.phase == "finalizing" else view.timer_text

    def _restyle(self, palette: Palette) -> None:
        live = getattr(self, "_view", None) is not None and self._view.phase in ("recording", "paused")
        border = palette.accent if live else palette.border_strong
        self._pill.setStyleSheet(
            f'QFrame[role="pill"] {{ background: {palette.surface_overlay}; border: 1px solid {border}; border-radius: 22px; }}'
        )

    # --- dragging: press on the pill's background (buttons consume their own clicks) ---
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._drag_offset = event.globalPosition().toPoint() - self.pos()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:
        if self._drag_offset is None:
            return
        self._drag_offset = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.moved_by_user.emit(self.pos())

    def showEvent(self, event: object) -> None:
        super().showEvent(event)  # type: ignore[arg-type]
        exclude_from_capture(self)
