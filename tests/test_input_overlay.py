"""Input feedback burned into frames: key chips, geometry, drawing, pipeline wiring, settings, MCP."""

import sys
from pathlib import Path

import numpy as np
import pytest

from screen_recorder_mcp.frame_pacer import FramePacer, LatestFrameSlot
from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.input_overlay import (
    CHIP_SECONDS, Click, FrameMapping, InputLog, InputSnapshot, KeyChips, KeyMode, OverlayOptions,
    OverlayPipeline, OverlayRenderer, blend_disc, chip_alpha,
)
from screen_recorder_mcp.service import RecordingRequest
from screen_recorder_mcp.session_factory import _overlay_pipeline
from screen_recorder_mcp.settings_store import validate_changes
from screen_recorder_mcp.tools import RecorderTools
from tests.fakes import FakeEncoder, FakeRecorderService, FakeSessionBuilder, solid_frame

ALL = OverlayOptions(cursor_halo=True, show_clicks=True, show_keys=KeyMode.ALL)


def texts(chips: KeyChips, now: float) -> list[str]:
    return [chip.label for chip in chips.visible(now)]


def test_shortcuts_mode_shows_combos_and_function_keys_only() -> None:
    chips = KeyChips(KeyMode.SHORTCUTS)
    chips.press("C", {"Ctrl"}, 1.0, "c")
    chips.press("H", set(), 1.1, "h")  # plain typing is not a shortcut
    chips.press("A", {"Shift"}, 1.2, "A")
    chips.press("F5", set(), 1.3)
    chips.press("Tab", {"Alt"}, 1.4)
    assert texts(chips, 1.5) == ["Ctrl + C", "F5", "Alt + Tab"]


def test_all_mode_merges_typing_and_names_special_keys() -> None:
    chips = KeyChips(KeyMode.ALL)
    for i, char in enumerate("hi!"):
        chips.press(char.upper(), set(), 1.0 + i * 0.1, char)
    chips.press("Enter", set(), 1.4)
    chips.press("S", {"Ctrl", "Shift"}, 1.5, "S")
    assert texts(chips, 1.6) == ["hi!", "Enter", "Ctrl + Shift + S"]


def test_repeats_collapse_and_chips_expire() -> None:
    chips = KeyChips(KeyMode.SHORTCUTS)
    for i in range(3):
        chips.press("Z", {"Ctrl"}, 1.0 + i * 0.2)
    assert texts(chips, 1.5) == ["Ctrl + Z ×3"]
    assert texts(chips, 1.4 + CHIP_SECONDS + 0.01) == []


def test_typing_pause_starts_a_new_chip_and_off_mode_records_nothing() -> None:
    chips = KeyChips(KeyMode.ALL)
    chips.press("A", set(), 1.0, "a")
    chips.press("B", set(), 2.5, "b")
    assert texts(chips, 2.6) == ["a", "b"]
    off = KeyChips(KeyMode.OFF)
    off.press("C", {"Ctrl"}, 1.0)
    assert texts(off, 1.1) == []


def test_chip_alpha_fades_over_the_last_third() -> None:
    assert chip_alpha(0.1) == 1.0 and chip_alpha(CHIP_SECONDS * 2 / 3) == 1.0
    assert 0 < chip_alpha(CHIP_SECONDS * 5 / 6) < 1 and chip_alpha(CHIP_SECONDS) == 0.0


def test_input_log_tracks_buttons_and_drops_old_clicks() -> None:
    log = InputLog(ALL, lambda: (10, 20))
    log.mouse_down("left", 10, 20, now=1.0)
    snap = log.snapshot(1.1)
    assert snap.cursor == (10, 20) and snap.buttons_down == {"left"} and len(snap.clicks) == 1
    log.mouse_up("left")
    assert log.snapshot(5.0).clicks == [] and log.snapshot(5.0).buttons_down == frozenset()
    log.key_down("Ctrl")
    log.key_down("V", "v", now=5.0)
    log.key_up("Ctrl")
    assert [c.label for c in log.snapshot(5.1).chips] == ["Ctrl + V"]


def test_mapping_follows_a_moving_window_and_crop() -> None:
    rect = {"now": Rect(100, 50, 800, 600)}
    mapping = FrameMapping(lambda: rect["now"], (1600, 1200), Rect(200, 100, 800, 600))
    assert mapping.to_output(500, 350) == (600.0, 500.0, 2.0)
    rect["now"] = Rect(0, 0, 800, 600)  # the window moved; same screen point lands elsewhere
    assert mapping.to_output(500, 350) == (800.0, 600.0, 2.0)


def test_blend_disc_paints_inside_and_clips_at_edges() -> None:
    frame = np.zeros((40, 40, 4), np.uint8)
    blend_disc(frame, 20, 20, 8, (0, 0, 255), 1.0)
    assert tuple(frame[20, 20, :3]) == (0, 0, 255) and tuple(frame[2, 2, :3]) == (0, 0, 0)
    blend_disc(frame, -5, 39, 10, (255, 0, 0), 0.5)  # half outside: no error
    ring = np.zeros((40, 40, 4), np.uint8)
    blend_disc(ring, 20, 20, 15, (0, 255, 0), 1.0, ring=3)
    assert ring[20, 20, 1] == 0 and ring[20, 34, 1] > 200  # hollow centre, painted rim


