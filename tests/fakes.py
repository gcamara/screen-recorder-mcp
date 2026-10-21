"""Named fakes for the recorder's I/O boundaries."""

from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

import numpy as np

from screen_recorder_mcp.capture import AudioMode, ClosedCallback, FrameCallback
from screen_recorder_mcp.geometry import FrameArray, Rect
from screen_recorder_mcp.service import (
    RecorderEvent,
    RecorderEventKind,
    RecorderListener,
    RecorderSettings,
    RecordingInfo,
    RecordingRequest,
    RecordingState,
    RecordingStatus,
    TargetList,
    Unsubscribe,
)
from screen_recorder_mcp.media_tools import MediaProbe
from screen_recorder_mcp.recorder import RecordingParts, RecordingPlan, RecordingSession
from screen_recorder_mcp.targets import CaptureTarget
from screen_recorder_mcp.window_registry import WindowInfo


def solid_frame(width: int, height: int, value: int = 7) -> FrameArray:
    return np.full((height, width, 4), value, dtype=np.uint8)


class FakeWindowLister:
    def __init__(self, windows: list[WindowInfo]) -> None:
        self._windows = windows

    def list_windows(self) -> list[WindowInfo]:
        return list(self._windows)


class FakeFrameSource:
    """Delivers one frame synchronously on start (or none, to simulate minimized)."""

    def __init__(self, first_frame: FrameArray | None) -> None:
        self._first_frame = first_frame
        self.stopped = False
        self.closed_sink: ClosedCallback | None = None

    def start(self, frame_sink: FrameCallback, closed_sink: ClosedCallback) -> None:
        self.closed_sink = closed_sink
        if self._first_frame is not None:
            frame_sink(self._first_frame)

    def stop(self) -> None:
        self.stopped = True


class FakeEncoder:
    def __init__(self) -> None:
        self.size: tuple[int, int, int] | None = None
        self.frames: list[FrameArray] = []
        self.closed = False

    def open(self, width: int, height: int, fps: int) -> None:
        self.size = (width, height, fps)

    def write(self, frame: FrameArray) -> None:
        self.frames.append(frame)

    def close(self) -> None:
        self.closed = True


class FakeAudioRecorder:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.paused = False
        self.pause_calls = 0
        self.resume_calls = 0

    def pause(self) -> None:
        self.paused = True
        self.pause_calls += 1

    def resume(self) -> None:
        self.paused = False
        self.resume_calls += 1

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class FakeMuxer:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path, Path]] = []

    def __call__(self, video: Path, audio: Path, output: Path) -> None:
        self.calls.append((video, audio, output))


def window(window_id: int, title: str, process: str) -> WindowInfo:
    return WindowInfo(window_id=window_id, title=title, process=process, rect=Rect(0, 0, 800, 600))


class FailingStopFrameSource(FakeFrameSource):
    """OS capture API that errors on stop, as SCK does after the window closed."""

    def stop(self) -> None:
        self.stopped = True
        raise TimeoutError("capture stop gave no answer")


class FailingAudioRecorder(FakeAudioRecorder):
    def start(self) -> None:
        raise PermissionError("screen recording not granted")


class GpuOutOfMemoryFrameSource(FakeFrameSource):
    """GPU capture that can't create its session, like WGC with VRAM exhausted."""

    def start(self, frame_sink: FrameCallback, closed_sink: ClosedCallback) -> None:
        raise OSError("DirectX error 0x8007000E: not enough memory")


