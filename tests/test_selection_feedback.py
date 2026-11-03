"""Selection toolbar: Mouse / Keys input feedback and Cancel, and how the choice reaches the request."""

from pathlib import Path

from screen_recorder_mcp.capture import AudioMode
from screen_recorder_mcp.input_overlay import KeyMode, OverlayOptions
from screen_recorder_mcp.service import RecorderSettings
from screen_recorder_mcp.ui.selection_toolbar import KEY_BUTTON, SelectionToolbar
from screen_recorder_mcp.ui.view_models import build_request


def test_mouse_toggle_switches_halo_and_clicks_together(qapp, theme) -> None:  # type: ignore[no-untyped-def]
    bar = SelectionToolbar(theme)
    seen: list[OverlayOptions] = []
    bar.overlay_changed.connect(seen.append)
    bar._mouse.setChecked(True)  # noqa: SLF001
    bar._mouse.setChecked(False)  # noqa: SLF001
    assert seen == [OverlayOptions(True, True, KeyMode.OFF), OverlayOptions()]


def test_keys_menu_sets_mode_and_label(qapp, theme) -> None:  # type: ignore[no-untyped-def]
    bar = SelectionToolbar(theme, overlay=OverlayOptions(cursor_halo=True, show_clicks=True))
    assert bar._mouse.isChecked() and bar._keys.text() == KEY_BUTTON[KeyMode.OFF]  # noqa: SLF001
    bar._key_actions[KeyMode.ALL].trigger()  # noqa: SLF001
    assert bar.overlay() == OverlayOptions(True, True, KeyMode.ALL) and bar._keys.text() == "Keys: all"  # noqa: SLF001


def test_cancel_closes_the_selection(qapp, theme) -> None:  # type: ignore[no-untyped-def]
    bar = SelectionToolbar(theme)
    closed: list[bool] = []
    bar.closed.connect(lambda: closed.append(True))
    cancel = next(b for b in bar.findChildren(type(bar._mouse)) if b.text() == "Cancel")  # noqa: SLF001
    cancel.click()
    assert closed == [True]


def test_ui_requests_carry_the_settings_overlay() -> None:
    settings = RecorderSettings(output_dir=Path("v"), highlight_cursor=True, show_keys=KeyMode.SHORTCUTS)
    request = build_request("monitor:1", None, settings, AudioMode.NONE, 0)
    assert request.overlay == OverlayOptions(True, False, KeyMode.SHORTCUTS)
