import threading

import numpy as np
import pytest

from screen_recorder_mcp.frame_pacer import FramePacer, LatestFrameSlot, frames_due
from screen_recorder_mcp.geometry import Rect, clamp_crop, crop_frame, even_floor, fit_frame
from tests.fakes import FakeEncoder, solid_frame


def test_even_floor_rounds_odd_down() -> None:
    assert (even_floor(601), even_floor(600)) == (600, 600)


def test_clamp_crop_clips_to_frame_and_evens_size() -> None:
    assert clamp_crop(Rect(-5, 0, 101, 50), 80, 41) == Rect(0, 0, 80, 40)


def test_clamp_crop_outside_frame_names_the_crop() -> None:
    with pytest.raises(ValueError, match="'x': 500"):
        clamp_crop(Rect(500, 500, 10, 10), 100, 100)


def test_crop_frame_slices_region() -> None:
    frame = np.arange(10 * 10 * 4, dtype=np.uint8).reshape(10, 10, 4)
    assert crop_frame(frame, Rect(2, 3, 4, 5)).shape == (5, 4, 4)


def test_fit_frame_pads_and_trims_to_locked_size() -> None:
    fitted = fit_frame(solid_frame(12, 10), 8, 14)
    assert fitted.shape == (14, 8, 4)
    assert fitted[13, 0, 0] == 0 and fitted[0, 0, 0] == 7


def test_frames_due_keeps_pace_with_clock() -> None:
    assert frames_due(0.0, 30, 0) == 1
    assert frames_due(1.0, 30, 25) == 6
    assert frames_due(0.5, 30, 40) == 0


def test_pacer_repeats_latest_frame_for_static_window() -> None:
    # A static window yields one WGC frame, but 1 s at 10 fps must be 11 frames (t=0..1.0).
    slot, encoder = LatestFrameSlot(), FakeEncoder()
    slot.put(solid_frame(6, 4))
    pacer = FramePacer(slot, encoder.write, 10, Rect(0, 0, 4, 2), (4, 2))
    pacer.tick(1.0)
    assert len(encoder.frames) == 11 and encoder.frames[0].shape == (2, 4, 4)


def test_pacer_run_flushes_on_stop() -> None:
    slot, encoder = LatestFrameSlot(), FakeEncoder()
    slot.put(solid_frame(4, 4))
    times = iter([0.0, 0.2])
    pacer = FramePacer(slot, encoder.write, 10, None, (4, 4), clock=lambda: next(times))
    stop = threading.Event()
    stop.set()
    pacer.run(stop, origin=0.0)
    assert pacer.frames_written == 1
