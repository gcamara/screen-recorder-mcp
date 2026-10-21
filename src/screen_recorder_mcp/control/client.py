"""RecorderService client for the control server of a running desktop app."""

from __future__ import annotations

import http.client
import json
import logging
import threading
from pathlib import Path
from typing import Callable, Mapping

from screen_recorder_mcp.control import codec
from screen_recorder_mcp.control.discovery import ControlDiscovery, pid_alive, read_discovery
from screen_recorder_mcp.control.errors import raise_from_payload
from screen_recorder_mcp.control.logjson import log_json
from screen_recorder_mcp.control.server import RPC_PATH, TOKEN_HEADER
from screen_recorder_mcp.service import (
    RecorderListener,
    RecorderSettings,
    RecordingInfo,
    RecordingRequest,
    RecordingStatus,
    TargetList,
    Unsubscribe,
    to_jsonable,
)
from screen_recorder_mcp.geometry import Rect

PING_TIMEOUT_SECONDS = 0.3
_CALL_TIMEOUT_SECONDS = 120.0  # stop/trim/export_gif block while ffmpeg finishes
_MAX_POLL_FAILURES = 5


def _post(discovery: ControlDiscovery, method: str, params: Mapping[str, object], timeout: float) -> object:
    """One RPC round trip; returns the result or raises the service's exception type."""
    connection = http.client.HTTPConnection("127.0.0.1", discovery.port, timeout=timeout)
    try:
        body = json.dumps({"method": method, "params": params})
        connection.request("POST", RPC_PATH, body, {TOKEN_HEADER: discovery.token, "Content-Type": "application/json"})
        reply = json.loads(connection.getresponse().read())
    except (OSError, http.client.HTTPException, ValueError) as error:
        raise RuntimeError(
            f"control server on 127.0.0.1:{discovery.port} unreachable for {method!r}: {error}"
        ) from error
    finally:
        connection.close()
    if not reply.get("ok"):
        raise_from_payload(reply.get("error", {}))
    return reply.get("result")


def discover_running_app(data_dir: Path) -> ControlDiscovery | None:
    """The running app's discovery info, or None (missing file, dead pid, or no ping answer in ~300 ms).

    Example: discovery = discover_running_app(app_data_dir()); service = RemoteRecorderService(discovery)
    """
    discovery = read_discovery(data_dir)
    if discovery is None or not pid_alive(discovery.pid):
        return None
    try:
        _post(discovery, "ping", {}, PING_TIMEOUT_SECONDS)
    except (RuntimeError, ValueError, LookupError):
        return None
    return discovery


class RemoteRecorderService:
    """RecorderService backed by the app's control server. Thread-safe: one connection per call."""

    def __init__(self, discovery: ControlDiscovery, poll_timeout_seconds: float = 15.0) -> None:
        self._discovery = discovery
        self._poll_timeout = poll_timeout_seconds

    def _call(self, method: str, **params: object) -> object:
        return _post(self._discovery, method, params, _CALL_TIMEOUT_SECONDS)

    def list_targets(self) -> TargetList:
        return codec.targets_from_json(self._call("list_targets"))

    def pick_region(self, target: str) -> tuple[dict[str, object], Rect | None]:
        resolved, region = self._call("pick_region", target=target)  # type: ignore[misc]
        return resolved, codec.rect_from_json(region)

    def start(self, request: RecordingRequest) -> RecordingStatus:
        return codec.status_from_json(self._call("start", request=to_jsonable(request)))

    def pause(self) -> RecordingStatus:
        return codec.status_from_json(self._call("pause"))

    def resume(self) -> RecordingStatus:
        return codec.status_from_json(self._call("resume"))

    def stop(self) -> RecordingInfo:
        return codec.info_from_json(self._call("stop"))

    def cancel(self) -> None:
        self._call("cancel")

    def status(self) -> RecordingStatus:
        return codec.status_from_json(self._call("status"))

    def history(self, limit: int = 50) -> list[RecordingInfo]:
        return [codec.info_from_json(item) for item in self._call("history", limit=limit)]  # type: ignore[attr-defined]

    def delete_recording(self, recording_id: str) -> None:
        self._call("delete_recording", recording_id=recording_id)

    def trim(self, recording_id: str, start_seconds: float, end_seconds: float) -> RecordingInfo:
        raw = self._call("trim", recording_id=recording_id, start_seconds=start_seconds, end_seconds=end_seconds)
        return codec.info_from_json(raw)

    def export_gif(self, recording_id: str, high_quality: bool, output: Path | None = None) -> Path:
        raw = self._call("export_gif", recording_id=recording_id, high_quality=high_quality, output=to_jsonable(output))
        return Path(str(raw))

    def get_settings(self) -> RecorderSettings:
        return codec.settings_from_json(self._call("get_settings"))

    def update_settings(self, changes: dict[str, object]) -> RecorderSettings:
        return codec.settings_from_json(self._call("update_settings", changes=to_jsonable(changes)))

    def subscribe(self, listener: RecorderListener) -> Unsubscribe:
        """Deliver the app's events to `listener` from a background thread until unsubscribed.

        Example: stop = remote.subscribe(print); ...; stop()
        """
        cancelled = threading.Event()
        pump = _EventPump(self._discovery, listener, cancelled, self._poll_timeout)
        pump.prime()  # synchronous, so events fired right after subscribe() are not missed
        threading.Thread(target=pump.run, name="control-events", daemon=True).start()
        return cancelled.set


class _EventPump:
    """Long-polls the server and forwards each event; gives up after repeated failures."""

    def __init__(
        self, discovery: ControlDiscovery, listener: RecorderListener, cancelled: threading.Event, poll_timeout: float
    ) -> None:
        self._discovery = discovery
        self._listener = listener
        self._cancelled = cancelled
        self._poll_timeout = poll_timeout
        self._cursor: int | None = None

    def prime(self) -> None:
        self._cursor = self._poll(None)

    def run(self) -> None:
        cursor = self._cursor
        failures = 0
        while not self._cancelled.is_set() and failures < _MAX_POLL_FAILURES:
            try:
                cursor = self._poll(cursor)
                failures = 0
            except RuntimeError as error:
                failures += 1
                log_json("control_poll_failed", level=logging.WARNING, error=str(error), failures=failures)
                self._cancelled.wait(1.0)

    def _poll(self, cursor: int | None) -> int | None:
        params = {"after": cursor, "timeout": self._poll_timeout}
        reply = _post(self._discovery, "poll_events", params, self._poll_timeout + 5.0)
        for item in reply["events"]:  # type: ignore[index]
            self._deliver(item["event"])
        return int(reply["last"])  # type: ignore[index]

    def _deliver(self, raw_event: object) -> None:
        if self._cancelled.is_set():
            return
        try:
            self._listener(codec.event_from_json(raw_event))
        except Exception as error:  # noqa: BLE001 - one bad listener must not kill the pump
            log_json("control_listener_failed", level=logging.ERROR, error=str(error))
