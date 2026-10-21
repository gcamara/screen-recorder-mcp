"""MCP stdio server exposing the screen recorder to AI clients."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from screen_recorder_mcp.backends import current_backend
from screen_recorder_mcp.capture import AudioMode
from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.recorder import RecordingSession
from screen_recorder_mcp.region_picker import pick_region_interactively
from screen_recorder_mcp.session_factory import DEFAULT_OUTPUT_DIR, build_session, plan_recording
from screen_recorder_mcp.targets import CaptureTarget, resolve_target

INSTRUCTIONS = """\
Screen/app video recorder for Windows and macOS. Workflow you MUST follow:
1. Target: if the user named a screen, app, system or site, pass it as `target`
   (a site means the browser window showing it). Otherwise call list_targets and
   ASK the user which window or monitor to record. Never guess.
2. Crop: unless the user already said which part to record (or said "all"),
   ASK whether they want to crop a portion of that app/screen. If yes, call
   pick_region (the user drags a rectangle on screen) and pass its result as
   `region` to start_recording.
3. Audio: keep audio="none" unless the user explicitly asked for sound
   ("system" = on Windows everything the PC plays, on macOS only the recorded
   app's sound, or every app's for a monitor; "mic" = microphone).
Window recordings only contain that app: windows moved in front of it are not
recorded. A minimized window produces no frames. On macOS the host app needs
the Screen Recording permission (System Settings > Privacy & Security).
"""

mcp = MCPServer("screen-recorder", instructions=INSTRUCTIONS)
_backend = current_backend()
_active: dict[str, RecordingSession] = {}


def _resolve(target: str) -> CaptureTarget:
    return resolve_target(target, _backend.windows, _backend.monitor_rects)


@mcp.tool()
def list_targets() -> dict[str, object]:
    """List recordable windows (front-most first) and monitors (`monitor:N`)."""
    monitors = [
        {"target": f"monitor:{index}", "rect": rect.as_dict()}
        for index, rect in enumerate(_backend.monitor_rects(), start=1)
    ]
    return {"windows": [w.as_dict() for w in _backend.windows.list_windows()], "monitors": monitors}


@mcp.tool()
def pick_region(target: str) -> dict[str, object]:
    """Show a drag-to-crop overlay over the target so the USER selects the area.

    target: window title / process name / window_id, or `monitor:N`.
    Returns `region` relative to the target (null = user pressed Esc, record all).
    The OS may refuse to bring the app to the front; tell the user to keep the
    app visible before calling this, since the overlay is semi-transparent.
    """
    resolved = _resolve(target)
    if resolved.window_id is not None:
        _backend.bring_to_front(resolved)
        resolved = _resolve(str(resolved.window_id))  # rect may change after restore
    region = pick_region_interactively(resolved.rect)
    return {"target": resolved.as_dict(), "region": None if region is None else region.as_dict()}


@mcp.tool()
def start_recording(
    target: str,
    region: dict[str, int] | None = None,
    fps: int = 30,
    audio: str = "none",
    capture_cursor: bool = True,
    output_dir: str | None = None,
) -> dict[str, object]:
    """Start recording a window or monitor to MP4 (H.264).

    target: window title / process name / window_id, or `monitor:N` (see list_targets).
    region: optional {x, y, width, height} relative to the target, usually from pick_region.
    audio: "none" (default), "system" or "mic" — only when the user asked for audio.
    """
    if _active:
        raise RuntimeError("a recording is already running; call stop_recording first")
    mode = AudioMode(audio)
    crop = None if region is None else Rect(**region)
    plan = plan_recording(_resolve(target), crop, fps, mode, _output_dir(output_dir))
    session = build_session(_backend, plan, mode, capture_cursor)
    session.start()
    _active["current"] = session
    return session.status()


@mcp.tool()
def recording_status() -> dict[str, object]:
    """Report the running recording (elapsed time, frames, whether the target closed)."""
    session = _active.get("current")
    return {"recording": False} if session is None else {"recording": True, **session.status()}


@mcp.tool()
def stop_recording() -> dict[str, object]:
    """Stop the running recording and return the final MP4 path."""
    session = _active.pop("current", None)
    if session is None:
        raise RuntimeError("no recording is running; call start_recording first")
    status = session.status()
    output = session.stop()
    return {**status, "output": str(output), "size_bytes": output.stat().st_size}


def _output_dir(raw: str | None) -> Path:
    return DEFAULT_OUTPUT_DIR if raw is None else Path(raw).expanduser()


def main() -> None:
    # stdout is the MCP channel: logs go to stderr only.
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")
    _backend.prepare_process()
    mcp.run()


if __name__ == "__main__":
    main()
