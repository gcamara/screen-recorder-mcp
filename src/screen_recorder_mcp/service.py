"""The recording service contract shared by the desktop app, the control server and MCP.

One process owns recording at a time:
- the desktop app (tray + Snipping-Tool-like UI) runs a `LocalRecorderService`
  and exposes it on localhost through the control server;
- the MCP server talks to that app through `RemoteRecorderService` when the app
  is running, and falls back to its own in-process `LocalRecorderService`
  otherwise (headless, as before the app existed).

Both sides only depend on the `RecorderService` protocol below, so a recording
the AI starts shows up in the app's recording toolbar, and recordings the user
made by hand are visible to the AI through `history()`.

Implementations:
- screen_recorder_mcp.service_local.LocalRecorderService      (in-process)
- screen_recorder_mcp.control.client.RemoteRecorderService    (localhost client)
- screen_recorder_mcp.control.server.serve_control(...)       (exposes a service)
"""

from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Literal, Protocol

from screen_recorder_mcp.capture import AudioMode
from screen_recorder_mcp.frame_sources import CaptureMode
from screen_recorder_mcp.geometry import Rect

StartedBy = Literal["user", "ai"]


class RecordingState(str, Enum):
    IDLE = "idle"
    COUNTDOWN = "countdown"  # delay before recording (Snipping Tool's "snip delay")
    RECORDING = "recording"
    PAUSED = "paused"
    FINALIZING = "finalizing"  # ffmpeg closing / audio mux


@dataclass(frozen=True)
class RecordingRequest:
    """Everything needed to start one recording.

    target: window title / process name / window_id, or `monitor:N` (see targets.py).
    region: optional crop relative to the target, in desktop units (points on macOS).
    delay_seconds: countdown before capture starts (0, 3, 5 or 10 in the UI).
    """

    target: str
    region: Rect | None = None
    fps: int = 30
    audio: AudioMode = AudioMode.NONE
    capture_cursor: bool = True
    capture: CaptureMode = CaptureMode.AUTO
    output_dir: Path | None = None
    started_by: StartedBy = "user"
    delay_seconds: int = 0


@dataclass(frozen=True)
class RecordingStatus:
    state: RecordingState
    recording_id: str | None = None
    target_label: str | None = None
    region: Rect | None = None
    # Seconds of recorded content: excludes countdown and paused time.
    elapsed_seconds: float = 0.0
    countdown_remaining: int = 0
    audio: AudioMode = AudioMode.NONE
    started_by: StartedBy | None = None
    output: Path | None = None
    target_closed: bool = False


@dataclass(frozen=True)
class RecordingInfo:
    """One finished recording in the history (newest first in history())."""

    recording_id: str
    path: Path
    created_at: datetime
    duration_seconds: float
    width: int
    height: int
    has_audio: bool
    target_label: str
    started_by: StartedBy
    derived_from: str | None = None  # recording_id this was trimmed from


@dataclass
class RecorderSettings:
    """Persisted user preferences (Snipping Tool's settings page, recording part)."""

    output_dir: Path
    default_fps: int = 30
    include_microphone: bool = False
    include_system_audio: bool = False
    capture_mode: CaptureMode = CaptureMode.AUTO
    capture_cursor: bool = True
    auto_copy_to_clipboard: bool = True  # copies the finished file to the clipboard, like Snipping Tool
    show_toolbar_for_ai_recordings: bool = True
    hotkey: str = "ctrl+shift+r"  # global "new recording" shortcut (cmd+shift+r on macOS)
    theme: Literal["system", "dark", "light"] = "system"


@dataclass(frozen=True)
class TargetList:
    windows: list[dict[str, object]] = field(default_factory=list)  # WindowInfo.as_dict()
    monitors: list[dict[str, object]] = field(default_factory=list)  # {"target", "rect"}


class RecorderEventKind(str, Enum):
    COUNTDOWN = "countdown"
    STARTED = "started"
    PAUSED = "paused"
    RESUMED = "resumed"
    STOPPED = "stopped"  # info set
    CANCELLED = "cancelled"
    FAILED = "failed"  # message set
    TARGET_CLOSED = "target_closed"
    HISTORY_CHANGED = "history_changed"  # trim/gif/delete
    SETTINGS_CHANGED = "settings_changed"


@dataclass(frozen=True)
class RecorderEvent:
    kind: RecorderEventKind
    status: RecordingStatus
    info: RecordingInfo | None = None
    message: str | None = None


RecorderListener = Callable[[RecorderEvent], None]
Unsubscribe = Callable[[], None]


class RegionPicker(Protocol):
    """Lets the USER draw the crop over a target. Returns None for "whole target".

    The desktop app implements it with its Qt overlay (it must marshal onto the
    Qt GUI thread; the call may come from the control-server thread). Headless
    mode uses region_picker.pick_region_interactively (Tk subprocess).
    """

    def pick_region(self, bounds: Rect) -> Rect | None: ...


class RecorderService(Protocol):
    """Single owner of recording. All methods are thread-safe and blocking.

    Errors: raise ValueError for bad input, LookupError for unknown targets or
    recording ids, RuntimeError for state conflicts (e.g. start while recording).
    Messages include the offending value and the expected shape.
    """

    def list_targets(self) -> TargetList: ...
    def pick_region(self, target: str) -> tuple[dict[str, object], Rect | None]: ...
    def start(self, request: RecordingRequest) -> RecordingStatus: ...
    def pause(self) -> RecordingStatus: ...
    def resume(self) -> RecordingStatus: ...
    def stop(self) -> RecordingInfo: ...
    def cancel(self) -> None: ...  # stop and delete the partial file
    def status(self) -> RecordingStatus: ...
    def history(self, limit: int = 50) -> list[RecordingInfo]: ...
    def delete_recording(self, recording_id: str) -> None: ...
    def trim(self, recording_id: str, start_seconds: float, end_seconds: float) -> RecordingInfo: ...
    def export_gif(
        self, recording_id: str, high_quality: bool, output: Path | None = None
    ) -> Path: ...
    def get_settings(self) -> RecorderSettings: ...
    def update_settings(self, changes: dict[str, object]) -> RecorderSettings: ...
    def subscribe(self, listener: RecorderListener) -> Unsubscribe: ...


def app_data_dir() -> Path:
    """Per-user folder for settings, history and the control-server discovery file.

    Example: app_data_dir() -> C:/Users/me/AppData/Roaming/screen-recorder-mcp
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "screen-recorder-mcp"


def to_jsonable(value: object) -> object:
    """Dataclasses/enums/paths/datetimes → JSON-safe values (shared by control server and MCP)."""
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}  # type: ignore[call-overload]
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value
