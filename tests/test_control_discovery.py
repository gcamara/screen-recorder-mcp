import os
import subprocess
import sys
from pathlib import Path

from screen_recorder_mcp.control.discovery import (
    ControlDiscovery,
    discovery_path,
    pid_alive,
    read_discovery,
    remove_discovery,
    write_discovery,
)

SAMPLE = ControlDiscovery(port=5123, token="tok", pid=os.getpid(), version="0.1.0")


def test_write_then_read_round_trips(tmp_path: Path) -> None:
    write_discovery(tmp_path / "app", SAMPLE)
    assert read_discovery(tmp_path / "app") == SAMPLE


def test_read_returns_none_for_missing_or_malformed(tmp_path: Path) -> None:
    assert read_discovery(tmp_path) is None
    discovery_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert read_discovery(tmp_path) is None
    discovery_path(tmp_path).write_text('{"port": 1}', encoding="utf-8")
    assert read_discovery(tmp_path) is None


def test_remove_only_deletes_files_owned_by_the_pid(tmp_path: Path) -> None:
    write_discovery(tmp_path, SAMPLE)
    remove_discovery(tmp_path, SAMPLE.pid + 1)
    assert discovery_path(tmp_path).exists()
    remove_discovery(tmp_path, SAMPLE.pid)
    assert not discovery_path(tmp_path).exists()


def test_write_leaves_no_temp_files(tmp_path: Path) -> None:
    write_discovery(tmp_path, SAMPLE)
    assert [path.name for path in tmp_path.iterdir()] == ["control.json"]


def test_pid_alive_for_self_and_for_an_exited_process() -> None:
    assert pid_alive(os.getpid())
    finished = subprocess.run([sys.executable, "-c", "pass"], check=True)
    assert not pid_alive(0) and not pid_alive(-3)
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert finished.returncode == 0
    assert not pid_alive(child.pid)
