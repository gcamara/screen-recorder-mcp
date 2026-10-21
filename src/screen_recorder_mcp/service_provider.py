"""Chooses, per MCP call, which RecorderService to use: the running desktop app or in-process."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from screen_recorder_mcp.control.client import RemoteRecorderService, discover_running_app
from screen_recorder_mcp.control.discovery import ControlDiscovery
from screen_recorder_mcp.service import RecorderService, RecordingState

Discover = Callable[[Path], ControlDiscovery | None]
RemoteFactory = Callable[[ControlDiscovery], RecorderService]
LocalFactory = Callable[[], RecorderService]


class ServiceProvider:
    """Remote when the app answers, else a lazily built local service.

    A local service that is mid-recording stays preferred, so a recording started
    headless is never orphaned when the app launches halfway through it.
    Example: ServiceProvider(app_data_dir(), build_local_service).get().list_targets()
    """

    def __init__(
        self,
        data_dir: Path,
        local_factory: LocalFactory,
        discover: Discover = discover_running_app,
        remote_factory: RemoteFactory = RemoteRecorderService,
    ) -> None:
        self._data_dir = data_dir
        self._local_factory = local_factory
        self._discover = discover
        self._remote_factory = remote_factory
        self._local: RecorderService | None = None
        self._lock = threading.Lock()

    def get(self) -> RecorderService:
        with self._lock:
            if self._local is not None and self._local.status().state != RecordingState.IDLE:
                return self._local
            discovery = self._discover(self._data_dir)
            if discovery is not None:
                return self._remote_factory(discovery)
            return self._local_service()

    def _local_service(self) -> RecorderService:
        if self._local is None:
            self._local = self._local_factory()
        return self._local
