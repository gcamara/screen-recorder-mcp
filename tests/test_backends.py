import pytest

from screen_recorder_mcp.backends import backend_for
from screen_recorder_mcp.proc import hidden_window_flags


def test_unsupported_platform_names_it() -> None:
    with pytest.raises(NotImplementedError, match="'linux'"):
        backend_for("linux")


def test_windows_backend_selected_on_win32() -> None:
    pytest.importorskip("windows_capture")
    assert backend_for("win32").name == "windows"


def test_hidden_window_flags_is_int() -> None:
    assert isinstance(hidden_window_flags(), int)
