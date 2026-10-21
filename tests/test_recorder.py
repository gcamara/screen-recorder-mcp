from pathlib import Path

import pytest

from screen_recorder_mcp import recorder
from screen_recorder_mcp.capture import AudioMode
from screen_recorder_mcp.wav_writer import SilenceGapFiller, float_to_int16
from screen_recorder_mcp.encoder import build_ffmpeg_args
from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.muxer import build_mux_args
from screen_recorder_mcp.recorder import RecordingParts, RecordingSession
from screen_recorder_mcp.region_picker import normalize_drag, parse_picker_output
from screen_recorder_mcp.session_factory import plan_recording
from screen_recorder_mcp.targets import CaptureTarget
from tests.fakes import FakeAudioRecorder, FakeEncoder, FakeFrameSource, FakeMuxer, solid_frame

TARGET = CaptureTarget(label="Notepad", rect=Rect(0, 0, 101, 81), window_id=5)


def make_session(tmp_path: Path, region: Rect | None, audio: AudioMode, first_frame: bool = True):
    plan = plan_recording(TARGET, region, 10, audio, tmp_path)
    parts = RecordingParts(
        frame_source=FakeFrameSource(solid_frame(101, 81) if first_frame else None),
        encoder=FakeEncoder(),
        audio=FakeAudioRecorder() if plan.audio_path else None,
        muxer=FakeMuxer(),
    )
    return RecordingSession(plan, parts), parts


def test_full_window_is_locked_to_even_size(tmp_path: Path) -> None:
    session, parts = make_session(tmp_path, None, AudioMode.NONE)
    session.start()
    assert session.stop() == session.plan.output
    assert parts.encoder.size == (100, 80, 10)
    assert parts.encoder.closed and parts.frame_source.stopped
    assert parts.encoder.frames[0].shape == (80, 100, 4)


def test_region_sets_output_size(tmp_path: Path) -> None:
    session, parts = make_session(tmp_path, Rect(10, 10, 31, 21), AudioMode.NONE)
    session.start()
    session.stop()
    assert parts.encoder.size == (30, 20, 10)


def test_audio_is_muxed_into_final_output(tmp_path: Path) -> None:
    session, parts = make_session(tmp_path, None, AudioMode.SYSTEM)
    session.start()
    session.stop()
    assert parts.audio.started and parts.audio.stopped
    video, audio, output = parts.muxer.calls[0]
    assert output == session.plan.output and video != output and audio.suffix == ".wav"


def test_minimized_window_fails_fast(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recorder, "_FIRST_FRAME_TIMEOUT_SECONDS", 0.01)
    session, parts = make_session(tmp_path, None, AudioMode.NONE, first_frame=False)
    with pytest.raises(RuntimeError, match="minimized"):
        session.start()
    assert parts.frame_source.stopped


def test_closed_window_is_reported(tmp_path: Path) -> None:
    session, parts = make_session(tmp_path, None, AudioMode.NONE)
    session.start()
    parts.frame_source.closed_sink()
    assert session.status()["target_closed"] is True
    session.stop()


def test_plan_rejects_bad_fps(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="fps=0"):
        plan_recording(TARGET, None, 0, AudioMode.NONE, tmp_path)


def test_silence_gap_filler_aligns_to_clock() -> None:
    filler = SilenceGapFiller(rate=1000, channels=2, origin=0.0)
    filler.record(500)
    assert filler.silence_due(0.55) == b""  # 50 ms gap is within slack
    assert len(filler.silence_due(1.0)) == 500 * 2 * 2
    assert filler.frames_written == 1000


def test_ffmpeg_args_pipe_bgra_to_h264() -> None:
    args = build_ffmpeg_args("ffmpeg", 640, 480, 30, Path("out.mp4"))
    assert args[args.index("-s") + 1] == "640x480" and "libx264" in args and args[-1] == "out.mp4"


def test_mux_args_pad_audio_to_video() -> None:
    args = build_mux_args("ffmpeg", Path("v.mp4"), Path("a.wav"), Path("o.mp4"))
    assert args[args.index("-af") + 1] == "apad" and "-shortest" in args


def test_drag_normalizes_any_direction_and_ignores_clicks() -> None:
    assert normalize_drag((50, 40), (10, 0)) == Rect(10, 0, 40, 40)
    assert normalize_drag((5, 5), (8, 8)) is None


def test_picker_output_parsing() -> None:
    assert parse_picker_output('{"region": null}\n') is None
    raw = '{"region": {"x": 1, "y": 2, "width": 30, "height": 40}}'
    assert parse_picker_output(raw) == Rect(1, 2, 30, 40)


def test_retina_frame_scales_region_from_points(tmp_path: Path) -> None:
    # macOS: target rect in points (101x81), frame in pixels at 2x (202x162).
    plan = plan_recording(TARGET, Rect(10, 10, 20, 15), 10, AudioMode.NONE, tmp_path)
    parts = RecordingParts(FakeFrameSource(solid_frame(202, 162)), FakeEncoder(), None, FakeMuxer())
    session = RecordingSession(plan, parts)
    session.start()
    session.stop()
    assert parts.encoder.size == (40, 30, 10)


def test_float_to_int16_clips_and_scales() -> None:
    import numpy as np

    pcm = float_to_int16(np.array([2.0, -1.0, 0.0], dtype=np.float32))
    assert np.frombuffer(pcm, "<i2").tolist() == [32767, -32767, 0]


def test_planar_float_audio_is_interleaved() -> None:
    import numpy as np

    from screen_recorder_mcp.wav_writer import planar_float_to_int16

    planes = np.array([1.0, 1.0, 0.0, 0.0], dtype=np.float32).tobytes()  # L=[1,1], R=[0,0]
    pcm, frames = planar_float_to_int16(planes, 2)
    assert frames == 2 and np.frombuffer(pcm, "<i2").tolist() == [32767, 0, 32767, 0]


def test_planar_audio_rejects_uneven_planes() -> None:
    import numpy as np

    from screen_recorder_mcp.wav_writer import planar_float_to_int16

    with pytest.raises(ValueError, match="3 float samples"):
        planar_float_to_int16(np.zeros(3, np.float32).tobytes(), 2)


def test_encoder_is_finalized_when_capture_stop_fails(tmp_path: Path) -> None:
    from tests.fakes import FailingStopFrameSource

    plan = plan_recording(TARGET, None, 10, AudioMode.NONE, tmp_path)
    parts = RecordingParts(FailingStopFrameSource(solid_frame(100, 80)), FakeEncoder(), None, FakeMuxer())
    session = RecordingSession(plan, parts)
    session.start()
    with pytest.raises(TimeoutError):
        session.stop()
    assert parts.encoder.closed


def test_audio_start_failure_stops_capture_and_encoder(tmp_path: Path) -> None:
    from tests.fakes import FailingAudioRecorder

    plan = plan_recording(TARGET, None, 10, AudioMode.SYSTEM, tmp_path)
    parts = RecordingParts(FakeFrameSource(solid_frame(100, 80)), FakeEncoder(), FailingAudioRecorder(), FakeMuxer())
    with pytest.raises(PermissionError):
        RecordingSession(plan, parts).start()
    assert parts.frame_source.stopped and parts.encoder.closed
