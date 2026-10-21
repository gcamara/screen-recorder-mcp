import threading
import wave
from pathlib import Path

import pytest

from screen_recorder_mcp.capture import AudioMode
from screen_recorder_mcp.frame_pacer import FramePacer, LatestFrameSlot
from screen_recorder_mcp.recorder import RecordingParts, RecordingSession
from screen_recorder_mcp.session_factory import plan_recording
from screen_recorder_mcp.wav_writer import AlignedWavWriter, SilenceGapFiller
from tests.fakes import (
    FakeAudioRecorder, FakeClock, FakeEncoder, FakeFrameSource, FakeMuxer, solid_frame,
)
from tests.test_recorder import TARGET


def make_pacer(clock: FakeClock) -> tuple[FramePacer, FakeEncoder]:
    slot, encoder = LatestFrameSlot(), FakeEncoder()
    slot.put(solid_frame(4, 4))
    return FramePacer(slot, encoder.write, 10, None, (4, 4), clock=clock), encoder


def test_pacer_content_seconds_excludes_paused_span() -> None:
    clock = FakeClock()
    pacer, _ = make_pacer(clock)
    clock.now = 1.0
    pacer.pause()
    clock.now = 4.0
    assert pacer.content_seconds(0.0) == 1.0  # frozen while paused
    pacer.resume()
    clock.now = 4.5
    assert pacer.content_seconds(0.0) == pytest.approx(1.5)


def test_pacer_emits_no_frames_for_paused_time() -> None:
    clock = FakeClock()
    pacer, encoder = make_pacer(clock)
    pacer.tick(pacer.content_seconds(0.0))
    clock.now = 1.0
    pacer.pause()
    clock.now = 6.0
    pacer.resume()
    clock.now = 6.5
    pacer.tick(pacer.content_seconds(0.0))
    assert len(encoder.frames) == 16  # 1.5 s of content at 10 fps, t=0..1.5


def test_pacer_run_does_not_tick_while_paused_but_flushes_on_stop() -> None:
    clock = FakeClock(2.0)
    pacer, encoder = make_pacer(clock)
    pacer.pause()
    stop = threading.Event()
    stop.set()
    pacer.run(stop, origin=0.0)
    assert len(encoder.frames) == 21  # flush at the frozen 2.0 s, not at wall clock


def test_pacer_pause_is_idempotent() -> None:
    clock = FakeClock()
    pacer, _ = make_pacer(clock)
    pacer.pause()
    clock.now = 1.0
    pacer.pause()
    pacer.resume()
    pacer.resume()
    assert pacer.is_paused() is False and pacer.content_seconds(0.0) == 0.0


def test_gap_filler_owes_no_silence_for_paused_span() -> None:
    filler = SilenceGapFiller(rate=1000, channels=1, origin=0.0)
    filler.record(1000)
    filler.pause(1.0)
    assert filler.silence_due(3.0) == b""
    filler.resume(4.0)
    assert filler.silence_due(4.5) == bytes(500 * 2)  # only the 0.5 s after resume


def test_wav_writer_drops_audio_and_gap_while_paused(tmp_path: Path) -> None:
    clock, path = FakeClock(), tmp_path / "a.wav"
    writer = AlignedWavWriter(path, 1000, 1, clock=clock)
    writer.write_pcm(bytes(1000 * 2), 1000)
    clock.now = 1.0
    writer.pause()
    clock.now = 2.0
    writer.write_pcm(bytes(777 * 2), 777)  # arrives mid-pause: dropped
    clock.now = 4.0
    writer.resume()
    writer.write_pcm(bytes(100 * 2), 100)
    writer.close()
    with wave.open(str(path)) as wav:
        assert wav.getnframes() == 1100


def test_session_pause_and_resume_reach_pacer_and_audio(tmp_path: Path) -> None:
    plan = plan_recording(TARGET, None, 10, AudioMode.SYSTEM, tmp_path)
    audio = FakeAudioRecorder()
    parts = RecordingParts(FakeFrameSource(solid_frame(100, 80)), FakeEncoder(), audio, FakeMuxer())
    session = RecordingSession(plan, parts)
    session.start()
    session.pause()
    assert audio.paused and session.content_seconds() >= 0.0
    frozen = session.content_seconds()
    assert session.content_seconds() == frozen
    session.resume()
    assert not audio.paused and audio.resume_calls == 1
    session.stop()


def test_session_pause_before_start_names_the_problem(tmp_path: Path) -> None:
    plan = plan_recording(TARGET, None, 10, AudioMode.NONE, tmp_path)
    session = RecordingSession(plan, RecordingParts(FakeFrameSource(None), FakeEncoder(), None, FakeMuxer()))
    with pytest.raises(RuntimeError, match="pause[(][)] before start"):
        session.pause()
    with pytest.raises(RuntimeError, match="resume[(][)] before start"):
        session.resume()
    assert session.content_seconds() == 0.0


def test_closed_target_calls_registered_callback(tmp_path: Path) -> None:
    plan = plan_recording(TARGET, None, 10, AudioMode.NONE, tmp_path)
    parts = RecordingParts(FakeFrameSource(solid_frame(100, 80)), FakeEncoder(), None, FakeMuxer())
    seen: list[str] = []
    session = RecordingSession(plan, parts, on_target_closed=lambda: seen.append("closed"))
    session.start()
    parts.frame_source.closed_sink()
    session.stop()
    assert seen == ["closed"]