def test_renderer_draws_halo_ripple_and_chips_where_expected() -> None:
    mapping = FrameMapping(lambda: Rect(0, 0, 320, 180), (320, 180), None)
    renderer = OverlayRenderer(ALL, mapping, (320, 180))
    frame = np.zeros((180, 320, 4), np.uint8)
    chips = KeyChips(KeyMode.ALL)
    chips.press("C", {"Ctrl"}, 1.0)
    snap = InputSnapshot((60, 60), frozenset(), [Click(0.9, 250, 60, "right")], chips.visible(1.0))
    renderer.draw(frame, snap, 1.0)
    assert frame[60, 60, :3].any()  # halo under the pointer
    # 0.1 s into a 0.45 s ripple the ring is ~17 px out (10 + 34 * 0.22), 4 px wide, hollow inside.
    assert frame[60, 250 + 16, :3].any() and not frame[60, 250, :3].any()
    assert frame[150:175, 100:220, :3].any()  # chip near the bottom centre
    assert not frame[5, 5, :3].any()


def test_pipeline_draws_on_a_copy_and_skips_when_idle() -> None:
    class Tracker:
        def __init__(self, snap: InputSnapshot) -> None:
            self.snap, self.started, self.stopped = snap, False, False

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.stopped = True

        def snapshot(self, now: float) -> InputSnapshot:
            return self.snap

    busy = Tracker(InputSnapshot((10, 10), frozenset(), [], []))
    pipeline = OverlayPipeline(ALL, busy, lambda: Rect(0, 0, 40, 40), clock=lambda: 1.0)
    decorate = pipeline.bind((40, 40), None, (40, 40))
    original = np.zeros((40, 40, 4), np.uint8)
    drawn = decorate(original)
    assert drawn is not original and drawn[10, 10, :3].any() and not original.any()
    idle = OverlayPipeline(ALL, Tracker(InputSnapshot(None, frozenset(), [], [])), lambda: Rect(0, 0, 40, 40))
    assert idle.bind((40, 40), None, (40, 40))(original) is original


def test_pacer_decorates_once_per_tick() -> None:
    slot, encoder, calls = LatestFrameSlot(), FakeEncoder(), []
    slot.put(solid_frame(4, 4))

    def decorate(frame: np.ndarray) -> np.ndarray:
        calls.append(frame.shape)
        return frame

    pacer = FramePacer(slot, encoder.write, 10, None, (4, 4), decorate=decorate)
    pacer.tick(1.0)
    pacer.tick(1.0)  # nothing due: no extra decoration
    assert len(encoder.frames) == 11 and calls == [(4, 4, 4)]


def test_no_pipeline_when_every_option_is_off() -> None:
    assert _overlay_pipeline(object(), object(), None) is None  # type: ignore[arg-type]
    assert _overlay_pipeline(object(), object(), OverlayOptions()) is None  # type: ignore[arg-type]


def test_settings_validate_show_keys() -> None:
    assert validate_changes({"show_keys": "shortcuts", "highlight_cursor": True}) == {
        "show_keys": KeyMode.SHORTCUTS, "highlight_cursor": True,
    }
    with pytest.raises(ValueError, match="one of off, shortcuts, all"):
        validate_changes({"show_keys": "loud"})


def test_start_recording_overrides_only_given_options() -> None:
    service = FakeRecorderService()
    tools = RecorderTools(lambda: service)
    tools.start_recording("Notepad")
    service.stop()
    tools.start_recording("Notepad", show_clicks=True, show_keys="shortcuts")
    assert service.requests[0].overlay is None  # app settings decide
    assert service.requests[1].overlay == OverlayOptions(False, True, KeyMode.SHORTCUTS)
    service.stop()
    with pytest.raises(ValueError, match="show_keys must be one of"):
        tools.start_recording("Notepad", show_keys="everything")


def test_service_passes_settings_overlay_unless_the_request_has_one(tmp_path: Path) -> None:
    from tests.test_service_local import Rig, request, wait_until

    rig = Rig(tmp_path)
    rig.service.update_settings({"highlight_cursor": True})
    rig.service.start(request())
    rig.service.stop()
    rig.service.start(request(overlay=OverlayOptions(show_keys=KeyMode.ALL)))
    rig.service.stop()
    assert rig.builder.overlays == [OverlayOptions(cursor_halo=True), OverlayOptions(show_keys=KeyMode.ALL)]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows input hooks")
def test_windows_key_names_and_hook_thread_lifecycle() -> None:
    from screen_recorder_mcp.backends.windows.input_hooks import WindowsInputTracker, key_name

    assert [key_name(vk) for vk in (0x43, 0x74, 0x0D, 0xA2, 0x5B, 0x31)] == ["C", "F5", "Enter", "Ctrl", "Win", "1"]
    tracker = WindowsInputTracker(OverlayOptions(show_clicks=True, show_keys=KeyMode.SHORTCUTS))
    tracker.start()
    assert tracker._thread is not None and tracker._thread.is_alive()  # noqa: SLF001
    assert tracker.snapshot(0.0).cursor is not None
    tracker.stop()
    assert tracker._thread is None  # noqa: SLF001
