"""Playback view: player, scrubber, trim mode and the recording actions (PlaybackView / TrimView)."""

from __future__ import annotations

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QSlider, QToolButton, QVBoxLayout, QWidget

from screen_recorder_mcp.service import RecorderService, RecordingInfo
from screen_recorder_mcp.ui.clipboard_actions import ClipboardActions
from screen_recorder_mcp.ui.dialogs import Dialogs
from screen_recorder_mcp.ui.file_actions import reveal_in_folder, save_copy
from screen_recorder_mcp.ui.formatting import format_clock, format_clock_tenths
from screen_recorder_mcp.ui.gif_dialog import GifExportDialog
from screen_recorder_mcp.ui.icons import icon
from screen_recorder_mcp.ui.qt_bridge import BackgroundRunner
from screen_recorder_mcp.ui.theme_controller import ThemeController
from screen_recorder_mcp.ui.trim_bar import TrimBar
from screen_recorder_mcp.ui.video_surface import VideoSurface
from screen_recorder_mcp.ui.view_models import TrimModel
from screen_recorder_mcp.ui.widgets_common import IconButton, role_label


class PlaybackView(QWidget):
    """Example: view = PlaybackView(service, theme, runner, dialogs); view.show_recording(info)"""

    message = Signal(str)
    recording_deleted = Signal(str)

    def __init__(
        self, service: RecorderService, theme: ThemeController, runner: BackgroundRunner,
        dialogs: Dialogs, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service, self._theme, self._runner, self._dialogs = service, theme, runner, dialogs
        self._info: RecordingInfo | None = None
        self._trim: TrimModel | None = None
        self.clipboard = ClipboardActions(service, runner, self)
        self.clipboard.message.connect(self.message)
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._build()
        self._wire_player()

    # --- construction ----------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addLayout(self._build_header())
        self._video = VideoSurface(self._theme)
        self._player.setVideoSink(self._video.sink())
        layout.addWidget(self._video, 1)
        layout.addLayout(self._build_transport())
        layout.addWidget(self._build_trim_row())
        layout.addLayout(self._build_actions())

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._title = role_label("", "title")
        self._badge = role_label("AI", "badge-ai")
        self._meta = role_label("", "muted")
        row.addWidget(self._title)
        row.addWidget(self._badge)
        row.addStretch(1)
        row.addWidget(self._meta)
        return row

    def _build_transport(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self._play = IconButton("play", "Play / pause", self._theme)
        self._time = role_label("00:00 / 00:00", "mono")
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 0)
        self._mute = IconButton("volume", "Mute", self._theme, tint="text_muted")
        for widget in (self._play, self._time):
            row.addWidget(widget)
        row.addWidget(self._slider, 1)
        row.addWidget(self._mute)
        self._play.clicked.connect(self.toggle_play)
        self._mute.clicked.connect(self._toggle_mute)
        self._slider.sliderMoved.connect(self._player.setPosition)
        self._transport_widgets = [self._play, self._time, self._slider, self._mute]
        return row

    def _build_trim_row(self) -> QWidget:
        self._trim_row = QWidget()
        column = QVBoxLayout(self._trim_row)
        column.setContentsMargins(0, 0, 0, 0)
        self._trim_bar = TrimBar(self._theme)
        self._trim_label = role_label("", "mono")
        buttons = QHBoxLayout()
        cancel, save = QPushButton("Cancel"), QPushButton("Save trimmed copy")
        save.setProperty("role", "primary")
        cancel.clicked.connect(self.exit_trim)
        save.clicked.connect(self._save_trimmed)
        buttons.addWidget(self._trim_label)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        column.addWidget(self._trim_bar)
        column.addLayout(buttons)
        self._trim_bar.trim_changed.connect(self._on_trim_changed)
        self._trim_bar.seek_requested.connect(lambda s: self._player.setPosition(int(s * 1000)))
        self._trim_row.hide()
        return self._trim_row

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        spec = [("scissors", "Trim", self.enter_trim), ("gif", "Export GIF", self._open_gif),
                ("save", "Save as...", self._save_as), ("copy", "Copy", self._copy),
                ("folder", "Open folder", self._open_folder)]
        self._action_buttons: list[QPushButton] = []
        for glyph, text, slot in spec:
            row.addWidget(self._action_button(glyph, text, slot))
            if text == "Copy":
                row.addWidget(self._copy_options_button())
        row.addStretch(1)
        delete = self._action_button("trash", "Delete", self._delete)
        delete.setProperty("role", "danger")
        row.addWidget(delete)
        return row

    def _copy_options_button(self) -> IconButton:
        # Split-button: "Copy" copies the video in one click, the arrow offers GIF / path.
        button = IconButton("chevron-down", "More copy options", self._theme, tint="text_muted", size=16)
        button.setProperty("flat", False)
        button.setMenu(self.clipboard.build_menu(self._theme, lambda: self._info, button))
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._copy_options = button
        return button

    def _action_button(self, glyph: str, text: str, slot: object) -> QPushButton:
        button = QPushButton(text)
        button.setIcon(icon(glyph, self._theme.palette.text_muted, 16))
        button.clicked.connect(slot)  # type: ignore[arg-type]
        self._action_buttons.append(button)
        return button

    def _wire_player(self) -> None:
        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(lambda ms: self._slider.setRange(0, ms))
        self._player.playbackStateChanged.connect(self._sync_play_icon)

    # --- showing a recording -----------------------------------------------
    def show_recording(self, info: RecordingInfo) -> None:
        self.exit_trim()
        self._info = info
        self._title.setText(info.target_label)
        self._badge.setVisible(info.started_by == "ai")
        self._meta.setText(f"{format_clock(info.duration_seconds)}  -  {info.width} x {info.height}")
        self._video.clear()
        self._player.setSource(QUrl.fromLocalFile(str(info.path)))
        self._player.pause()
        self._refresh_time(0)

    def video(self) -> VideoSurface:
        return self._video

    def current(self) -> RecordingInfo | None:
        return self._info

    def stop_playback(self) -> None:
        self._player.stop()
        self._player.setSource(QUrl())

    # --- transport ---------------------------------------------------------
    def toggle_play(self) -> None:
        playing = self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self._player.pause() if playing else self._player.play()

    def _toggle_mute(self) -> None:
        muted = not self._audio.isMuted()
        self._audio.setMuted(muted)
        self._mute.set_glyph("volume-off" if muted else "volume")

    def _sync_play_icon(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._play.set_glyph("pause" if playing else "play")

    def _on_position(self, ms: int) -> None:
        if not self._slider.isSliderDown():
            self._slider.setValue(ms)
        self._refresh_time(ms)
        if self._trim is not None:
            self._trim_bar.set_playhead(ms / 1000)
            self._loop_inside_trim(ms)

    def _loop_inside_trim(self, ms: int) -> None:
        assert self._trim is not None
        if ms / 1000 >= self._trim.end:
            self._player.pause()
            self._player.setPosition(int(self._trim.start * 1000))

    def _refresh_time(self, ms: int) -> None:
        total = self._info.duration_seconds if self._info else 0.0
        self._time.setText(f"{format_clock(ms / 1000)} / {format_clock(total)}")

    # --- trim mode ---------------------------------------------------------
    def enter_trim(self) -> None:
        if self._info is None:
            return
        self._trim = TrimModel(duration=self._info.duration_seconds)
        self._trim_bar.set_model(self._trim)
        self._on_trim_changed(self._trim.start, self._trim.end)
        for widget in self._transport_widgets:
            widget.hide()
        self._trim_row.show()
        self._player.pause()

    def exit_trim(self) -> None:
        self._trim = None
        self._trim_row.hide()
        for widget in self._transport_widgets:
            widget.show()

    def _on_trim_changed(self, start: float, end: float) -> None:
        self._trim_label.setText(
            f"{format_clock_tenths(start)}  to  {format_clock_tenths(end)}   ({end - start:.1f} s kept)"
        )

    def _save_trimmed(self) -> None:
        if self._info is None or self._trim is None:
            return
        info, trim = self._info, self._trim
        self._runner.submit(
            lambda: self._service.trim(info.recording_id, trim.start, trim.end),
            on_result=lambda new: self.message.emit(f"Saved trimmed copy ({format_clock(new.duration_seconds)})"),
            on_error=lambda error: self.message.emit(f"Trim failed: {error}"),
        )
        self.exit_trim()

    # --- actions -----------------------------------------------------------
    def _open_gif(self) -> None:
        if self._info is None:
            return
        chooser = lambda name: self._dialogs.save_path(name, self._info.path.parent)  # type: ignore[union-attr]  # noqa: E731
        dialog = GifExportDialog(self._service, self._info, self._theme, self._runner, chooser, self)
        if dialog.exec() and dialog.exported_path:
            self.message.emit(f"GIF ready: {dialog.exported_path.name}")

    def _save_as(self) -> None:
        if self._info is None:
            return
        target = self._dialogs.save_path(self._info.path.name, self._info.path.parent)
        if target is None:
            return
        self._guard(lambda: save_copy(self._info.path, target), f"Saved {target.name}")  # type: ignore[union-attr]

    def _copy(self) -> None:
        if self._info is not None:
            self.clipboard.copy_video(self._info)

    def _open_folder(self) -> None:
        if self._info is not None:
            self._guard(lambda: reveal_in_folder(self._info.path), "")  # type: ignore[union-attr]

    def _delete(self) -> None:
        if self._info is None or not self._dialogs.confirm("Delete recording", "Delete this recording permanently?"):
            return
        info = self._info
        self.stop_playback()
        self._runner.submit(
            lambda: self._service.delete_recording(info.recording_id),
            on_result=lambda _none: self.recording_deleted.emit(info.recording_id),
            on_error=lambda error: self.message.emit(f"Delete failed: {error}"),
        )

    def _guard(self, action: object, success: str) -> None:
        try:
            action()  # type: ignore[operator]
        except (OSError, FileNotFoundError) as error:
            self.message.emit(str(error))
            return
        if success:
            self.message.emit(success)
