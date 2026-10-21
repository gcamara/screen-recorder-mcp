"""Settings page (SettingsView): every field maps 1:1 to RecorderSettings via update_settings."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QFrame, QHBoxLayout, QLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from screen_recorder_mcp.frame_sources import CaptureMode
from screen_recorder_mcp.service import RecorderService, RecorderSettings
from screen_recorder_mcp.ui.dialogs import Dialogs
from screen_recorder_mcp.ui.widgets_common import role_label

FPS_CHOICES = (15, 24, 30, 60)
THEME_CHOICES = ("system", "dark", "light")


def settings_changes(old: RecorderSettings, new: RecorderSettings) -> dict[str, object]:
    """Only the fields that differ, as the update_settings payload.

    Example: settings_changes(a, replace(a, default_fps=60)) -> {"default_fps": 60}
    """
    return {
        name: getattr(new, name)
        for name in RecorderSettings.__dataclass_fields__
        if getattr(new, name) != getattr(old, name)
    }


class SettingsView(QWidget):
    """Example: view = SettingsView(service, dialogs); view.load(); view.settings_saved.connect(f)"""

    settings_saved = Signal(object)  # RecorderSettings

    def __init__(self, service: RecorderService, dialogs: Dialogs, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service, self._dialogs = service, dialogs
        self._loaded: RecorderSettings | None = None
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        outer.addWidget(role_label("Settings", "title"))
        outer.addWidget(self._card("RECORDING", self._recording_form()))
        outer.addWidget(self._card("AUDIO", self._audio_form()))
        outer.addWidget(self._card("APP", self._app_form()))
        outer.addStretch(1)

    def _card(self, eyebrow: str, body: QLayout) -> QFrame:
        card = QFrame()
        card.setProperty("role", "card")
        column = QVBoxLayout(card)
        column.setContentsMargins(16, 12, 16, 12)
        column.addWidget(role_label(eyebrow, "eyebrow"))
        column.addLayout(body)
        return card

    def _recording_form(self) -> QFormLayout:
        form = self._form()
        self._folder = QLineEdit()
        self._folder.setReadOnly(True)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self._folder, 1)
        row.addWidget(browse)
        self._fps = self._combo([str(f) for f in FPS_CHOICES])
        self._capture = self._combo([m.value for m in CaptureMode])
        self._cursor = self._check("Capture mouse cursor")
        form.addRow("Output folder", row)
        form.addRow("Frame rate", self._fps)
        form.addRow("Capture mode", self._capture)
        form.addRow("", self._cursor)
        return form

    def _audio_form(self) -> QVBoxLayout:
        column = QVBoxLayout()
        self._mic = self._check("Include microphone by default", autosave=False)
        self._system = self._check("Include system audio by default", autosave=False)
        # The recording contract carries one audio source, so the two defaults exclude each other.
        # Exclusivity is wired before autosave so the intermediate "both on" state is never stored.
        self._mic.toggled.connect(lambda on: on and self._system.setChecked(False))
        self._system.toggled.connect(lambda on: on and self._mic.setChecked(False))
        self._mic.toggled.connect(self._save)
        self._system.toggled.connect(self._save)
        column.addWidget(self._mic)
        column.addWidget(self._system)
        return column

    def _app_form(self) -> QVBoxLayout:
        column = QVBoxLayout()
        form = self._form()
        self._auto_copy = self._check("Copy the file to the clipboard when a recording finishes")
        self._ai_toolbar = self._check("Show the recording toolbar for AI recordings")
        self._hotkey = QLineEdit()
        self._hotkey.setMaximumWidth(200)
        self._hotkey.editingFinished.connect(self._save)
        self._theme = self._combo(list(THEME_CHOICES))
        column.addWidget(self._auto_copy)
        column.addWidget(self._ai_toolbar)
        form.addRow("New recording hotkey", self._hotkey)
        form.addRow("Theme", self._theme)
        column.addLayout(form)
        return column

    @staticmethod
    def _form() -> QFormLayout:
        form = QFormLayout()
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(10)
        return form

    def _combo(self, items: list[str]) -> QComboBox:
        box = QComboBox()
        box.addItems(items)
        box.setMaximumWidth(200)
        box.currentIndexChanged.connect(self._save)
        return box

    def _check(self, text: str, autosave: bool = True) -> QCheckBox:
        box = QCheckBox(text)
        if autosave:
            box.toggled.connect(self._save)
        return box

    # --- state ---------------------------------------------------------------
    def load(self) -> None:
        settings = self._service.get_settings()
        self._loaded = settings
        widgets = (self._fps, self._capture, self._cursor, self._mic, self._system, self._auto_copy,
                   self._ai_toolbar, self._theme)
        for widget in widgets:
            widget.blockSignals(True)
        self._folder.setText(str(settings.output_dir))
        self._fps.setCurrentText(str(settings.default_fps))
        self._capture.setCurrentText(settings.capture_mode.value)
        self._cursor.setChecked(settings.capture_cursor)
        self._mic.setChecked(settings.include_microphone)
        self._system.setChecked(settings.include_system_audio)
        self._auto_copy.setChecked(settings.auto_copy_to_clipboard)
        self._ai_toolbar.setChecked(settings.show_toolbar_for_ai_recordings)
        self._theme.setCurrentText(settings.theme)
        self._hotkey.setText(settings.hotkey)
        for widget in widgets:
            widget.blockSignals(False)

    def read_form(self) -> RecorderSettings:
        base = self._loaded or self._service.get_settings()
        return replace(
            base, output_dir=Path(self._folder.text()), default_fps=int(self._fps.currentText()),
            capture_mode=CaptureMode(self._capture.currentText()), capture_cursor=self._cursor.isChecked(),
            include_microphone=self._mic.isChecked(), include_system_audio=self._system.isChecked(),
            auto_copy_to_clipboard=self._auto_copy.isChecked(),
            show_toolbar_for_ai_recordings=self._ai_toolbar.isChecked(),
            hotkey=self._hotkey.text().strip() or base.hotkey, theme=self._theme.currentText(),  # type: ignore[arg-type]
        )

    def _save(self) -> None:
        if self._loaded is None:
            return
        changes = settings_changes(self._loaded, self.read_form())
        if not changes:
            return
        self._loaded = self._service.update_settings(changes)
        self.settings_saved.emit(self._loaded)

    def _browse(self) -> None:
        chosen = self._dialogs.choose_folder(Path(self._folder.text() or "."))
        if chosen is None:
            return
        self._folder.setText(str(chosen))
        self._save()

