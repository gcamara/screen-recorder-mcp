"""Small JSON persistence helpers shared by the history and settings stores."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

_log = logging.getLogger(__name__)


def read_json_object(path: Path) -> dict[str, object]:
    """Load a JSON object; a missing or corrupt file yields {} so the app still starts.

    Example: read_json_object(Path("settings.json")).get("theme")
    """
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        _log.warning('{"event": "json_unreadable", "path": "%s", "error": "%s"}', path.as_posix(), error)
        return {}
    return loaded if isinstance(loaded, dict) else {}


def write_json_atomic(path: Path, document: object) -> None:
    """Write via a temp file + replace so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_suffix(path.suffix + ".tmp")
    scratch.write_text(json.dumps(document, indent=2), encoding="utf-8")
    os.replace(scratch, path)
