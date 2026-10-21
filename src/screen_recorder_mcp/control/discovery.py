"""Discovery file (control.json): how other processes find the running app's control server."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

DISCOVERY_FILENAME = "control.json"
_WINDOWS_QUERY_LIMITED = 0x1000
_WINDOWS_STILL_ACTIVE = 259


@dataclass(frozen=True)
class ControlDiscovery:
    port: int
    token: str
    pid: int
    version: str


def discovery_path(data_dir: Path) -> Path:
    return data_dir / DISCOVERY_FILENAME


def write_discovery(data_dir: Path, discovery: ControlDiscovery) -> Path:
    """Atomically write control.json, readable by the current user only where the OS allows.

    Example: write_discovery(Path("/tmp/app"), ControlDiscovery(5123, "tok", 42, "0.1.0"))
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    target = discovery_path(data_dir)
    # mkstemp creates the file 0600 on POSIX; on Windows %APPDATA% is already per-user.
    descriptor, temp_name = tempfile.mkstemp(dir=data_dir, prefix="control-", suffix=".tmp")
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(asdict(discovery), handle)
    os.replace(temp_name, target)
    return target


def read_discovery(data_dir: Path) -> ControlDiscovery | None:
    """The parsed file, or None when it is missing or malformed."""
    try:
        raw = json.loads(discovery_path(data_dir).read_text(encoding="utf-8"))
        return ControlDiscovery(int(raw["port"]), str(raw["token"]), int(raw["pid"]), str(raw["version"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def remove_discovery(data_dir: Path, owner_pid: int) -> None:
    """Delete control.json only if this process wrote it (a newer app instance may own it now)."""
    current = read_discovery(data_dir)
    if current is None or current.pid != owner_pid:
        return
    discovery_path(data_dir).unlink(missing_ok=True)


def pid_alive(pid: int) -> bool:
    """Whether a process exists. Never os.kill on Windows: any non-CTRL signal terminates it."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_pid_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(_WINDOWS_QUERY_LIMITED, False, pid)
    if not handle:
        return False
    code = wintypes.DWORD()
    ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    kernel32.CloseHandle(handle)
    return bool(ok) and code.value == _WINDOWS_STILL_ACTIVE
