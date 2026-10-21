import pytest

from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.targets import resolve_target
from screen_recorder_mcp.window_registry import find_window
from tests.fakes import FakeWindowLister, window

LISTER = FakeWindowLister(
    [
        window(11, "Inbox - Google Chrome", "chrome.exe"),
        window(12, "Docs - Google Chrome", "chrome.exe"),
        window(13, "notes.txt - Notepad", "Notepad.exe"),
        window(14, "Chrome tips - Notepad", "Notepad.exe"),
    ]
)
MONITORS = [Rect(0, 0, 1920, 1080), Rect(1920, 0, 2560, 1440)]


def test_find_by_process_without_exe_picks_front_most() -> None:
    assert find_window(LISTER, "CHROME").window_id == 11


def test_find_by_window_id() -> None:
    assert find_window(LISTER, "13").title == "notes.txt - Notepad"


def test_find_by_title_substring_for_a_site() -> None:
    assert find_window(LISTER, "docs").window_id == 12


def test_title_substring_across_processes_is_ambiguous() -> None:
    with pytest.raises(LookupError, match="ambiguous"):
        find_window(LISTER, " - ")


def test_unknown_window_names_the_query() -> None:
    with pytest.raises(LookupError, match="'spotify'"):
        find_window(LISTER, "spotify")


def test_resolve_monitor_is_one_based() -> None:
    target = resolve_target("monitor:2", LISTER, lambda: MONITORS)
    assert target.monitor_index == 2 and target.rect.width == 2560 and target.window_id is None


def test_resolve_bad_monitor_lists_range() -> None:
    with pytest.raises(LookupError, match="monitor:1..monitor:2"):
        resolve_target("monitor:3", LISTER, lambda: MONITORS)


def test_resolve_window_carries_window_id() -> None:
    assert resolve_target("notepad", LISTER, lambda: MONITORS).window_id == 13


def test_find_by_mac_app_suffix() -> None:
    lister = FakeWindowLister([window(7, "Untitled", "Safari.app")])
    assert find_window(lister, "safari").window_id == 7


def test_find_by_process_substring_for_mac_owner_names() -> None:
    lister = FakeWindowLister([window(9, "", "Google Chrome")])
    assert find_window(lister, "chrome").window_id == 9
