"""Maps wire method names to RecorderService calls (JSON in, JSON out)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Mapping

from screen_recorder_mcp.control.codec import request_from_json
from screen_recorder_mcp.control.events import EventBus
from screen_recorder_mcp.service import RecorderService, to_jsonable

Params = Mapping[str, object]
Method = Callable[[Params], object]

PROTOCOL_VERSION = "1"
_MAX_POLL_SECONDS = 30.0


def _need(params: Params, key: str) -> object:
    if key not in params:
        raise ValueError(f"missing parameter {key!r}; got keys {sorted(params)}")
    return params[key]


class RpcDispatcher:
    """One method per RecorderService method, plus `ping` and `poll_events`."""

    def __init__(self, service: RecorderService, bus: EventBus, app_version: str) -> None:
        self._service = service
        self._bus = bus
        self._app_version = app_version
        self._methods: dict[str, Method] = self._build_table()

    def call(self, method: str, params: Params) -> object:
        """Run one method and return its JSON-safe result.

        Example: dispatcher.call("history", {"limit": 5}) -> [{"recording_id": ...}, ...]
        """
        handler = self._methods.get(method)
        if handler is None:
            raise ValueError(f"unknown method {method!r}; expected one of {sorted(self._methods)}")
        return to_jsonable(handler(params))

    def _build_table(self) -> dict[str, Method]:
        service = self._service
        return {
            "ping": lambda _p: {"version": self._app_version, "protocol": PROTOCOL_VERSION, "pid": os.getpid()},
            "list_targets": lambda _p: service.list_targets(),
            "pick_region": lambda p: list(service.pick_region(str(_need(p, "target")))),
            "start": lambda p: service.start(request_from_json(p.get("request"))),
            "pause": lambda _p: service.pause(),
            "resume": lambda _p: service.resume(),
            "stop": lambda _p: service.stop(),
            "cancel": self._cancel,
            "status": lambda _p: service.status(),
            "history": lambda p: service.history(int(p.get("limit", 50))),  # type: ignore[call-overload]
            "delete_recording": self._delete_recording,
            "trim": self._trim,
            "export_gif": self._export_gif,
            "get_settings": lambda _p: service.get_settings(),
            "update_settings": self._update_settings,
            "poll_events": self._poll_events,
        }

    def _cancel(self, _params: Params) -> None:
        self._service.cancel()

    def _delete_recording(self, params: Params) -> None:
        self._service.delete_recording(str(_need(params, "recording_id")))

    def _trim(self, params: Params) -> object:
        return self._service.trim(
            str(_need(params, "recording_id")), float(_need(params, "start_seconds")), float(_need(params, "end_seconds"))  # type: ignore[arg-type]
        )

    def _export_gif(self, params: Params) -> object:
        raw_output = params.get("output")
        output = None if raw_output is None else Path(str(raw_output))
        return self._service.export_gif(str(_need(params, "recording_id")), bool(params.get("high_quality", False)), output)

    def _update_settings(self, params: Params) -> object:
        changes = params.get("changes")
        if not isinstance(changes, dict):
            raise ValueError(f"update_settings needs 'changes' as an object, got {changes!r}")
        return self._service.update_settings(changes)

    def _poll_events(self, params: Params) -> dict[str, object]:
        raw_after = params.get("after")
        timeout = min(float(params.get("timeout", 20.0)), _MAX_POLL_SECONDS)  # type: ignore[arg-type]
        events, last = self._bus.poll(None if raw_after is None else int(raw_after), timeout)  # type: ignore[call-overload]
        return {"events": events, "last": last}
