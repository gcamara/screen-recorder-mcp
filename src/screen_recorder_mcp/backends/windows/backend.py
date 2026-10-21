"""Windows backend: Win32 window listing, Windows Graphics Capture, WASAPI audio."""

from __future__ import annotations

from pathlib import Path

from screen_recorder_mcp.backends.windows import win32_api
from screen_recorder_mcp.backends.windows.wasapi_audio import WasapiAudioRecorder
from screen_recorder_mcp.backends.windows.wgc_frame_source import WgcFrameSource
from screen_recorder_mcp.capture import AudioMode, AudioRecorder, FrameSource
from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.targets import CaptureTarget
from screen_recorder_mcp.window_registry import WindowInfo


class Win32WindowLister:
    """Lists visible, uncloaked, titled windows (what a user calls 'an app')."""

    def list_windows(self) -> list[WindowInfo]:
        return [
            self._describe(hwnd)
            for hwnd in win32_api.enum_top_level_hwnds()
            if self._is_recordable(hwnd)
        ]

    def _is_recordable(self, hwnd: int) -> bool:
        if not win32_api.is_visible(hwnd) or win32_api.is_cloaked(hwnd):
            return False
        return bool(win32_api.window_title(hwnd).strip())

    def _describe(self, hwnd: int) -> WindowInfo:
        return WindowInfo(
            window_id=hwnd,
            title=win32_api.window_title(hwnd),
            process=win32_api.process_name(hwnd),
            rect=win32_api.visible_frame_rect(hwnd),
        )


class WindowsBackend:
    name = "windows"

    def __init__(self) -> None:
        self.windows = Win32WindowLister()

    def prepare_process(self) -> None:
        win32_api.enable_dpi_awareness()

    def monitor_rects(self) -> list[Rect]:
        return win32_api.monitor_rects()

    def bring_to_front(self, target: CaptureTarget) -> None:
        if target.window_id is not None:
            win32_api.bring_to_front(target.window_id)

    def frame_source(self, target: CaptureTarget, capture_cursor: bool) -> FrameSource:
        return WgcFrameSource(target.window_id, target.monitor_index, capture_cursor)

    def audio_recorder(self, mode: AudioMode, target: CaptureTarget, output: Path) -> AudioRecorder:
        # WASAPI has no per-window loopback here: "system" is every app's sound.
        return WasapiAudioRecorder(mode, output)
