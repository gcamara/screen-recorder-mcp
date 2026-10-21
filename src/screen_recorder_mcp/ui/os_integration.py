"""OS-specific window tricks: exclude overlays from capture, and the global hotkey.

Windows: SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE) and RegisterHotKey on a native event filter.
macOS: capture exclusion via NSWindow.sharingType = none; the global hotkey is NOT implemented
(logged once, the tray menu and in-app button still start a recording).
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter, QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget

from screen_recorder_mcp.geometry import Rect

log = logging.getLogger(__name__)

WDA_EXCLUDEFROMCAPTURE = 0x11
WM_HOTKEY = 0x0312
_MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8, "cmd": 0x8, "meta": 0x8}
_NAMED_KEYS = {"space": 0x20, "enter": 0x0D, "esc": 0x1B, "tab": 0x09, **{f"f{n}": 0x6F + n for n in range(1, 13)}}
_HOTKEY_ID = 0x5252


@dataclass(frozen=True)
class HotkeySpec:
    modifiers: int
    virtual_key: int


def parse_hotkey(text: str) -> HotkeySpec:
    """Parse "ctrl+shift+r" into Win32 modifier flags and a virtual-key code.

    Example: parse_hotkey("ctrl+shift+r") -> HotkeySpec(modifiers=6, virtual_key=82)
    """
    parts = [part.strip().lower() for part in text.split("+") if part.strip()]
    keys = [part for part in parts if part not in _MODS]
    if len(keys) != 1 or len(parts) < 2:
        raise ValueError(f"hotkey {text!r} must be modifiers plus one key, e.g. 'ctrl+shift+r'")
    modifiers = sum(_MODS[part] for part in parts if part in _MODS)
    return HotkeySpec(modifiers, _virtual_key(keys[0], text))


def _virtual_key(key: str, original: str) -> int:
    if key in _NAMED_KEYS:
        return _NAMED_KEYS[key]
    if len(key) == 1 and key.isalnum():
        return ord(key.upper())
    raise ValueError(f"key {key!r} in hotkey {original!r} must be a letter, digit, F1-F12, space, enter, esc or tab")


def exclude_from_capture(widget: QWidget) -> bool:
    """Hide this top-level window from screen capture where the OS allows it. Returns success."""
    if QGuiApplication.platformName() not in ("windows", "cocoa"):
        return False  # offscreen / tests: winId is not a real native handle
    if sys.platform == "win32":
        return _exclude_windows(int(widget.winId()))
    if sys.platform == "darwin":
        return _exclude_macos(int(widget.winId()))
    return False


def _exclude_windows(hwnd: int) -> bool:
    import ctypes

    ok = bool(ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))
    if not ok:
        log.warning(json.dumps({"event": "capture_exclusion_failed", "hwnd": hwnd}))
    return ok


def _exclude_macos(view_id: int) -> bool:
    try:
        import objc  # type: ignore[import-not-found]

        view = objc.objc_object(c_void_p=view_id)
        view.window().setSharingType_(0)  # NSWindowSharingNone
        return True
    except Exception as error:  # noqa: BLE001 - best effort, overlays still work
        log.warning(json.dumps({"event": "capture_exclusion_failed", "error": repr(error)}))
        return False


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, on_pressed: Callable[[], None]) -> None:
        super().__init__()
        self._on_pressed = on_pressed

    def nativeEventFilter(self, event_type: object, message: object) -> tuple[bool, int]:
        if bytes(event_type) != b"windows_generic_MSG":  # type: ignore[call-overload]
            return False, 0
        from ctypes import wintypes

        msg = wintypes.MSG.from_address(int(message))  # type: ignore[call-overload]
        if msg.message == WM_HOTKEY and msg.wParam == _HOTKEY_ID:
            self._on_pressed()
            return True, 0
        return False, 0


class GlobalHotkey(QObject):
    """Registers one system-wide hotkey; call `register("ctrl+shift+r")` again to rebind.

    Example: GlobalHotkey(app, on_pressed=start_new_recording).register(settings.hotkey)
    """

    def __init__(self, app: QApplication, on_pressed: Callable[[], None]) -> None:
        super().__init__(app)
        self._app = app
        self._filter = _HotkeyFilter(on_pressed)
        self._registered = False
        app.installNativeEventFilter(self._filter)

    def register(self, text: str) -> bool:
        if sys.platform != "win32":
            log.warning(json.dumps({"event": "global_hotkey_unsupported", "platform": sys.platform}))
            return False
        self.unregister()
        import ctypes

        spec = parse_hotkey(text)
        self._registered = bool(ctypes.windll.user32.RegisterHotKey(None, _HOTKEY_ID, spec.modifiers | 0x4000, spec.virtual_key))
        if not self._registered:
            log.warning(json.dumps({"event": "hotkey_register_failed", "hotkey": text}))
        return self._registered

    def unregister(self) -> None:
        if not self._registered or sys.platform != "win32":
            return
        import ctypes

        ctypes.windll.user32.UnregisterHotKey(None, _HOTKEY_ID)
        self._registered = False


def native_window_rect(window_id: int) -> Rect | None:
    """Where a window is now, in the OS's desktop units (physical px on Windows, points on macOS).

    Lets the recording border follow a window the user drags while it is being recorded.
    Returns None when the window is gone or the platform can't tell.
    Example: native_window_rect(int(target)) -> Rect(384, 250, 877, 540)
    """
    try:
        return _native_window_rect(window_id)
    except Exception as error:  # noqa: BLE001 - the border just stops following
        log.warning(json.dumps({"event": "window_rect_failed", "window_id": window_id, "error": repr(error)}))
        return None


def _native_window_rect(window_id: int) -> Rect | None:
    if sys.platform == "win32":
        from screen_recorder_mcp.backends.windows import win32_api

        return win32_api.visible_frame_rect(window_id) if win32_api.is_window(window_id) else None
    if sys.platform == "darwin":
        from screen_recorder_mcp.backends.macos.quartz_windows import QuartzWindowLister

        return next((w.rect for w in QuartzWindowLister().list_windows() if w.window_id == window_id), None)
    return None
