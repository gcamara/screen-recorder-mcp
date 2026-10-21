"""Named fakes for the recorder's I/O boundaries."""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import numpy as np

from screen_recorder_mcp.capture import AudioMode, ClosedCallback, FrameCallback
from screen_recorder_mcp.geometry import FrameArray, Rect
from screen_recorder_mcp.media_tools import MediaProbe
from screen_recorder_mcp.recorder import RecordingParts, RecordingPlan, RecordingSession
from screen_recorder_mcp.service import RecorderEvent, RecorderEventKind
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
