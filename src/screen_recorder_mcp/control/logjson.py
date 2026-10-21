"""Structured JSON logging to stderr. stdout is the MCP protocol channel: never print there."""

from __future__ import annotations

import json
import logging
import sys

_LOGGER_NAME = "screen_recorder_mcp.control"


def log_json(event: str, level: int = logging.INFO, **fields: object) -> None:
    """Emit one JSON line on stderr.

    Example: log_json("control_started", port=5123) -> {"event": "control_started", "port": 5123}
    """
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        _attach_stderr_handler(logger)
    logger.log(level, json.dumps({"event": event, **fields}, default=str))


def _attach_stderr_handler(logger: logging.Logger) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
