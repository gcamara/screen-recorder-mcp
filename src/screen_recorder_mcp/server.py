"""MCP stdio server exposing the screen recorder to AI clients."""

from __future__ import annotations

import logging
import sys
from typing import Callable

from mcp.server.mcpserver import MCPServer

from screen_recorder_mcp.region_picker import TkRegionPicker
from screen_recorder_mcp.service import RecorderService, app_data_dir
from screen_recorder_mcp.service_provider import ServiceProvider
from screen_recorder_mcp.tools import RecorderTools

INSTRUCTIONS = """\
Screen/app video recorder for Windows and macOS. Workflow you MUST follow:
1. Target: if the user named a screen, app, system or site, pass it as `target`
   (a site means the browser window showing it). Otherwise call list_targets and
   ASK the user which window or monitor to record. Never guess.
2. Crop: unless the user already said which part to record (or said "all"),
   ASK whether they want to crop a portion of that app/screen. If yes, call
   pick_region (the user drags a rectangle on screen) and pass its result as
   `region` to start_recording.
3. Capture: keep capture="auto" (GPU, automatic CPU fallback). Use "cpu" when the
   user says the GPU is busy or asks for CPU recording (Windows only).
4. Audio: keep audio="none" unless the user explicitly asked for sound
   ("system" = on Windows everything the PC plays, on macOS only the recorded
   app's sound, or every app's for a monitor; "mic" = microphone).
5. Control: pause_recording / resume_recording / cancel_recording (deletes the
   partial file) act on the running recording; stop_recording returns the MP4 path.
   Use delay_seconds on start_recording only when the user wants a countdown.
6. After recording: list_recordings shows the history (also recordings the user
   made by hand in the desktop app); trim_recording cuts a new file from one and
   export_gif converts one to a GIF. Both keep the original.
When the desktop app is running, recordings you start appear in its toolbar so the
user can always see, pause or stop them; otherwise recording runs inside this server.
Window recordings only contain that app: windows moved in front of it are not
recorded. A minimized window produces no frames. On macOS the host app needs
the Screen Recording permission (System Settings > Privacy & Security).
"""

_TOOL_NAMES: tuple[str, ...] = (
    "list_targets",
    "pick_region",
    "start_recording",
    "recording_status",
    "pause_recording",
    "resume_recording",
    "stop_recording",
    "cancel_recording",
    "list_recordings",
    "trim_recording",
    "export_gif",
)


def build_local_service() -> RecorderService:
    """In-process service for when the desktop app is not running.

    Imported lazily: service_local is heavy and only needed in headless mode.
    """
    from screen_recorder_mcp.backends import current_backend
    from screen_recorder_mcp.service_local import LocalRecorderService

    backend = current_backend()
    backend.prepare_process()
    return LocalRecorderService(backend, TkRegionPicker())


def build_server(get_service: Callable[[], RecorderService]) -> MCPServer:
    """MCP server whose tools resolve their RecorderService on every call.

    Example: build_server(lambda: fake_service).run()
    """
    server = MCPServer("screen-recorder", instructions=INSTRUCTIONS)
    tools = RecorderTools(get_service)
    for name in _TOOL_NAMES:
        server.add_tool(getattr(tools, name), name=name)
    return server


mcp = build_server(ServiceProvider(app_data_dir(), build_local_service).get)


def main() -> None:
    # stdout is the MCP channel: logs go to stderr only.
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")
    mcp.run()


if __name__ == "__main__":
    main()
