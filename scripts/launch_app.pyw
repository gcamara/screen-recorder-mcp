"""Start the desktop app with no console window: the target of a desktop / Start menu shortcut.

Run it with the BASE interpreter's pythonw.exe (the `home` in .venv/pyvenv.cfg), e.g.
    pythonw.exe C:\\path\\to\\screen-recorder-mcp\\scripts\\launch_app.pyw
The venv's own Scripts\\pythonw.exe is a launcher that starts the console python.exe, which
opens a terminal window next to the app; the venv's packages are added here instead.
"""

import runpy
import site
from pathlib import Path

site.addsitedir(str(Path(__file__).resolve().parent.parent / ".venv" / "Lib" / "site-packages"))
runpy.run_module("screen_recorder_mcp.ui.app", run_name="__main__")
