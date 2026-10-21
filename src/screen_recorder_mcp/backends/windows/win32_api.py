"""Thin ctypes layer over the few Win32 calls the recorder needs.

Everything that touches user32/dwmapi/kernel32 lives here so the rest of the
package can be tested with fakes.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from screen_recorder_mcp.geometry import Rect

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_dwmapi = ctypes.WinDLL("dwmapi")
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_DWMWA_EXTENDED_FRAME_BOUNDS = 9
_DWMWA_CLOAKED = 14
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SW_RESTORE = 9
_DPI_AWARENESS_PER_MONITOR_V2 = ctypes.c_void_p(-4)

_EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def enable_dpi_awareness() -> None:
    """Make every coordinate physical pixels, matching what WGC delivers.

    Example: enable_dpi_awareness() at process start, before any window query.
    """
    try:
        _user32.SetProcessDpiAwarenessContext(_DPI_AWARENESS_PER_MONITOR_V2)
    except (AttributeError, OSError):
        ctypes.windll.shcore.SetProcessDpiAwareness(2)


def enum_top_level_hwnds() -> list[int]:
    """Return the HWND of every top-level window, front-to-back z-order."""
    hwnds: list[int] = []

    def collect(hwnd: int, _lparam: int) -> bool:
        hwnds.append(int(hwnd))
        return True

    _user32.EnumWindows(_EnumWindowsProc(collect), 0)
    return hwnds


def window_title(hwnd: int) -> str:
    length = _user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def is_visible(hwnd: int) -> bool:
    return bool(_user32.IsWindowVisible(hwnd))


def is_minimized(hwnd: int) -> bool:
    return bool(_user32.IsIconic(hwnd))


def is_cloaked(hwnd: int) -> bool:
    # UWP background windows are "visible" but cloaked by DWM; they never paint.
    cloaked = wintypes.DWORD(0)
    _dwmapi.DwmGetWindowAttribute(
        hwnd, _DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
    )
    return cloaked.value != 0


def is_window(hwnd: int) -> bool:
    return bool(_user32.IsWindow(hwnd))


def restore_window(hwnd: int) -> None:
    _user32.ShowWindow(hwnd, _SW_RESTORE)


def visible_frame_rect(hwnd: int) -> Rect:
    """Screen rect of the window without the invisible resize border.

    This is the rect WGC renders into its frames, so overlays and crops use it.
    """
    rect = wintypes.RECT()
    _dwmapi.DwmGetWindowAttribute(
        hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)
    )
    return Rect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def bring_to_front(hwnd: int) -> None:
    """Best effort: Windows may refuse focus steals, the overlay is topmost anyway."""
    if is_minimized(hwnd):
        restore_window(hwnd)
    _user32.SetForegroundWindow(hwnd)


_MonitorEnumProc = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
)


def monitor_rects() -> list[Rect]:
    """Monitor rects in EnumDisplayMonitors order (WGC's monitor_index is 1-based on it)."""
    rects: list[Rect] = []

    def collect(_hmon: int, _hdc: int, rect_ptr: ctypes._Pointer, _lparam: int) -> bool:  # type: ignore[type-arg]
        rect = rect_ptr.contents
        rects.append(Rect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top))
        return True

    _user32.EnumDisplayMonitors(None, None, _MonitorEnumProc(collect), 0)
    return rects


def process_name(hwnd: int) -> str:
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        return _query_image_name(handle)
    finally:
        _kernel32.CloseHandle(handle)


def _query_image_name(handle: int) -> str:
    size = wintypes.DWORD(1024)
    buffer = ctypes.create_unicode_buffer(size.value)
    if not _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
        return ""
    return buffer.value.rsplit("\\", 1)[-1]
