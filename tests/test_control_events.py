import threading
import time

from screen_recorder_mcp.control.events import EventBus


def test_poll_with_none_returns_only_the_cursor() -> None:
    bus = EventBus()
    bus.publish({"n": 1})
    assert bus.poll(None, 0) == ([], 1)


def test_poll_returns_events_after_the_cursor() -> None:
    bus = EventBus()
    for number in range(3):
        bus.publish({"n": number})
    events, last = bus.poll(1, 0)
    assert [item["seq"] for item in events] == [2, 3] and last == 3


def test_poll_times_out_empty() -> None:
    assert EventBus().poll(0, 0.05) == ([], 0)


def test_poll_wakes_when_an_event_is_published() -> None:
    bus = EventBus()
    threading.Timer(0.05, bus.publish, args=({"n": 1},)).start()
    started = time.monotonic()
    events, _ = bus.poll(0, 5)
    assert len(events) == 1 and time.monotonic() - started < 2


def test_close_releases_waiting_polls() -> None:
    bus = EventBus()
    threading.Timer(0.05, bus.close).start()
    started = time.monotonic()
    assert bus.poll(0, 5)[0] == [] and time.monotonic() - started < 2
