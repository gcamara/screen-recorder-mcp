"""Dev preview: the real UI against an in-memory demo service (no screen capture, no backends).

    uv run python -m screen_recorder_mcp.ui.preview                 # interactive
    uv run python -m screen_recorder_mcp.ui.preview --ai-after 6    # an "AI" recording starts after 6 s
    uv run python -m screen_recorder_mcp.ui.preview --shots docs/screenshots   # render PNGs and exit
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.ui.selection_session import screens_from_qt
from screen_recorder_mcp.ui.demo_service import DemoRecorderService
from screen_recorder_mcp.ui.shell import AppShell
from screen_recorder_mcp.ui.theme_controller import ThemeController, load_bundled_fonts

TICK_SECONDS = 0.1


def start_ticker(service: DemoRecorderService, parent: QApplication) -> QTimer:
    """Drive the demo clock from the GUI thread (countdown and recorded seconds advance)."""
    timer = QTimer(parent)
    timer.setInterval(int(TICK_SECONDS * 1000))
    timer.timeout.connect(lambda: service.tick(TICK_SECONDS))
    timer.start()
    return timer


def physical_monitors() -> list[Rect]:
    """The real screens as backend-style physical rects, so the selection overlay maps cleanly."""
    return [s.physical_size for s in screens_from_qt()]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="screen_recorder_mcp.ui.preview")
    parser.add_argument("--ai-after", type=float, default=0.0, help="seconds until a simulated AI recording starts")
    parser.add_argument("--shots", type=Path, default=None, help="write screenshots to this folder and exit")
    parser.add_argument("--empty", action="store_true", help="start without any history")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    load_bundled_fonts()
    theme = ThemeController(app)
    work = Path(tempfile.mkdtemp(prefix="screen-recorder-preview-"))
    service = DemoRecorderService(work, seed_history=not args.empty, monitors=physical_monitors())
    if args.shots is not None:
        from screen_recorder_mcp.ui.preview_shots import render_all

        return render_all(app, service, theme, work, args.shots)
    shell = AppShell(app, service, theme, work, with_hotkey=False)
    ticker = start_ticker(service, app)
    if args.ai_after > 0:
        QTimer.singleShot(int(args.ai_after * 1000), service.simulate_ai_start)
    shell.start()
    code = app.exec()
    ticker.stop()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
