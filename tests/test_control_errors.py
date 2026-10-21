import pytest

from screen_recorder_mcp.control.errors import error_payload, raise_from_payload


@pytest.mark.parametrize("error", [ValueError("bad fps 0"), LookupError("no id 7"), RuntimeError("busy")])
def test_round_trip_keeps_type_and_message(error: Exception) -> None:
    with pytest.raises(type(error), match=str(error)) as raised:
        raise_from_payload(error_payload(error))
    assert type(raised.value) is type(error)


def test_key_error_travels_as_lookup_error_without_repr_quotes() -> None:
    payload = error_payload(KeyError("rec-9"))
    assert payload == {"type": "LookupError", "message": "rec-9"}


def test_unexpected_exception_becomes_runtime_error_naming_its_type() -> None:
    payload = error_payload(OSError("disk full"))
    assert payload == {"type": "RuntimeError", "message": "OSError: disk full"}


def test_unknown_wire_type_is_reported_with_value() -> None:
    with pytest.raises(RuntimeError, match="unknown error type 'Weird'"):
        raise_from_payload({"type": "Weird", "message": "m"})
