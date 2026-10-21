"""File-level actions on a recording: Save as, copy to clipboard, reveal in the file manager."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication


def save_copy(source: Path, destination: Path) -> Path:
    """Copy a recording to a user-chosen path (never moves: the history keeps pointing at the original).

    Example: save_copy(info.path, Path("~/Desktop/demo.mp4").expanduser())
    """
    if not source.is_file():
        raise FileNotFoundError(f"recording file {str(source)!r} does not exist; expected an existing .mp4")
    destination.parent.mkdir(parents=True, exist_ok=True)
    return Path(shutil.copy2(source, destination))


def copy_file_to_clipboard(path: Path) -> None:
    """Put the file itself on the clipboard so it can be pasted into Explorer, Slack, mail."""
    if not path.is_file():
        raise FileNotFoundError(f"cannot copy {str(path)!r} to the clipboard; expected an existing file")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    QGuiApplication.clipboard().setMimeData(mime)


def reveal_in_folder(path: Path) -> None:
    """Open the containing folder with the file selected where the OS supports it."""
    if sys.platform == "win32":
        subprocess.Popen(["explorer", f"/select,{path}"])  # noqa: S603,S607 - fixed argv, no shell
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])  # noqa: S603,S607
        return
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
