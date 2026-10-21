"""Persisted RecorderSettings with strict validation of updates."""

from __future__ import annotations

import dataclasses
import re
import threading
from pathlib import Path
from typing import Callable

from screen_recorder_mcp.frame_sources import CaptureMode
from screen_recorder_mcp.json_file import read_json_object, write_json_atomic
from screen_recorder_mcp.service import RecorderSettings, to_jsonable

SETTINGS_FILE_NAME = "settings.json"
_HOTKEY = re.compile(r"^[a-z0-9]+(\+[a-z0-9]+)*$")
_THEMES = ("system", "dark", "light")


def _bad(key: str, value: object, expected: str) -> ValueError:
    return ValueError(f"setting {key!r}={value!r} is invalid; expected {expected}")


def _as_bool(key: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise _bad(key, value, "true or false")
    return value


def _as_fps(key: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 120:
        raise _bad(key, value, "an integer 1..120")
    return value


def _as_dir(key: str, value: object) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise _bad(key, value, "a non-empty folder path")
    return Path(value).expanduser()


def _as_capture_mode(key: str, value: object) -> CaptureMode:
    try:
        return value if isinstance(value, CaptureMode) else CaptureMode(value)
    except ValueError:
        raise _bad(key, value, "one of auto, gpu, cpu") from None


def _as_hotkey(key: str, value: object) -> str:
    if not isinstance(value, str) or not _HOTKEY.match(value.strip().lower()):
        raise _bad(key, value, "keys joined by '+', e.g. 'ctrl+shift+r'")
    return value.strip().lower()


def _as_theme(key: str, value: object) -> str:
    if value not in _THEMES:
        raise _bad(key, value, "one of system, dark, light")
    return str(value)


_VALIDATORS: dict[str, Callable[[str, object], object]] = {
    "output_dir": _as_dir,
    "default_fps": _as_fps,
    "include_microphone": _as_bool,
    "include_system_audio": _as_bool,
    "capture_mode": _as_capture_mode,
    "capture_cursor": _as_bool,
    "auto_copy_to_clipboard": _as_bool,
    "show_toolbar_for_ai_recordings": _as_bool,
    "hotkey": _as_hotkey,
    "theme": _as_theme,
}


def validate_changes(changes: dict[str, object]) -> dict[str, object]:
    """Typed copy of `changes`; raises ValueError naming the first bad key and its expected shape.

    Example: validate_changes({"default_fps": 60}) -> {"default_fps": 60}
    """
    checked: dict[str, object] = {}
    for key, value in changes.items():
        validator = _VALIDATORS.get(key)
        if validator is None:
            raise ValueError(f"unknown setting {key!r}; expected one of {sorted(_VALIDATORS)}")
        checked[key] = validator(key, value)
    return checked


class JsonSettingsStore:
    """Example: store = JsonSettingsStore(app_data_dir(), DEFAULT_OUTPUT_DIR); store.update({"theme": "dark"})"""

    def __init__(self, directory: Path, default_output_dir: Path) -> None:
        self._path = directory / SETTINGS_FILE_NAME
        self._lock = threading.Lock()
        self._settings = RecorderSettings(output_dir=default_output_dir)
        self._apply_stored(read_json_object(self._path))

    def get(self) -> RecorderSettings:
        with self._lock:
            return dataclasses.replace(self._settings)

    def update(self, changes: dict[str, object]) -> RecorderSettings:
        checked = validate_changes(changes)  # all-or-nothing: nothing is applied on a bad key
        with self._lock:
            self._settings = dataclasses.replace(self._settings, **checked)  # type: ignore[arg-type]
            write_json_atomic(self._path, to_jsonable(self._settings))
            return dataclasses.replace(self._settings)

    def _apply_stored(self, stored: dict[str, object]) -> None:
        for key, value in stored.items():
            try:
                checked = validate_changes({key: value})
            except ValueError:
                continue  # stale or hand-edited key: fall back to the default
            self._settings = dataclasses.replace(self._settings, **checked)  # type: ignore[arg-type]
