"""Drag-to-select overlay that lets the user crop part of a window or monitor.

Runs in its own process (`python -m screen_recorder_mcp.region_picker x y w h`)
because Tk needs the main thread and must never print into the MCP stdio
channel of the server. It prints one JSON line: {"region": {...} | null},
with the region relative to the overlaid rect.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tkinter as tk

from screen_recorder_mcp.backends import current_backend
from screen_recorder_mcp.geometry import Rect

# "mission control" palette: violet-ink dim, violet accent, near-white text.
_DIM_COLOR = "#0b0a10"
_ACCENT_COLOR = "#9d8cff"
_TEXT_COLOR = "#ecebf2"
_DIM_ALPHA = 0.45
_HINT = "Arraste para recortar  ·  Enter ou duplo clique confirma  ·  Esc ou botão direito = sem recorte"
_FONT_FAMILY = "Segoe UI" if sys.platform == "win32" else "Helvetica Neue"
_MIN_SIZE = 16


def normalize_drag(start: tuple[int, int], end: tuple[int, int]) -> Rect | None:
    """Rect spanned by a drag in any direction; None if too small to be intentional.

    Example: normalize_drag((50, 40), (10, 0)) -> Rect(10, 0, 40, 40)
    """
    left, right = sorted((start[0], end[0]))
    top, bottom = sorted((start[1], end[1]))
    if right - left < _MIN_SIZE or bottom - top < _MIN_SIZE:
        return None
    return Rect(left, top, right - left, bottom - top)


class RegionOverlay:
    """Fullscreen-over-target Tk window; result is set when the user finishes."""

    def __init__(self, bounds: Rect) -> None:
        self.result: Rect | None = None
        self._start: tuple[int, int] | None = None
        self._root = tk.Tk()
        self._canvas = tk.Canvas(self._root, bg=_DIM_COLOR, highlightthickness=0, cursor="crosshair")
        self._configure_window(bounds)
        self._selection = self._canvas.create_rectangle(0, 0, 0, 0, outline=_ACCENT_COLOR, width=3)
        self._draw_hint(bounds)
        self._bind_events()

    def run(self) -> Rect | None:
        self._root.mainloop()
        return self.result

    def _configure_window(self, bounds: Rect) -> None:
        self._root.overrideredirect(True)
        self._root.geometry(f"{bounds.width}x{bounds.height}+{bounds.x}+{bounds.y}")
        self._root.attributes("-topmost", True)
        self._root.attributes("-alpha", _DIM_ALPHA)
        self._canvas.pack(fill="both", expand=True)
        # Focus only sticks once the window is mapped (macOS ignores it earlier).
        self._root.after(50, self._take_focus)

    def _take_focus(self) -> None:
        self._root.lift()
        self._root.focus_force()

    def _draw_hint(self, bounds: Rect) -> None:
        self._canvas.create_text(
            bounds.width // 2, 28, text=_HINT, fill=_TEXT_COLOR, font=(_FONT_FAMILY, 14, "bold")
        )

    def _bind_events(self) -> None:
        self._canvas.bind("<ButtonPress-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_drag)
        # Mouse-only confirm/cancel too: an overrideredirect window may never
        # get keyboard focus on macOS. Right click is Button-2 on Tk/aqua.
        self._canvas.bind("<Double-Button-1>", lambda _event: self._root.destroy())
        self._root.bind("<Return>", lambda _event: self._root.destroy())
        self._root.bind("<Escape>", self._on_cancel)
        for right_button in ("<ButtonPress-2>", "<ButtonPress-3>"):
            self._canvas.bind(right_button, self._on_cancel)

    def _on_press(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        # Keep the previous rect until a drag replaces it, so the first click
        # of a confirming double-click doesn't wipe the selection.
        self._start = (event.x, event.y)

    def _on_drag(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        if self._start is None:
            return
        dragged = normalize_drag(self._start, (event.x, event.y))
        if dragged is None:
            return  # jitter (e.g. during a double-click) must not wipe the selection
        self._canvas.coords(self._selection, *self._start, event.x, event.y)
        self.result = dragged

    def _on_cancel(self, _event: tk.Event) -> None:  # type: ignore[type-arg]
        self.result = None
        self._root.destroy()


def pick_region_interactively(bounds: Rect, timeout_seconds: float = 120) -> Rect | None:
    """Launch the overlay process over `bounds` and wait for the user's choice.

    Example: pick_region_interactively(Rect(0, 0, 1920, 1080)) -> Rect(100, 80, 640, 360) or None
    """
    args = [str(value) for value in (bounds.x, bounds.y, bounds.width, bounds.height)]
    completed = subprocess.run(
        [sys.executable, "-m", "screen_recorder_mcp.region_picker", *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"region picker exited {completed.returncode}: {completed.stderr!r}")
    return parse_picker_output(completed.stdout.decode())


def parse_picker_output(stdout: str) -> Rect | None:
    region = json.loads(stdout.strip().splitlines()[-1])["region"]
    return None if region is None else Rect(**region)


def main(argv: list[str]) -> None:
    current_backend().prepare_process()
    x, y, width, height = (int(value) for value in argv)
    chosen = RegionOverlay(Rect(x, y, width, height)).run()
    print(json.dumps({"region": None if chosen is None else chosen.as_dict()}))


if __name__ == "__main__":
    main(sys.argv[1:])
