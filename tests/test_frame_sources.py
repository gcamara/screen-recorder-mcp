import threading

from screen_recorder_mcp.frame_sources import FallbackFrameSource, PollingFrameSource
from tests.fakes import FakeFrameSource, GpuOutOfMemoryFrameSource, solid_frame


def test_fallback_uses_cpu_when_gpu_cannot_start() -> None:
    gpu, cpu = GpuOutOfMemoryFrameSource(None), FakeFrameSource(solid_frame(4, 4))
    frames = []
    source = FallbackFrameSource(gpu, cpu)
    source.start(frames.append, lambda: None)
    source.stop()
    assert source.active is cpu and len(frames) == 1 and cpu.stopped


def test_fallback_keeps_gpu_when_it_starts() -> None:
    gpu, cpu = FakeFrameSource(solid_frame(4, 4)), FakeFrameSource(solid_frame(4, 4))
    source = FallbackFrameSource(gpu, cpu)
    source.start(lambda _frame: None, lambda: None)
    assert source.active is gpu and cpu.closed_sink is None


def test_polling_source_delivers_frames_then_reports_closed_window() -> None:
    alive = iter([True, True, False])
    frames, closed = [], threading.Event()
    source = PollingFrameSource(lambda: solid_frame(2, 2), lambda: next(alive), 0.001)
    source.start(frames.append, closed.set)
    assert closed.wait(2)
    source.stop()
    assert len(frames) == 2


def test_polling_source_skips_missing_frames() -> None:
    alive = iter([True, False])
    frames, closed = [], threading.Event()
    source = PollingFrameSource(lambda: None, lambda: next(alive), 0.001)
    source.start(frames.append, closed.set)
    assert closed.wait(2) and frames == []


def test_stretch_nearest_scales_dpi_unaware_paint() -> None:
    import pytest

    gdi = pytest.importorskip("screen_recorder_mcp.backends.windows.gdi_capture")
    frame = solid_frame(8, 4)
    frame[0, 0, 0] = 99
    stretched = gdi.stretch_nearest(frame, 10, 5)
    assert stretched.shape == (5, 10, 4) and stretched[0, 0, 0] == 99 and stretched[4, 9, 0] == 7
