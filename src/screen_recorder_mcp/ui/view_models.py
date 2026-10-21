"""Qt-free view models: state mapping for the recording toolbar, request building, trim and history rows.

Kept free of Qt so the rules (what the pill shows in each state, when the AI badge appears,
how trim handles clamp) are unit-tested without a display.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from screen_recorder_mcp.capture import AudioMode
from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.service import (
    RecorderEvent,
    RecorderEventKind,
    RecorderSettings,
    RecordingInfo,
    RecordingRequest,
    RecordingState,
    RecordingStatus,
)
from screen_recorder_mcp.ui.formatting import format_clock, format_created, format_resolution


class CaptureTargetMode(str, Enum):
    """SnippingMode: Rectangle / RecordingWindow / Fullscreen."""

    RECTANGLE = "rectangle"
    WINDOW = "window"
    FULLSCREEN = "fullscreen"


MODE_LABELS: dict[CaptureTargetMode, str] = {
    CaptureTargetMode.RECTANGLE: "Rectangle",
    CaptureTargetMode.WINDOW: "Window",
    CaptureTargetMode.FULLSCREEN: "Full screen",
}

# SnipDelayOption: label -> seconds.
DELAY_OPTIONS: tuple[tuple[str, int], ...] = (
    ("No delay", 0), ("3 seconds", 3), ("5 seconds", 5), ("10 seconds", 10),
)


@dataclass(frozen=True)
class ToolbarView:
    """What the recording pill shows for one state (all fields are plain data)."""

    phase: str  # ready | countdown | recording | paused | finalizing
    timer_text: str
    show_start: bool
    show_pause: bool
    show_resume: bool
    show_stop: bool
    show_cancel: bool
    show_dot: bool
    dot_pulses: bool
    ai_badge: bool
    audio_editable: bool


def toolbar_view_for(status: RecordingStatus | None) -> ToolbarView:
    """Map a status to the pill layout; None means "selection made, not started" (ready).

    Example: toolbar_view_for(status).show_pause  # True while RECORDING
    """
    state = status.state if status else RecordingState.IDLE
    ai = bool(status and status.started_by == "ai")
    elapsed = status.elapsed_seconds if status else 0.0
    active = state in (RecordingState.RECORDING, RecordingState.PAUSED)
    return ToolbarView(
        phase="ready" if state is RecordingState.IDLE else state.value,
        timer_text=format_clock(elapsed),
        show_start=state is RecordingState.IDLE,
        show_pause=state is RecordingState.RECORDING,
        show_resume=state is RecordingState.PAUSED,
        show_stop=active,
        show_cancel=state is not RecordingState.FINALIZING,
        show_dot=state is not RecordingState.COUNTDOWN,
        dot_pulses=state is RecordingState.RECORDING,
        ai_badge=ai,
        audio_editable=state in (RecordingState.IDLE, RecordingState.COUNTDOWN),
    )


def toolbar_should_show(event: RecorderEvent, settings: RecorderSettings) -> bool:
    """Decide whether this event makes the pill appear (user recordings always; AI ones by setting).

    Example: toolbar_should_show(started_by_ai_event, settings)  # settings.show_toolbar_for_ai_recordings
    """
    if event.kind not in (RecorderEventKind.COUNTDOWN, RecorderEventKind.STARTED):
        return False
    if event.status.started_by == "ai":
        return settings.show_toolbar_for_ai_recordings
    return True


def toolbar_should_hide(event: RecorderEvent) -> bool:
    return event.kind in (
        RecorderEventKind.STOPPED, RecorderEventKind.CANCELLED,
        RecorderEventKind.FAILED, RecorderEventKind.TARGET_CLOSED,
    )


def audio_mode_for(microphone: bool, system_audio: bool) -> AudioMode:
    """The contract carries one AudioMode, so system audio wins if both are asked for.

    The UI keeps the two toggles mutually exclusive, so this only matters for stored settings.
    """
    if system_audio:
        return AudioMode.SYSTEM
    return AudioMode.MIC if microphone else AudioMode.NONE


def toggle_audio(current: AudioMode, which: AudioMode) -> AudioMode:
    """Clicking the active toggle turns audio off; clicking the other one switches to it."""
    if which is AudioMode.NONE:
        raise ValueError(f"which={which!r} must be AudioMode.MIC or AudioMode.SYSTEM")
    return AudioMode.NONE if current is which else which


def region_relative_to(region: Rect, origin: Rect) -> Rect:
    """Desktop-space rectangle -> relative to the target's top-left (the RecordingRequest shape)."""
    return Rect(region.x - origin.x, region.y - origin.y, region.width, region.height)


