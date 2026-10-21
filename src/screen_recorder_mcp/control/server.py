"""Loopback control server: exposes a RecorderService to the MCP server and other local tools.

Wire format: POST /rpc, header `X-Control-Token`, body {"method": str, "params": {...}},
reply {"ok": true, "result": ...} or {"ok": false, "error": {"type", "message"}}.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from screen_recorder_mcp.control.discovery import ControlDiscovery, remove_discovery, write_discovery
from screen_recorder_mcp.control.dispatch import RpcDispatcher
from screen_recorder_mcp.control.errors import error_payload
from screen_recorder_mcp.control.events import EventBus
from screen_recorder_mcp.control.logjson import log_json
from screen_recorder_mcp.service import RecorderService, app_data_dir, to_jsonable

TOKEN_HEADER = "X-Control-Token"
RPC_PATH = "/rpc"
APP_VERSION = "0.1.0"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})
_MAX_BODY_BYTES = 1_000_000


class _ControlHttpServer(ThreadingHTTPServer):
    daemon_threads = True  # a hung long-poll must never block process exit

    def __init__(self, token: str, dispatcher: RpcDispatcher) -> None:
        super().__init__(("127.0.0.1", 0), _RpcHandler)
        self.token = token
        self.dispatcher = dispatcher


class _RpcHandler(BaseHTTPRequestHandler):
    server: _ControlHttpServer

    def do_POST(self) -> None:  # noqa: N802 (http.server naming)
        rejection = self._rejection()
        if rejection is not None:
            self._reply(rejection[0], {"ok": False, "error": {"type": "RuntimeError", "message": rejection[1]}})
            return
        self._reply(200, self._run_rpc())

    def _rejection(self) -> tuple[int, str] | None:
        if self.client_address[0] not in _LOOPBACK_HOSTS:
            return 403, f"non-loopback client {self.client_address[0]!r} rejected"
        supplied = self.headers.get(TOKEN_HEADER, "")
        if not hmac.compare_digest(supplied.encode(), self.server.token.encode()):
            return 401, f"missing or invalid {TOKEN_HEADER} header"
        if self.path != RPC_PATH:
            return 404, f"unknown path {self.path!r}; expected {RPC_PATH}"
        return None

    def _run_rpc(self) -> dict[str, object]:
        try:
            body = self._read_json_body()
            method = body.get("method")
            if not isinstance(method, str):
                raise ValueError(f"'method' must be a string, got {method!r}")
            params = body.get("params") or {}
            return {"ok": True, "result": self.server.dispatcher.call(method, params)}
        except Exception as error:  # noqa: BLE001 - every failure must reach the client as a typed payload
            return {"ok": False, "error": error_payload(error)}

    def _read_json_body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > _MAX_BODY_BYTES:
            raise ValueError(f"request body of {length} bytes exceeds the {_MAX_BODY_BYTES} byte limit")
        parsed = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(parsed, dict):
            raise ValueError(f"request body must be a JSON object, got {type(parsed).__name__}")
        return parsed

    def _reply(self, status: int, payload: dict[str, object]) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        log_json("control_http", level=logging.DEBUG, line=format % args)


@dataclass
class ControlServerHandle:
    """Running control server. `stop()` is idempotent."""

    port: int
    token: str
    _stop: Callable[[], None]

    def stop(self) -> None:
        self._stop()


def serve_control(
    service: RecorderService, data_dir: Path | None = None, app_version: str = APP_VERSION
) -> ControlServerHandle:
    """Expose `service` on 127.0.0.1 (OS-assigned port) and publish control.json in `data_dir`.

    Example: handle = serve_control(local_service); ...; handle.stop()
    """
    directory = data_dir if data_dir is not None else app_data_dir()
    bus = EventBus()
    unsubscribe = service.subscribe(lambda event: bus.publish(to_jsonable(event)))
    token = secrets.token_hex(32)
    http_server = _ControlHttpServer(token, RpcDispatcher(service, bus, app_version))
    port = int(http_server.server_address[1])
    threading.Thread(target=http_server.serve_forever, name="control-server", daemon=True).start()
    write_discovery(directory, ControlDiscovery(port, token, os.getpid(), app_version))
    log_json("control_started", port=port, pid=os.getpid())
    return ControlServerHandle(port, token, _make_stopper(http_server, bus, unsubscribe, directory))


def _make_stopper(
    http_server: _ControlHttpServer, bus: EventBus, unsubscribe: Callable[[], None], directory: Path
) -> Callable[[], None]:
    stopped = threading.Event()

    def stop() -> None:
        if stopped.is_set():
            return
        stopped.set()
        remove_discovery(directory, os.getpid())
        bus.close()
        unsubscribe()
        http_server.shutdown()
        http_server.server_close()
        log_json("control_stopped")

    return stop
