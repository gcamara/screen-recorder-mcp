"""Shared fixtures: a headless QApplication for the UI tests (QT_QPA_PLATFORM=offscreen)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def qapp():  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QApplication

    from screen_recorder_mcp.ui.theme_controller import load_bundled_fonts

    app = QApplication.instance() or QApplication(["tests"])
    app.setQuitOnLastWindowClosed(False)
    load_bundled_fonts()
    return app


@pytest.fixture()
def theme(qapp):  # type: ignore[no-untyped-def]
    from screen_recorder_mcp.ui.theme_controller import ThemeController

    return ThemeController(qapp, "dark")
