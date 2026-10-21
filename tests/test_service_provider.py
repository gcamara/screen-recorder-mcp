from pathlib import Path

from screen_recorder_mcp.control.discovery import ControlDiscovery
from screen_recorder_mcp.service import RecorderService, RecordingRequest
from screen_recorder_mcp.service_provider import ServiceProvider
from tests.fakes import FakeRecorderService

DISCOVERY = ControlDiscovery(1, "t", 2, "v")


class ProviderFixture:
    """Named fake of the discovery + factory seams of ServiceProvider."""

    def __init__(self, app_running: bool) -> None:
        self.app_running = app_running
        self.local = FakeRecorderService()
        self.remote = FakeRecorderService()
        self.local_builds = 0

    def discover(self, _data_dir: Path) -> ControlDiscovery | None:
        return DISCOVERY if self.app_running else None

    def build_remote(self, _discovery: ControlDiscovery) -> RecorderService:
        return self.remote

    def build_local(self) -> RecorderService:
        self.local_builds += 1
        return self.local

    def provider(self) -> ServiceProvider:
        return ServiceProvider(Path("."), self.build_local, self.discover, self.build_remote)


def test_uses_remote_when_the_app_answers_and_never_builds_local() -> None:
    fixture = ProviderFixture(app_running=True)
    assert fixture.provider().get() is fixture.remote
    assert fixture.local_builds == 0


def test_falls_back_to_a_single_lazy_local_service() -> None:
    fixture = ProviderFixture(app_running=False)
    provider = fixture.provider()
    assert provider.get() is fixture.local and provider.get() is fixture.local
    assert fixture.local_builds == 1


def test_choice_is_made_per_call() -> None:
    fixture = ProviderFixture(app_running=False)
    provider = fixture.provider()
    assert provider.get() is fixture.local
    fixture.app_running = True
    assert provider.get() is fixture.remote


def test_a_running_local_recording_is_not_orphaned_when_the_app_appears() -> None:
    fixture = ProviderFixture(app_running=False)
    provider = fixture.provider()
    provider.get().start(RecordingRequest("Notepad"))
    fixture.app_running = True
    assert provider.get() is fixture.local
    fixture.local.stop()
    assert provider.get() is fixture.remote
