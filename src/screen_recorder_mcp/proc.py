"""Subprocess options that differ per OS."""

from __future__ import annotations

import subprocess
import sys


def hidden_window_flags() -> int:
    """creationflags that keep ffmpeg from flashing a console window on Windows; 0 elsewhere.

    Example: subprocess.Popen(args, creationflags=hidden_window_flags())
    """
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0
