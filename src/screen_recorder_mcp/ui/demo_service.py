"""In-memory RecorderService for the preview app and tests: no screen capture, no backends.

Time only moves when `tick(dt)` is called, so tests are deterministic and the preview drives it
with a QTimer. Recordings "made" here copy a small ffmpeg testsrc clip.
Example: service = DemoRecorderService(Path(tmp)); service.start(request); service.tick(1.0)
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from screen_recorder_mcp.capture import AudioMode
from screen_recorder_mcp.geometry import Rect
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

SAMPLE_SECONDS = 8
_SETTINGS_KEYS = set(RecorderSettings.__dataclass_fields__)


def make_sample_video(path: Path, seconds: int = SAMPLE_SECONDS) -> Path:
    """Generate a tiny test MP4 with ffmpeg's testsrc2 (needs ffmpeg on PATH)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
        "-i", f"testsrc2=size=640x360:rate=30:duration={seconds}",
        "-pix_fmt", "yuv420p", "-c:v", "libx264", str(path),
    ]
    completed = subprocess.run(command, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"ffmpeg exited {completed.returncode} for {path}: {completed.stderr!r}")
    return path


class DemoRecorderService:
    def __init__(self, work_dir: Path, seed_history: bool = True,
                 monitors: list[Rect] | None = None, sample: Path | None = None) -> None:
        self._dir = work_dir
        self._monitors = monitors or [Rect(0, 0, 1920, 1080)]
        self._lock = threading.RLock()
        self._listeners: list[RecorderListener] = []
        self._settings = RecorderSettings(output_dir=work_dir)
        self._status = RecordingStatus(state=RecordingState.IDLE)
        self._request: RecordingRequest | None = None
        self._tick_carry = 0.0
        self._history: list[RecordingInfo] = []
        self._sample = sample or make_sample_video(work_dir / "sample.mp4")  # tests pass a stub file, no ffmpeg
        if seed_history:
            self._seed_history()

    # --- history seeding -------------------------------------------------
    def _seed_history(self) -> None:
        now = datetime.now()
        labels = [("Chrome - Pull request", "user", 0), ("Terminal", "ai", 1), ("Monitor 1", "user", 30)]
        for index, (label, who, hours_ago) in enumerate(labels):
            moved = shutil.copy(self._sample, self._dir / f"rec-demo-{index}.mp4")
            self._history.append(self._info(f"demo-{index}", Path(moved), label, who, now - timedelta(hours=hours_ago)))

    def _info(self, rec_id: str, path: Path, label: str, who: str, when: datetime) -> RecordingInfo:
        return RecordingInfo(
            recording_id=rec_id, path=path, created_at=when, duration_seconds=float(SAMPLE_SECONDS),
            width=640, height=360, has_audio=False, target_label=label, started_by=who,  # type: ignore[arg-type]
        )

    # --- RecorderService -------------------------------------------------
    def list_targets(self) -> TargetList:
        monitors = [{"target": f"monitor:{i}", "rect": r.as_dict()} for i, r in enumerate(self._monitors, 1)]
        first = self._monitors[0]
        window = {"window_id": 1, "title": "Demo window", "process": "demo.exe",
                  "rect": Rect(first.x + 200, first.y + 150, 900, 600).as_dict()}
        return TargetList(windows=[window], monitors=monitors)

    def pick_region(self, target: str) -> tuple[dict[str, object], Rect | None]:
        return {"label": target}, None

    def start(self, request: RecordingRequest) -> RecordingStatus:
        with self._lock:
            if self._status.state is not RecordingState.IDLE:
                raise RuntimeError(f"cannot start while state={self._status.state.value!r}; expected 'idle'")
            self._request = request
            self._tick_carry = 0.0
            if request.delay_seconds > 0:
                return self._enter_countdown(request)
            return self._enter_recording(request)

    def _enter_countdown(self, request: RecordingRequest) -> RecordingStatus:
        self._status = self._base_status(request, RecordingState.COUNTDOWN, countdown=request.delay_seconds)
        self._emit(RecorderEventKind.COUNTDOWN)
        return self._status

    def _enter_recording(self, request: RecordingRequest) -> RecordingStatus:
        self._status = self._base_status(request, RecordingState.RECORDING)
        self._emit(RecorderEventKind.STARTED)
        return self._status

    def _base_status(self, request: RecordingRequest, state: RecordingState, countdown: int = 0) -> RecordingStatus:
        return RecordingStatus(
            state=state, recording_id=f"demo-{len(self._history) + 1}-live", target_label=request.target,
            region=request.region, countdown_remaining=countdown, audio=request.audio,
            started_by=request.started_by,
        )

    def tick(self, seconds: float) -> None:
        """Advance demo time: counts down, or adds recorded seconds while RECORDING."""
        with self._lock:
            state = self._status.state
            if state is RecordingState.COUNTDOWN:
                self._tick_countdown(seconds)
            elif state is RecordingState.RECORDING:
                self._status = replace(self._status, elapsed_seconds=self._status.elapsed_seconds + seconds)

    def _tick_countdown(self, seconds: float) -> None:
        self._tick_carry += seconds
        while self._tick_carry >= 1.0 and self._status.state is RecordingState.COUNTDOWN:
            self._tick_carry -= 1.0
            remaining = self._status.countdown_remaining - 1
            if remaining <= 0 and self._request is not None:
                self._enter_recording(self._request)
                continue
            self._status = replace(self._status, countdown_remaining=remaining)
            self._emit(RecorderEventKind.COUNTDOWN)

    def pause(self) -> RecordingStatus:
        return self._transition(RecordingState.RECORDING, RecordingState.PAUSED, RecorderEventKind.PAUSED)

    def resume(self) -> RecordingStatus:
        return self._transition(RecordingState.PAUSED, RecordingState.RECORDING, RecorderEventKind.RESUMED)

    def _transition(self, expected: RecordingState, new: RecordingState, kind: RecorderEventKind) -> RecordingStatus:
        with self._lock:
            if self._status.state is not expected:
                raise RuntimeError(f"state={self._status.state.value!r}; expected {expected.value!r}")
            self._status = replace(self._status, state=new)
            self._emit(kind)
            return self._status

    def stop(self) -> RecordingInfo:
        with self._lock:
            if self._status.state not in (RecordingState.RECORDING, RecordingState.PAUSED):
                raise RuntimeError(f"state={self._status.state.value!r}; expected 'recording' or 'paused'")
            info = self._finish_recording()
            self._status = RecordingStatus(state=RecordingState.IDLE)
            self._emit(RecorderEventKind.STOPPED, info)
            return info

    def _finish_recording(self) -> RecordingInfo:
        rec_id = f"demo-{len(self._history) + 10}"
        path = Path(shutil.copy(self._sample, self._dir / f"rec-{rec_id}.mp4"))
        label = self._status.target_label or "Recording"
        who = self._status.started_by or "user"
        info = self._info(rec_id, path, label, who, datetime.now())
        self._history.insert(0, info)
        return info

    def cancel(self) -> None:
        with self._lock:
            self._status = RecordingStatus(state=RecordingState.IDLE)
            self._emit(RecorderEventKind.CANCELLED)

    def status(self) -> RecordingStatus:
        return self._status

    def history(self, limit: int = 50) -> list[RecordingInfo]:
        return list(self._history[:limit])

    def delete_recording(self, recording_id: str) -> None:
        with self._lock:
            info = self._find(recording_id)
            self._history.remove(info)
            self._emit(RecorderEventKind.HISTORY_CHANGED)

    def _find(self, recording_id: str) -> RecordingInfo:
        for info in self._history:
            if info.recording_id == recording_id:
                return info
        raise LookupError(f"recording_id={recording_id!r} not in history {[i.recording_id for i in self._history]}")

    def trim(self, recording_id: str, start_seconds: float, end_seconds: float) -> RecordingInfo:
        with self._lock:
            source = self._find(recording_id)
            if not 0 <= start_seconds < end_seconds <= source.duration_seconds:
                raise ValueError(
                    f"trim range ({start_seconds}, {end_seconds}) must satisfy 0 <= start < end <= {source.duration_seconds}"
                )
            path = Path(shutil.copy(source.path, self._dir / f"trim-{len(self._history)}.mp4"))
            info = replace(source, recording_id=f"{recording_id}-t{len(self._history)}", path=path,
                           duration_seconds=end_seconds - start_seconds, created_at=datetime.now(),
                           derived_from=recording_id)
            self._history.insert(0, info)
            self._emit(RecorderEventKind.HISTORY_CHANGED)
            return info

    def export_gif(self, recording_id: str, high_quality: bool, output: Path | None = None) -> Path:
        source = self._find(recording_id)
        target = output or self._dir / f"{recording_id}.gif"
        width = 480 if high_quality else 240
        command = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(source.path),
                   "-vf", f"fps=10,scale={width}:-1", str(target)]
        completed = subprocess.run(command, capture_output=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"ffmpeg exited {completed.returncode} writing {target}: {completed.stderr!r}")
        return target

    def get_settings(self) -> RecorderSettings:
        return replace(self._settings)

    def update_settings(self, changes: dict[str, object]) -> RecorderSettings:
        unknown = set(changes) - _SETTINGS_KEYS
        if unknown:
            raise ValueError(f"unknown settings {sorted(unknown)}; expected keys from {sorted(_SETTINGS_KEYS)}")
        with self._lock:
            self._settings = replace(self._settings, **changes)  # type: ignore[arg-type]
            self._emit(RecorderEventKind.SETTINGS_CHANGED)
            return replace(self._settings)

    def subscribe(self, listener: RecorderListener) -> Unsubscribe:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def _emit(self, kind: RecorderEventKind, info: RecordingInfo | None = None) -> None:
        event = RecorderEvent(kind=kind, status=self._status, info=info)
        for listener in list(self._listeners):
            listener(event)

    # --- demo helpers ------------------------------------------------------
    def simulate_ai_start(self, target: str = "Chrome - checkout flow") -> RecordingStatus:
        """What the MCP server does remotely: start a recording with started_by="ai"."""
        return self.start(RecordingRequest(target=target, started_by="ai", audio=AudioMode.NONE))
