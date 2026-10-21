"""In-process implementation of the RecorderService contract (see service.py)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from screen_recorder_mcp.backends import PlatformBackend
from screen_recorder_mcp.countdown import TickWait, run_countdown, wait_on_event
from screen_recorder_mcp.event_bus import EventBus
from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.history_store import JsonHistoryStore
from screen_recorder_mcp.media_tools import MediaTools
from screen_recorder_mcp.recorder import RecordingPlan, RecordingSession
from screen_recorder_mcp.recording_library import RecordingLibrary, new_recording_id
from screen_recorder_mcp.service import (
    RecorderEvent,
    RecorderEventKind,
    RecorderListener,
    RecorderSettings,
    RecordingInfo,
    RecordingRequest,
    RecordingState,
    RecordingStatus,
    RegionPicker,
    TargetList,
    Unsubscribe,
    app_data_dir,
)
from screen_recorder_mcp.session_factory import DEFAULT_OUTPUT_DIR, build_session, plan_recording
from screen_recorder_mcp.settings_store import JsonSettingsStore
from screen_recorder_mcp.targets import CaptureTarget, resolve_target

SessionBuilder = Callable[..., RecordingSession]
Planner = Callable[..., RecordingPlan]
MAX_DELAY_SECONDS = 60


@dataclass
class _Active:
    """Everything that belongs to the one recording in flight."""

    recording_id: str
    request: RecordingRequest
    target: CaptureTarget
    plan: RecordingPlan | None = None
    session: RecordingSession | None = None
    countdown_remaining: int = 0
    cancelled: threading.Event = field(default_factory=threading.Event)


class LocalRecorderService:
    """Owns recording in this process. Thread-safe: one lock guards state transitions.

    start() with delay_seconds > 0 returns at once in COUNTDOWN and the capture begins
    on a worker thread (STARTED or FAILED event); with no delay it starts synchronously.

    Example:
        service = LocalRecorderService(current_backend(), picker)
        service.start(RecordingRequest(target="Notepad")); info = service.stop()
    """

    def __init__(
        self,
        backend: PlatformBackend,
        region_picker: RegionPicker,
        data_dir: Path | None = None,
        *,
        media_tools: MediaTools | None = None,
        session_builder: SessionBuilder = build_session,
        planner: Planner = plan_recording,
        clock: Callable[[], datetime] = datetime.now,
        countdown_wait: TickWait = wait_on_event,
        default_output_dir: Path = DEFAULT_OUTPUT_DIR,
    ) -> None:
        directory = data_dir if data_dir is not None else app_data_dir()
        self._backend = backend
        self._picker = region_picker
        self._session_builder = session_builder
        self._planner = planner
        self._countdown_wait = countdown_wait
        self._lock = threading.RLock()
        self._state = RecordingState.IDLE
        self._active: _Active | None = None
        self._bus = EventBus()
        self._settings = JsonSettingsStore(directory, default_output_dir)
        self._library = RecordingLibrary(JsonHistoryStore(directory), media_tools, clock)

    # --- targets and region -------------------------------------------------

    def list_targets(self) -> TargetList:
        monitors = [
            {"target": f"monitor:{index}", "rect": rect.as_dict()}
            for index, rect in enumerate(self._backend.monitor_rects(), start=1)
        ]
        windows = [w.as_dict() for w in self._backend.windows.list_windows()]
        return TargetList(windows=windows, monitors=monitors)

    def pick_region(self, target: str) -> tuple[dict[str, object], Rect | None]:
        """Let the user drag a crop over `target`; (None region = whole target)."""
        resolved = self._resolve(target)
        if resolved.window_id is not None:
            self._backend.bring_to_front(resolved)
            resolved = self._resolve(str(resolved.window_id))  # rect may change after restore
        return resolved.as_dict(), self._picker.pick_region(resolved.rect)

    # --- recording lifecycle ------------------------------------------------

    def start(self, request: RecordingRequest) -> RecordingStatus:
        with self._lock:
            self._require_state(RecordingState.IDLE, "start")
            _validate_delay(request.delay_seconds)
            active = _Active(new_recording_id(), request, self._resolve(request.target))
            self._active = active
            if request.delay_seconds == 0:
                self._begin_capture(active)
            else:
                self._begin_countdown(active)
            return self._status_now()

    def pause(self) -> RecordingStatus:
        with self._lock:
            self._require_state(RecordingState.RECORDING, "pause")
            self._session().pause()
            self._state = RecordingState.PAUSED
            self._emit(RecorderEventKind.PAUSED)
            return self._status_now()

    def resume(self) -> RecordingStatus:
        with self._lock:
            self._require_state(RecordingState.PAUSED, "resume")
            self._session().resume()
            self._state = RecordingState.RECORDING
            self._emit(RecorderEventKind.RESUMED)
            return self._status_now()

    def stop(self) -> RecordingInfo:
        with self._lock:
            if self._state not in (RecordingState.RECORDING, RecordingState.PAUSED):
                raise RuntimeError(
                    f"cannot stop while {self._state.value!r}; expected 'recording' or 'paused'"
                )
            active = self._finalizing()
        try:
            info = self._finish(active)
        except Exception as error:
            self._reset_after_failure(error)
            raise
        self._reset_to_idle()
        self._emit(RecorderEventKind.STOPPED, info=info)
        self._emit(RecorderEventKind.HISTORY_CHANGED, info=info)
        return info

    def cancel(self) -> None:
        """Abort a countdown, or stop a recording and delete its partial files."""
        with self._lock:
            if self._state is RecordingState.IDLE:
                return
            if self._state is RecordingState.FINALIZING:
                raise RuntimeError("cannot cancel while 'finalizing'; the recording is already closing")
            if self._state is RecordingState.COUNTDOWN:
                self._cancel_countdown()
                return
            active = self._finalizing()
        self._discard(active)
        self._reset_to_idle()
        self._emit(RecorderEventKind.CANCELLED)

    def status(self) -> RecordingStatus:
        with self._lock:
            return self._status_now()

    # --- history, editing ---------------------------------------------------

    def history(self, limit: int = 50) -> list[RecordingInfo]:
        return self._library.entries(limit)

    def delete_recording(self, recording_id: str) -> None:
        self._library.delete(recording_id)
        self._emit(RecorderEventKind.HISTORY_CHANGED)

    def trim(self, recording_id: str, start_seconds: float, end_seconds: float) -> RecordingInfo:
        info = self._library.trim(recording_id, start_seconds, end_seconds)
        self._emit(RecorderEventKind.HISTORY_CHANGED, info=info)
        return info

    def export_gif(
        self, recording_id: str, high_quality: bool, output: Path | None = None
    ) -> Path:
        return self._library.export_gif(recording_id, high_quality, output)

    # --- settings, events ---------------------------------------------------

    def get_settings(self) -> RecorderSettings:
        return self._settings.get()

    def update_settings(self, changes: dict[str, object]) -> RecorderSettings:
        updated = self._settings.update(changes)
        self._emit(RecorderEventKind.SETTINGS_CHANGED)
        return updated

    def subscribe(self, listener: RecorderListener) -> Unsubscribe:
        return self._bus.subscribe(listener)

    def flush_events(self) -> None:
        """Wait until every queued event reached the listeners (tests, shutdown)."""
        self._bus.flush()

    # --- internals (callers hold the lock unless stated) --------------------

    def _resolve(self, query: str) -> CaptureTarget:
        return resolve_target(query, self._backend.windows, self._backend.monitor_rects)

    def _require_state(self, expected: RecordingState, action: str) -> None:
        if self._state is not expected:
            raise RuntimeError(
                f"cannot {action} while {self._state.value!r}; expected {expected.value!r}"
            )

    def _session(self) -> RecordingSession:
        assert self._active is not None and self._active.session is not None
        return self._active.session

    def _begin_countdown(self, active: _Active) -> None:
        self._state = RecordingState.COUNTDOWN
        threading.Thread(target=self._countdown_then_capture, args=(active,), daemon=True).start()

    def _countdown_then_capture(self, active: _Active) -> None:
        """Worker thread: tick once per second, then start capturing unless cancelled."""
        if not run_countdown(
            active.request.delay_seconds,
            lambda remaining: self._on_tick(active, remaining),
            active.cancelled,
            self._countdown_wait,
        ):
            return
        with self._lock:
            if self._active is not active or self._state is not RecordingState.COUNTDOWN:
                return
            try:
                self._begin_capture(active)
            except Exception as error:  # reported as an event: nobody is waiting on start()
                self._emit(RecorderEventKind.FAILED, message=str(error))

    def _on_tick(self, active: _Active, remaining: int) -> None:
        with self._lock:
            if self._active is not active:
                return
            active.countdown_remaining = remaining
            self._emit(RecorderEventKind.COUNTDOWN)

    def _begin_capture(self, active: _Active) -> None:
        """Create the files and start capturing; on failure back to IDLE and re-raise."""
        request = active.request
        try:
            active.plan = self._plan(active)
            active.session = self._session_builder(
                self._backend, active.plan, request.audio, request.capture_cursor, request.capture
            )
            active.session.on_target_closed = lambda: self._emit(RecorderEventKind.TARGET_CLOSED)
            active.session.start()
        except Exception:
            self._state, self._active = RecordingState.IDLE, None
            raise
        active.countdown_remaining = 0
        self._state = RecordingState.RECORDING
        self._emit(RecorderEventKind.STARTED)

    def _plan(self, active: _Active) -> RecordingPlan:
        request = active.request
        output_dir = request.output_dir or self._settings.get().output_dir
        return self._planner(active.target, request.region, request.fps, request.audio, output_dir)

    def _cancel_countdown(self) -> None:
        assert self._active is not None
        self._active.cancelled.set()
        self._state, self._active = RecordingState.IDLE, None
        self._emit(RecorderEventKind.CANCELLED)

    def _finalizing(self) -> _Active:
        assert self._active is not None
        self._state = RecordingState.FINALIZING
        return self._active

    def _finish(self, active: _Active) -> RecordingInfo:
        """Close the pipeline (outside the lock: ffmpeg/mux can take seconds) and log it."""
        assert active.session is not None
        output = active.session.stop()
        return self._library.register(
            active.recording_id, output, active.target.label, active.request.started_by
        )

    def _discard(self, active: _Active) -> None:
        assert active.session is not None and active.plan is not None
        try:
            active.session.stop()
        except Exception:  # the partial recording is being thrown away anyway
            pass
        for leftover in _partial_files(active.plan):
            leftover.unlink(missing_ok=True)

    def _reset_to_idle(self) -> None:
        with self._lock:
            self._state, self._active = RecordingState.IDLE, None

    def _reset_after_failure(self, error: Exception) -> None:
        self._reset_to_idle()
        self._emit(RecorderEventKind.FAILED, message=str(error))

    def _status_now(self) -> RecordingStatus:
        active = self._active
        if active is None:
            return RecordingStatus(state=self._state)
        session = active.session
        return RecordingStatus(
            state=self._state,
            recording_id=active.recording_id,
            target_label=active.target.label,
            region=active.request.region,
            elapsed_seconds=0.0 if session is None else session.content_seconds(),
            countdown_remaining=active.countdown_remaining,
            audio=active.request.audio,
            started_by=active.request.started_by,
            output=None if active.plan is None else active.plan.output,
            target_closed=session is not None and session.target_closed.is_set(),
        )

    def _emit(
        self, kind: RecorderEventKind, info: RecordingInfo | None = None, message: str | None = None
    ) -> None:
        with self._lock:
            event = RecorderEvent(kind, self._status_now(), info, message)
        self._bus.publish(event)


def _validate_delay(delay_seconds: int) -> None:
    if not 0 <= delay_seconds <= MAX_DELAY_SECONDS:
        raise ValueError(
            f"delay_seconds={delay_seconds} is invalid; expected an integer 0..{MAX_DELAY_SECONDS}"
        )


def _partial_files(plan: RecordingPlan) -> list[Path]:
    log = plan.output.with_suffix(".ffmpeg.log")
    audio = [] if plan.audio_path is None else [plan.audio_path]
    return [plan.output, plan.video_only_path, log, *audio]
