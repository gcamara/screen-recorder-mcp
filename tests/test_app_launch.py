"""Desktop app launch: a second launch hands over to the running one; the console-free launcher."""

from pathlib import Path

from screen_recorder_mcp.ui.app import hand_over_to_running_instance, listen_for_later_launches


def test_second_launch_hands_over_to_the_first(qapp) -> None:  # type: ignore[no-untyped-def]
    key = f"screen-recorder-mcp-test-{id(qapp)}"
    assert not hand_over_to_running_instance(key)
    shown: list[bool] = []
    server = listen_for_later_launches(key, lambda: shown.append(True))
    assert hand_over_to_running_instance(key)
    for _ in range(50):
        qapp.processEvents()
        if shown:
            break
    server.close()
    assert shown == [True]


def test_launcher_script_names_the_app_module() -> None:
    script = Path(__file__).resolve().parent.parent / "scripts" / "launch_app.pyw"
    text = script.read_text(encoding="utf-8")
    assert 'run_module("screen_recorder_mcp.ui.app", run_name="__main__")' in text and "site-packages" in text
