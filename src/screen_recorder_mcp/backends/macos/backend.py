"""macOS backend (13 Ventura+): Quartz listing, ScreenCaptureKit video and app audio."""

from __future__ import annotations

from pathlib import Path

from screen_recorder_mcp.backends.macos import quartz_windows
from screen_recorder_mcp.backends.macos.mic_audio import MicAudioRecorder
from screen_recorder_mcp.backends.macos.sck_audio import SckAudioRecorder
from screen_recorder_mcp.backends.macos.sck_frame_source import SckFrameSource
from screen_recorder_mcp.capture import AudioMode, AudioRecorder, FrameSource
from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.targets import CaptureTarget


class MacBackend:
    name = "macos"

    def __init__(self) -> None:
        self.windows = quartz_windows.QuartzWindowLister()

    def prepare_process(self) -> None:
        quartz_windows.init_window_server_connection()

    def monitor_rects(self) -> list[Rect]:
        return [quartz_windows.display_rect(display) for display in quartz_windows.active_display_ids()]

    def bring_to_front(self, target: CaptureTarget) -> None:
        if target.pid:
            quartz_windows.activate_app(target.pid)

    def frame_source(self, target: CaptureTarget, capture_cursor: bool) -> FrameSource:
        if target.window_id is not None:
            return SckFrameSource(target.window_id, None, capture_cursor)
        return SckFrameSource(None, self._display_id(target), capture_cursor)

    def audio_recorder(self, mode: AudioMode, target: CaptureTarget, output: Path) -> AudioRecorder:
        if mode is AudioMode.MIC:
            return MicAudioRecorder(output)
        if mode is not AudioMode.SYSTEM:
            raise ValueError(f"audio mode {mode.value!r} needs no recorder; expected system|mic")
        # Window target → only its app's sound (pid); monitor → every app (pid 0).
        display_id = self._display_id(target) if target.monitor_index else self._main_display_id()
        return SckAudioRecorder(target.pid if target.window_id is not None else 0, display_id, output)

    def _display_id(self, target: CaptureTarget) -> int:
        displays = quartz_windows.active_display_ids()
        index = target.monitor_index or 1
        if not 1 <= index <= len(displays):
            raise LookupError(f"monitor:{index} is gone; expected monitor:1..monitor:{len(displays)}")
        return displays[index - 1]

    def _main_display_id(self) -> int:
        return quartz_windows.active_display_ids()[0]
