"""Modal prompts behind a tiny interface so views stay testable (tests inject a fake)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget


class Dialogs(Protocol):
    def save_path(self, suggested_name: str, start_dir: Path) -> Path | None: ...
    def choose_folder(self, start_dir: Path) -> Path | None: ...
    def confirm(self, title: str, text: str) -> bool: ...


class QtDialogs:
    """Native Qt dialogs. Example: QtDialogs(main_window).confirm("Delete", "Delete this recording?")"""

    def __init__(self, parent: QWidget | None = None) -> None:
        self._parent = parent

    def save_path(self, suggested_name: str, start_dir: Path) -> Path | None:
        suffix = Path(suggested_name).suffix.lstrip(".") or "mp4"
        chosen, _ = QFileDialog.getSaveFileName(
            self._parent, "Save as", str(start_dir / suggested_name), f"{suffix.upper()} (*.{suffix})"
        )
        return Path(chosen) if chosen else None

    def choose_folder(self, start_dir: Path) -> Path | None:
        chosen = QFileDialog.getExistingDirectory(self._parent, "Choose output folder", str(start_dir))
        return Path(chosen) if chosen else None

    def confirm(self, title: str, text: str) -> bool:
        answer = QMessageBox.question(self._parent, title, text)
        return answer == QMessageBox.StandardButton.Yes