def build_request(
    target: str, region: Rect | None, settings: RecorderSettings, audio: AudioMode, delay: int
) -> RecordingRequest:
    """Compose the request the UI sends; started_by is always "user" for hand-made recordings.

    Example: build_request("monitor:1", None, settings, AudioMode.NONE, 3).delay_seconds  # 3
    """
    if delay not in {seconds for _, seconds in DELAY_OPTIONS}:
        raise ValueError(f"delay={delay!r} must be one of {[s for _, s in DELAY_OPTIONS]}")
    return RecordingRequest(
        target=target, region=region, fps=settings.default_fps, audio=audio,
        capture_cursor=settings.capture_cursor, capture=settings.capture_mode,
        output_dir=settings.output_dir, started_by="user", delay_seconds=delay,
    )


@dataclass
class TrimModel:
    """Left/right trim handles over a clip, clamped so at least `min_length` seconds remain."""

    duration: float
    start: float = 0.0
    end: float = 0.0
    min_length: float = 0.5

    def __post_init__(self) -> None:
        if self.duration <= 0:
            raise ValueError(f"duration={self.duration!r} must be > 0")
        self.min_length = min(self.min_length, self.duration)
        self.end = self.duration if self.end <= 0 else min(self.end, self.duration)

    def set_start(self, value: float) -> None:
        self.start = max(0.0, min(value, self.end - self.min_length))

    def set_end(self, value: float) -> None:
        self.end = min(self.duration, max(value, self.start + self.min_length))

    @property
    def is_trimmed(self) -> bool:
        return self.start > 0.0 or self.end < self.duration

    @property
    def length(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class HistoryRow:
    recording_id: str
    title: str
    subtitle: str
    duration_text: str
    ai_badge: bool


def history_row_for(info: RecordingInfo, now: datetime) -> HistoryRow:
    """Example: history_row_for(info, datetime.now()).duration_text  # "00:07"."""
    return HistoryRow(
        recording_id=info.recording_id,
        title=info.target_label,
        subtitle=f"{format_created(info.created_at, now)}  -  {format_resolution(info.width, info.height)}",
        duration_text=format_clock(info.duration_seconds),
        ai_badge=info.started_by == "ai",
    )


def toolbar_position(target: Rect, size: tuple[int, int], screen: Rect, gap: int = 12) -> tuple[int, int]:
    """Top-left for the pill: centred under the recorded area, else above it, else top of the screen.

    Always outside the target when there is room, so it cannot cover what is being recorded.
    Example: toolbar_position(Rect(100,100,800,500), (420,48), Rect(0,0,1920,1080)) -> (290, 612)
    """
    width, height = size
    x = target.x + (target.width - width) // 2
    x = max(screen.x, min(x, screen.x + screen.width - width))
    below = target.y + target.height + gap
    if below + height <= screen.y + screen.height:
        return x, below
    above = target.y - gap - height
    if above >= screen.y:
        return x, above
    return x, screen.y + gap


@dataclass(frozen=True)
class SelectionResult:
    """What the user picked on the overlay.

    target: a RecordingRequest target ("monitor:N" or a window id).
    region: crop relative to the target, desktop physical px; None records the whole target.
    area: the picked area in global LOGICAL px (Qt space), used to place the recording pill.
    screen: the logical rect of the screen the pick happened on.
    """

    mode: CaptureTargetMode
    target: str
    region: Rect | None
    area: Rect
    screen: Rect
    label: str
