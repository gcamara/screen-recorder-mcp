"""CPU-only frame grabs through GDI, for when the GPU can't host a capture session.

`PrintWindow(PW_RENDERFULLCONTENT)` asks the window to render itself into our
bitmap, so windows covering it are still left out, like with WGC.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

import numpy as np

from screen_recorder_mcp.backends.windows import win32_api
from screen_recorder_mcp.geometry import FrameArray, Rect

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

_PW_RENDERFULLCONTENT = 0x2  # includes DirectX/Chromium content (Windows 8.1+)
_SRCCOPY = 0x00CC0020
_CAPTUREBLT = 0x40000000
_DIB_RGB_COLORS = 0
_BI_RGB = 0

# 64-bit handles overflow ctypes' default int conversion; declare every signature.
_user32.GetDC.argtypes = [wintypes.HWND]
_user32.GetDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
_user32.PrintWindow.restype = wintypes.BOOL
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
    wintypes.HANDLE, wintypes.DWORD,
]  # fmt: skip
_gdi32.CreateDIBSection.restype = wintypes.HBITMAP
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.BitBlt.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
]  # fmt: skip


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]  # fmt: skip


class _DibCanvas:
    """A top-down 32-bit BGRA DIB selected into a memory DC."""

    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.dc = _gdi32.CreateCompatibleDC(None)
        header = _BitmapInfoHeader(ctypes.sizeof(_BitmapInfoHeader), width, -height, 1, 32, _BI_RGB)
        self._bits = ctypes.c_void_p()
        self._bitmap = _gdi32.CreateDIBSection(
            self.dc, ctypes.byref(header), _DIB_RGB_COLORS, ctypes.byref(self._bits), None, 0
        )
        if not self._bitmap:
            raise OSError(f"CreateDIBSection failed for {width}x{height}; expected a 32-bit DIB")
        self._previous = _gdi32.SelectObject(self.dc, self._bitmap)

    def pixels(self) -> FrameArray:
        size = self.width * self.height * 4
        raw = (ctypes.c_ubyte * size).from_address(self._bits.value or 0)
        frame = np.frombuffer(raw, dtype=np.uint8).reshape(self.height, self.width, 4).copy()
        frame[..., 3] = 255  # GDI leaves alpha undefined
        return frame

    def close(self) -> None:
        _gdi32.SelectObject(self.dc, self._previous)
        _gdi32.DeleteObject(self._bitmap)
        _gdi32.DeleteDC(self.dc)


def grab_window(hwnd: int) -> FrameArray | None:
    """The window's own pixels, cropped to its visible frame (same rect WGC returns).

    Returns None when the window can't paint (minimized) or refuses PrintWindow.
    Example: grab_window(hwnd).shape -> (1098, 1502, 4)
    """
    if win32_api.is_minimized(hwnd):
        return None
    scale = win32_api.window_content_scale(hwnd)
    outer = _scaled(win32_api.window_rect(hwnd), 1 / scale)
    canvas = _DibCanvas(outer.width, outer.height)
    try:
        if not _user32.PrintWindow(hwnd, canvas.dc, _PW_RENDERFULLCONTENT):
            return None
        visible = win32_api.visible_frame_rect(hwnd)
        logical = _crop_to_visible(canvas.pixels(), outer, _scaled(visible, 1 / scale))
        return stretch_nearest(logical, visible.width, visible.height)
    finally:
        canvas.close()


def grab_screen_rect(rect: Rect) -> FrameArray:
    """Plain screen copy of a monitor rect (a monitor shows whatever is on top anyway)."""
    canvas = _DibCanvas(rect.width, rect.height)
    screen_dc = _user32.GetDC(None)
    try:
        _gdi32.BitBlt(
            canvas.dc, 0, 0, rect.width, rect.height, screen_dc, rect.x, rect.y, _SRCCOPY | _CAPTUREBLT
        )
        return canvas.pixels()
    finally:
        _user32.ReleaseDC(None, screen_dc)
        canvas.close()


def _crop_to_visible(frame: FrameArray, outer: Rect, visible: Rect) -> FrameArray:
    # GetWindowRect includes the invisible resize border; drop it.
    left, top = visible.x - outer.x, visible.y - outer.y
    return frame[top : top + visible.height, left : left + visible.width]


def _scaled(rect: Rect, factor: float) -> Rect:
    return Rect(
        round(rect.x * factor), round(rect.y * factor),
        max(1, round(rect.width * factor)), max(1, round(rect.height * factor)),
    )  # fmt: skip


def stretch_nearest(frame: FrameArray, width: int, height: int) -> FrameArray:
    """Nearest-neighbour resize, matching how DWM shows a DPI-scaled window.

    Example: stretch_nearest(np.zeros((4, 8, 4), np.uint8), 10, 5).shape -> (5, 10, 4)
    """
    if frame.shape[0] == height and frame.shape[1] == width:
        return frame
    rows = (np.arange(height) * frame.shape[0] // height).clip(0, frame.shape[0] - 1)
    cols = (np.arange(width) * frame.shape[1] // width).clip(0, frame.shape[1] - 1)
    return frame[rows][:, cols]
