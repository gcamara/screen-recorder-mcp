"""Low-level mouse and keyboard hooks (WH_MOUSE_LL / WH_KEYBOARD_LL) feeding an InputLog.

The hooks live on their own thread with a message loop, only while a recording that asked for
input feedback runs. They never block or swallow input: every event is passed on with
CallNextHookEx, and the callbacks only append to the in-memory InputLog.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

from screen_recorder_mcp.input_overlay import InputLog, InputSnapshot, OverlayOptions

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_WH_KEYBOARD_LL, _WH_MOUSE_LL = 13, 14
_WM_QUIT = 0x0012
_WM_KEYDOWN, _WM_KEYUP, _WM_SYSKEYDOWN, _WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
_MOUSE_DOWN = {0x0201: "left", 0x0204: "right", 0x0207: "middle"}
_MOUSE_UP = {0x0202: "left", 0x0205: "right", 0x0208: "middle"}
_TOUNICODE_KEEP_STATE = 0x4  # Windows 10 1607+: translating does not disturb dead keys

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class _MouseHook(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]  # fmt: skip


class _KeyboardHook(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]  # fmt: skip


_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
_user32.CallNextHookEx.restype = LRESULT
_user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
_user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetKeyboardLayout.restype = wintypes.HKL
_user32.GetKeyState.restype = ctypes.c_short
_user32.ToUnicodeEx.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_ubyte), wintypes.LPWSTR,
                                ctypes.c_int, wintypes.UINT, wintypes.HKL]  # fmt: skip
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD

_MODIFIER_VKS = {
    0x10: "Shift", 0xA0: "Shift", 0xA1: "Shift", 0x11: "Ctrl", 0xA2: "Ctrl", 0xA3: "Ctrl",
    0x12: "Alt", 0xA4: "Alt", 0xA5: "Alt", 0x5B: "Win", 0x5C: "Win",
}  # fmt: skip
_NAMED_VKS = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x13: "Pause", 0x14: "CapsLock", 0x1B: "Esc", 0x20: "Space",
    0x21: "PageUp", 0x22: "PageDown", 0x23: "End", 0x24: "Home", 0x25: "Left", 0x26: "Up", 0x27: "Right",
    0x28: "Down", 0x2C: "PrintScreen", 0x2D: "Insert", 0x2E: "Delete", 0x5D: "Menu",
}  # fmt: skip
_VK_SHIFT, _VK_CAPITAL = 0x10, 0x14


def key_name(vk: int) -> str:
    """Display name for a virtual-key code, layout-independent (shortcuts read as "Ctrl + C").

    Example: key_name(0x43) -> "C"; key_name(0x74) -> "F5"; key_name(0x0D) -> "Enter"
    """
    if vk in _MODIFIER_VKS:
        return _MODIFIER_VKS[vk]
    if vk in _NAMED_VKS:
        return _NAMED_VKS[vk]
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x60 <= vk <= 0x69:
        return f"Num {vk - 0x60}"
    if 0x70 <= vk <= 0x87:
        return f"F{vk - 0x6F}"
    return f"Key {vk:#04x}"


def _typed_char(vk: int, scan: int, shift: bool) -> str | None:
    """The character this key types in the foreground window's keyboard layout, if any."""
    state = (ctypes.c_ubyte * 256)()
    if shift:
        state[_VK_SHIFT] = 0x80
    if _user32.GetKeyState(_VK_CAPITAL) & 1:
        state[_VK_CAPITAL] = 0x01
    thread = _user32.GetWindowThreadProcessId(_user32.GetForegroundWindow(), None)
    layout = _user32.GetKeyboardLayout(thread)
    buffer = ctypes.create_unicode_buffer(8)
    count = _user32.ToUnicodeEx(vk, scan, state, buffer, len(buffer), _TOUNICODE_KEEP_STATE, layout)
    return buffer.value[:1] if count == 1 and buffer.value[:1].isprintable() else None


def cursor_position() -> tuple[int, int] | None:
    point = wintypes.POINT()
    return (point.x, point.y) if _user32.GetCursorPos(ctypes.byref(point)) else None


class WindowsInputTracker:
    """InputTracker for Windows. Example: tracker.start(); tracker.snapshot(now); tracker.stop()"""

    def __init__(self, options: OverlayOptions) -> None:
        self._options = options
        self._log = InputLog(options, cursor_position)
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._shift = False
        # Keep the ctypes callbacks alive for as long as the hooks are installed.
        self._mouse_proc = HOOKPROC(self._on_mouse)
        self._keyboard_proc = HOOKPROC(self._on_keyboard)

    def start(self) -> None:
        if not (self._options.show_clicks or self._options.wants_keys):
            return  # the halo only needs GetCursorPos: no hooks at all
        self._thread = threading.Thread(target=self._run, name="input-hooks", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)

    def stop(self) -> None:
        if self._thread is None:
            return
        _user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
        self._thread.join(timeout=2.0)
        self._thread = None

    def snapshot(self, now: float) -> InputSnapshot:
        return self._log.snapshot(now)

    def _run(self) -> None:
        self._thread_id = _kernel32.GetCurrentThreadId()
        module = _kernel32.GetModuleHandleW(None)
        hooks = []
        if self._options.show_clicks:
            hooks.append(_user32.SetWindowsHookExW(_WH_MOUSE_LL, self._mouse_proc, module, 0))
        if self._options.wants_keys:
            hooks.append(_user32.SetWindowsHookExW(_WH_KEYBOARD_LL, self._keyboard_proc, module, 0))
        self._ready.set()
        message = wintypes.MSG()
        try:
            while _user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                pass
        finally:
            for hook in hooks:
                if hook:
                    _user32.UnhookWindowsHookEx(hook)

    def _on_mouse(self, code: int, wparam: int, lparam: int) -> int:
        if code >= 0:
            event = ctypes.cast(lparam, ctypes.POINTER(_MouseHook)).contents
            if wparam in _MOUSE_DOWN:
                self._log.mouse_down(_MOUSE_DOWN[wparam], event.pt.x, event.pt.y)
            elif wparam in _MOUSE_UP:
                self._log.mouse_up(_MOUSE_UP[wparam])
        return _user32.CallNextHookEx(None, code, wparam, lparam)

    def _on_keyboard(self, code: int, wparam: int, lparam: int) -> int:
        if code >= 0:
            event = ctypes.cast(lparam, ctypes.POINTER(_KeyboardHook)).contents
            name = key_name(event.vkCode)
            if wparam in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
                if name == "Shift":
                    self._shift = True
                char = None
                if self._options.show_keys.value == "all" and event.vkCode not in _MODIFIER_VKS:
                    char = _typed_char(event.vkCode, event.scanCode, self._shift)
                self._log.key_down(name, char)
            elif wparam in (_WM_KEYUP, _WM_SYSKEYUP):
                if name == "Shift":
                    self._shift = False
                self._log.key_up(name)
        return _user32.CallNextHookEx(None, code, wparam, lparam)
