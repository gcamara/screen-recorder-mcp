"""Save an edit (trim / crop): as a new file next to the original (default) or over the original."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QPushButton, QRadioButton, QVBoxLayout, QWidget

from screen_recorder_mcp.recording_library import edit_output_path
from screen_recorder_mcp.ui.widgets_common import role_label


@dataclass(frozen=True)
class SaveChoice:
    output_name: str | None  # None when overwriting
    overwrite: bool


class SaveEditDialog(QDialog):
    """Example: dialog = SaveEditDialog(info.path, "rec-trim.mp4"); dialog.exec(); dialog.choice

    The name field is focused with its stem selected, so typing replaces the name and Enter saves.
    """

    def __init__(self, source: Path, suggested_name: str, summary: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Save edit")
        self.setMinimumWidth(460)
        self._source = source
        self.choice: SaveChoice | None = None
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(role_label(summary, "muted"))
        self._as_new = QRadioButton("Save as a new file")
        self._as_new.setChecked(True)
        self._name = QLineEdit(suggested_name)
        self._name.setSelection(0, len(Path(suggested_name).stem))
        self._overwrite = QRadioButton(f"Overwrite the original ({source.name})")
        self._error = role_label("", "muted")
        self._error.hide()
        for widget in (self._as_new, self._name, self._overwrite, self._error):
            layout.addWidget(widget)
        layout.addLayout(self._buttons())
        self._overwrite.toggled.connect(lambda on: self._name.setEnabled(not on))
        self._name.textEdited.connect(lambda _text: self._error.hide())
        self._name.setFocus()

    def _buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        cancel, save = QPushButton("Cancel"), QPushButton("Save")
        save.setProperty("role", "primary")
        save.setDefault(True)  # Enter in the name field saves
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._accept_choice)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(save)
        return row

    def _accept_choice(self) -> None:
        if self._overwrite.isChecked():
            self.choice = SaveChoice(None, overwrite=True)
            self.accept()
            return
        try:
            target = edit_output_path(self._source, self._name.text())
        except (ValueError, FileExistsError) as error:
            self._error.setText(str(error))
            self._error.show()
            self._name.setFocus()
            return
        self.choice = SaveChoice(target.name, overwrite=False)
        self.accept()
