"""Typed error payloads so the client re-raises the exact exception the service raised."""

from __future__ import annotations

from typing import Mapping

# Order matters: the first isinstance match wins (KeyError is a LookupError).
_WIRE_TYPES: tuple[tuple[str, type[Exception]], ...] = (
    ("ValueError", ValueError),
    ("LookupError", LookupError),
    ("RuntimeError", RuntimeError),
)


def error_payload(error: Exception) -> dict[str, str]:
    """Wire form of an exception; anything unexpected becomes a RuntimeError naming its real type.

    Example: error_payload(ValueError("fps 0")) -> {"type": "ValueError", "message": "fps 0"}
    """
    for wire_name, exception_type in _WIRE_TYPES:
        if isinstance(error, exception_type):
            return {"type": wire_name, "message": _message_of(error)}
    return {"type": "RuntimeError", "message": f"{type(error).__name__}: {error}"}


def _message_of(error: Exception) -> str:
    # str(KeyError("x")) is the repr "'x'"; args[0] keeps the original text.
    if isinstance(error, KeyError) and error.args:
        return str(error.args[0])
    return str(error)


def raise_from_payload(payload: Mapping[str, object]) -> None:
    """Raise the exception described by an error payload.

    Example: raise_from_payload({"type": "LookupError", "message": "no id 7"}) raises LookupError("no id 7")
    """
    message = str(payload.get("message", ""))
    for wire_name, exception_type in _WIRE_TYPES:
        if payload.get("type") == wire_name:
            raise exception_type(message)
    raise RuntimeError(f"control server sent unknown error type {payload.get('type')!r}: {message}")