class FakeClock:
    """Manually advanced time source for pacers, WAV writers and the like."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class TouchingEncoder(FakeEncoder):
    """FakeEncoder that leaves a real (empty) file behind, like ffmpeg would."""

    def __init__(self, output: Path) -> None:
        super().__init__()
        self._output = output

    def close(self) -> None:
        super().close()
        self._output.write_bytes(b"mp4")


class FakeCommandRunner:
    """Scripted ffmpeg/ffprobe: records every command, answers from a queue."""

    def __init__(self, replies: list[tuple[int, bytes]] | None = None) -> None:
        self.commands: list[list[str]] = []
        self._replies = list(replies or [])

    def __call__(self, args: list[str]) -> "subprocess.CompletedProcess[bytes]":
        self.commands.append(args)
        code, out = self._replies.pop(0) if self._replies else (0, b"")
        if code == 0 and args[0] == "ffmpeg":
            Path(args[-1]).write_bytes(b"x")  # the output the real ffmpeg would write
        return subprocess.CompletedProcess(args, code, stdout=out, stderr=b"boom" if code else b"")


def probe_json(duration: float, width: int = 100, height: int = 80, audio: bool = False) -> bytes:
    streams = [{"codec_type": "video", "width": width, "height": height}]
    if audio:
        streams.append({"codec_type": "audio"})
    return json.dumps({"format": {"duration": str(duration)}, "streams": streams}).encode()


class FakeMediaTools:
    """MediaTools stand-in: fixed probe result, trim/gif just write files."""

    def __init__(self, duration: float = 4.0, audio: bool = False) -> None:
        self._probe = MediaProbe(duration, 100, 80, audio)
        self.trims: list[tuple[Path, float, float, Path]] = []
        self.gifs: list[tuple[Path, Path, bool]] = []

    def probe(self, path: Path) -> MediaProbe:
        return self._probe

    def trim(self, source: Path, start: float, end: float, output: Path) -> None:
        self.trims.append((source, start, end, output))
        output.write_bytes(b"trim")
        self._probe = MediaProbe(end - start, 100, 80, self._probe.has_audio)

    def export_gif(self, source: Path, output: Path, high_quality: bool) -> None:
        self.gifs.append((source, output, high_quality))
        output.write_bytes(b"gif")


class FakeRegionPicker:
    def __init__(self, region: Rect | None) -> None:
        self._region = region
        self.bounds: list[Rect] = []

    def pick_region(self, bounds: Rect) -> Rect | None:
        self.bounds.append(bounds)
        return self._region


class FakeBackend:
    """PlatformBackend with one window and one monitor; capture parts are never asked for."""

    name = "fake"

    def __init__(self, windows: list[WindowInfo]) -> None:
        self.windows = FakeWindowLister(windows)
        self.fronted: list[int | None] = []

    def prepare_process(self) -> None:
        return None

    def monitor_rects(self) -> list[Rect]:
        return [Rect(0, 0, 1920, 1080)]

    def bring_to_front(self, target: CaptureTarget) -> None:
        self.fronted.append(target.window_id)


class FakeSessionBuilder:
    """session_factory.build_session stand-in producing RecordingSessions over fakes."""

    def __init__(self, fail_start: bool = False) -> None:
        self.calls = 0
        self.sessions: list[RecordingSession] = []
        self._fail_start = fail_start

    def __call__(
        self, backend: object, plan: RecordingPlan, audio: AudioMode, cursor: bool, mode: object
    ) -> RecordingSession:
        self.calls += 1
        source: FakeFrameSource = (
            GpuOutOfMemoryFrameSource(None)
            if self._fail_start
            else FakeFrameSource(solid_frame(100, 80))
        )
        parts = RecordingParts(
            source,
            TouchingEncoder(plan.video_only_path),
            FakeAudioRecorder() if plan.audio_path else None,
            FakeMuxer(),
        )
        session = RecordingSession(plan, parts)
        self.sessions.append(session)
        return session


class ListenerLog:
    """Collects events; `kinds()` is the compact view tests assert on."""

    def __init__(self) -> None:
        self.events: list[RecorderEvent] = []

    def __call__(self, event: RecorderEvent) -> None:
        self.events.append(event)

    def kinds(self) -> list[RecorderEventKind]:
        return [event.kind for event in self.events]


class ExplodingListener:
    def __call__(self, event: RecorderEvent) -> None:
        raise RuntimeError("listener bug")


class GatedWait:
    """Countdown wait that never elapses on its own: returns when cancelled or released."""

    def __init__(self) -> None:
        self.release = threading.Event()

    def __call__(self, cancelled: threading.Event, seconds: float) -> bool:
        while not (cancelled.is_set() or self.release.is_set()):
            cancelled.wait(0.005)
        return cancelled.is_set()


def instant_wait(cancelled: threading.Event, seconds: float) -> bool:
    return cancelled.is_set()
class FakeRecorderService:
    """In-memory RecorderService: a tiny state machine that emits events like the real one."""

    def __init__(self) -> None:
        self.requests: list[RecordingRequest] = []
        self.picker_calls: list[str] = []
        self._status = RecordingStatus(state=RecordingState.IDLE)
        self._history: list[RecordingInfo] = []
        self._listeners: list[RecorderListener] = []
        self._settings = RecorderSettings(output_dir=Path("videos"))

    def list_targets(self) -> TargetList:
        rect = Rect(0, 0, 800, 600).as_dict()
        return TargetList(windows=[{"window_id": 1, "title": "Notepad"}], monitors=[{"target": "monitor:1", "rect": rect}])

    def pick_region(self, target: str) -> tuple[dict[str, object], Rect | None]:
        self.picker_calls.append(target)
        return {"target": target, "window_id": 1}, Rect(10, 20, 300, 200)

    def start(self, request: RecordingRequest) -> RecordingStatus:
        if self._status.state != RecordingState.IDLE:
            raise RuntimeError(f"a recording is already running ({self._status.recording_id}); stop it first")
        if request.target == "nope":
            raise LookupError("no window matches 'nope'; call list_targets")
        if request.delay_seconds < 0:
            raise ValueError(f"delay_seconds must be >= 0, got {request.delay_seconds}")
        self.requests.append(request)
        return self._move(RecordingState.RECORDING, RecorderEventKind.STARTED, request=request)

    def pause(self) -> RecordingStatus:
        self._require_state(RecordingState.RECORDING, "pause")
        return self._move(RecordingState.PAUSED, RecorderEventKind.PAUSED)

    def resume(self) -> RecordingStatus:
        self._require_state(RecordingState.PAUSED, "resume")
        return self._move(RecordingState.RECORDING, RecorderEventKind.RESUMED)

    def stop(self) -> RecordingInfo:
        if self._status.state == RecordingState.IDLE:
            raise RuntimeError("no recording is running; call start first")
        info = self._finished_info()
        self._history.insert(0, info)
        self._move(RecordingState.IDLE, RecorderEventKind.STOPPED, info=info)
        return info

    def cancel(self) -> None:
        self._require_state(None, "cancel")
        self._move(RecordingState.IDLE, RecorderEventKind.CANCELLED)

    def status(self) -> RecordingStatus:
        return self._status

    def history(self, limit: int = 50) -> list[RecordingInfo]:
        return self._history[:limit]

    def delete_recording(self, recording_id: str) -> None:
        self._find(recording_id)
        self._history = [info for info in self._history if info.recording_id != recording_id]
        self._emit(RecorderEventKind.HISTORY_CHANGED)

    def trim(self, recording_id: str, start_seconds: float, end_seconds: float) -> RecordingInfo:
        source = self._find(recording_id)
        if not 0 <= start_seconds < end_seconds <= source.duration_seconds:
            raise ValueError(f"trim range {start_seconds}-{end_seconds} must fit 0-{source.duration_seconds}")
        trimmed = replace(source, recording_id=f"{recording_id}-t", derived_from=recording_id,
                          duration_seconds=end_seconds - start_seconds)
        self._history.insert(0, trimmed)
        self._emit(RecorderEventKind.HISTORY_CHANGED)
        return trimmed

    def export_gif(self, recording_id: str, high_quality: bool, output: Path | None = None) -> Path:
        source = self._find(recording_id)
        return output or source.path.with_suffix(".hq.gif" if high_quality else ".gif")

    def get_settings(self) -> RecorderSettings:
        return self._settings

    def update_settings(self, changes: dict[str, object]) -> RecorderSettings:
        unknown = set(changes) - set(asdict(self._settings))
        if unknown:
            raise ValueError(f"unknown settings {sorted(unknown)}")
        self._settings = replace(self._settings, **changes)  # type: ignore[arg-type]
        self._emit(RecorderEventKind.SETTINGS_CHANGED)
        return self._settings

    def subscribe(self, listener: RecorderListener) -> Unsubscribe:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def emit_failure(self, message: str) -> None:
        """Test hook: simulate the app reporting a capture failure."""
        self._emit(RecorderEventKind.FAILED, message=message)

    def _require_state(self, expected: RecordingState | None, action: str) -> None:
        state = self._status.state
        if state == RecordingState.IDLE or (expected is not None and state != expected):
            raise RuntimeError(f"cannot {action} while state is {state.value!r}")

    def _find(self, recording_id: str) -> RecordingInfo:
        for info in self._history:
            if info.recording_id == recording_id:
                return info
        raise LookupError(f"no recording with id {recording_id!r}; known: {[i.recording_id for i in self._history]}")

    def _finished_info(self) -> RecordingInfo:
        started_by = self._status.started_by or "user"
        return RecordingInfo(
            recording_id=self._status.recording_id or "rec-0", path=Path("videos/rec.mp4"),
            created_at=datetime(2026, 10, 21, 12, 0, 0), duration_seconds=12.5, width=800, height=600,
            has_audio=False, target_label=self._status.target_label or "", started_by=started_by,
        )

    def _move(self, state: RecordingState, kind: RecorderEventKind, request: RecordingRequest | None = None,
              info: RecordingInfo | None = None) -> RecordingStatus:
        if request is not None:
            self._status = RecordingStatus(state=state, recording_id=f"rec-{len(self.requests)}", target_label=request.target,
                                           region=request.region, audio=request.audio, started_by=request.started_by)
        else:
            self._status = replace(self._status, state=state) if state != RecordingState.IDLE else RecordingStatus(state=state)
        self._emit(kind, info=info)
        return self._status

    def _emit(self, kind: RecorderEventKind, info: RecordingInfo | None = None, message: str | None = None) -> None:
        for listener in list(self._listeners):
            listener(RecorderEvent(kind, self._status, info, message))
