"""Video manuals: script parsing, holds, drawing, subtitles, narration cache, full render with ffmpeg."""

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.manual import (
    Annotation, Hold, Painter, Plan, SourceVideo, Step, StepWindow, Timeline, audio_filter, build_overlay, build_srt,
    draw_overlay, fade, parse_steps, plan_manual, probe_source, render_manual, srt_time,
)
from screen_recorder_mcp.media_tools import run_command
from screen_recorder_mcp.narration import Narrator, Sentence, SpeechClip, sentences_from_marks

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def test_parse_steps_sorts_and_reads_every_annotation_type() -> None:
    steps = parse_steps([
        {"at": 3, "say": "Second."},
        {"at": 1, "say": "First.", "annotations": [
            {"type": "text", "x": 10, "y": 20, "text": "Hello", "width": 300},
            {"type": "arrow", "from": [1, 2], "to": [30, 40], "start": 0.5, "end": 2, "color": "#00ff00"},
            {"type": "box", "x": 1, "y": 2, "width": 3, "height": 4},
            {"type": "ellipse", "x": 1, "y": 2, "width": 3, "height": 4},
            {"type": "badge", "x": 50, "y": 60, "text": "1"},
            {"type": "spotlight", "x": 0, "y": 0, "width": 9, "height": 9},
            {"type": "blur", "x": 0, "y": 0, "width": 9, "height": 9},
        ]},
    ])  # fmt: skip
    assert [s.at for s in steps] == [1.0, 3.0]
    arrow = steps[0].items[1]
    assert arrow.points == ((1, 2), (30, 40)) and (arrow.start, arrow.end, arrow.color) == (0.5, 2.0, "#00ff00")
    assert steps[0].items[0].rect == Rect(10, 20, 300, 0) and len(steps[0].items) == 7


@pytest.mark.parametrize("raw, message", [
    ([], "at least one step"),
    ([{"say": "x"}], "needs 'at'"),
    ([{"at": 0, "annotations": [{"type": "circle"}]}], "type must be one of"),
    ([{"at": 0, "annotations": [{"type": "arrow", "from": [1, 2]}]}], "'from': \\[x, y\\] and 'to'"),
    ([{"at": 0, "annotations": [{"type": "text", "x": 1, "y": 2}]}], "needs 'x', 'y' and 'text'"),
    ([{"at": 0, "annotations": [{"type": "box", "x": 1}]}], "missing \\['y', 'width', 'height'\\]"),
])  # fmt: skip
def test_parse_steps_names_what_is_wrong(raw: list, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_steps(raw)


def test_timeline_maps_both_ways_around_a_hold() -> None:
    timeline = Timeline((Hold(2.0, 1.5),))
    assert [timeline.to_output(t) for t in (1.0, 2.0, 3.0)] == [1.0, 3.5, 4.5]
    assert timeline.to_source(1.0) == 1.0
    assert timeline.to_source(2.7) == pytest.approx(1.999)  # inside the hold: the step's last frame
    assert timeline.to_source(4.5) == 3.0


def test_plan_says_then_does_by_default() -> None:
    steps = [Step(0.0, "a"), Step(5.0, "b"), Step(6.0), Step(9.0, "c")]
    plan = plan_manual(steps, [2.0, 3.0, None, 1.0], 10.0)
    # step 0 has 5 s for 2.7 s of speech: no hold; step 1 has 1 s for 3.7 s: hold 2.7 s on its
    # first frame, then its action plays; step 3 has 1 s for 1.7 s: hold 0.7 s at its start.
    assert plan.timeline.holds == (Hold(5.0, 2.7, 5.0), Hold(9.0, 0.7, 9.0))
    assert plan.windows[1] == StepWindow(5.0, 8.7, 5.3)
    assert plan.windows[2] == StepWindow(8.7, 11.7, None)
    assert plan.windows[3] == StepWindow(11.7, 13.4, 12.0) and plan.duration == pytest.approx(13.4)
    assert plan.timeline.to_source(6.0) == 5.0 and plan.timeline.to_source(8.0) == pytest.approx(5.3)


def test_plan_hold_at_end_freezes_the_last_frame() -> None:
    steps = parse_steps([{"at": 0, "say": "a", "hold": "end"}, {"at": 1}])
    plan = plan_manual(steps, [2.0, None], 4.0)
    assert plan.timeline.holds == (Hold(1.0, 1.7),) and plan.windows[0] == StepWindow(0.0, 2.7, 0.3)
    assert plan.timeline.to_source(2.0) == pytest.approx(0.999)  # the step's last frame
    with pytest.raises(ValueError, match="hold must be one of"):
        parse_steps([{"at": 0, "hold": "middle"}])


def test_last_step_keeps_its_annotations_to_the_very_end() -> None:
    # Regression: a rounded hold time (7.767 vs 7.766667) once dropped the last hold from the window.
    steps = [Step(0.3, "a"), Step(4.1, "c", items=(Annotation("box", rect=Rect(1, 1, 5, 5)),))]
    plan = plan_manual(steps, [1.0, 5.232], 7.766667)
    overlay = build_overlay(steps, plan, [None, None], burn=False)
    assert overlay.shapes[0][2] == pytest.approx(plan.duration)


def test_plan_rejects_steps_outside_the_video() -> None:
    with pytest.raises(ValueError, match="outside the 4.00s recording"):
        plan_manual([Step(5.0)], [None], 4.0)


def test_fade_and_srt_time() -> None:
    assert fade(1.0, 0.0, 5.0) == 1.0 and fade(0.1, 0.0, 5.0) == pytest.approx(0.4)
    assert fade(4.9, 0.0, 5.0) == pytest.approx(0.4) and fade(6.0, 0.0, 5.0) == 0.0
    assert srt_time(83.25) == "00:01:23,250" and srt_time(3725.5) == "01:02:05,500"


def clip(text: str, duration: float, sentences: tuple[Sentence, ...] = ()) -> SpeechClip:
    return SpeechClip(text, Path("x.mp3"), duration, sentences)


def test_srt_uses_sentences_on_the_output_timeline() -> None:
    plan = Plan(Timeline(), (StepWindow(0.0, 4.0, 0.3), StepWindow(4.0, 8.0, None)), 8.0)
    clips = [clip("Hi. Bye.", 2.0, (Sentence(0.05, 0.8, "Hi."), Sentence(0.9, 1.9, "Bye."))), None]
    assert build_srt(plan, clips) == (
        "1\n00:00:00,350 --> 00:00:01,100\nHi.\n\n2\n00:00:01,200 --> 00:00:02,200\nBye.\n"
    )


def test_sentences_from_edge_tts_marks() -> None:
    marks = [{"offset": 500000, "duration": 21125000, "text": " Click Save. "}]
    assert sentences_from_marks(marks) == (Sentence(0.05, 2.162, "Click Save."),)


def test_audio_filter_retimes_original_audio_and_places_clips() -> None:
    plan = Plan(Timeline((Hold(2.0, 1.0),)), (), 6.0)
    with_audio = SourceVideo(100, 80, 30.0, 5.0, has_audio=True)
    graph = audio_filter(with_audio, plan, 1, 2, [0.3])
    assert "[1:a]atrim=0.000:2.000,asetpts=PTS-STARTPTS,adelay=0:all=1[o0]" in graph
    assert "[1:a]atrim=2.000:5.000,asetpts=PTS-STARTPTS,adelay=3000:all=1[o1]" in graph
    assert "[2:a]adelay=300:all=1[n0]" in graph and graph.endswith("amix=inputs=3:normalize=0:duration=longest[aout]")
    silent = SourceVideo(100, 80, 30.0, 5.0, has_audio=False)
    assert audio_filter(silent, plan, 1, 1, [0.3]) == "[1:a]adelay=300:all=1[n0];[n0]anull[aout]"


def test_sprites_stay_inside_the_frame() -> None:
    painter = Painter(640, 360)
    text = painter.sprite(Annotation("text", rect=Rect(600, 340, 0, 0), text="A long label that wraps nicely"))
    assert text.x + text.image.shape[1] <= 640 and text.y + text.image.shape[0] <= 360
    arrow = painter.sprite(Annotation("arrow", points=((50, 50), (200, 120))))
    assert arrow.image[..., 3].any() and arrow.x < 50 and arrow.y < 50
    badge = painter.sprite(Annotation("badge", rect=Rect(100, 100, 0, 0), text="2"))
    assert abs(badge.x + badge.image.shape[1] // 2 - 100) <= 1


def test_draw_overlay_spotlight_blur_and_sprites() -> None:
    frame = np.full((360, 640, 4), 200, np.uint8)
    frame[50:60, 400:410, :3] = 0  # a detail that blur must smear
    steps = [Step(0.0, items=(
        Annotation("spotlight", rect=Rect(100, 100, 100, 100)),
        Annotation("blur", rect=Rect(400, 50, 30, 30)),
        Annotation("box", rect=Rect(300, 200, 80, 60), color="#ff0000"),
    ))]  # fmt: skip
    plan = Plan(Timeline(), (StepWindow(0.0, 5.0, None),), 5.0)
    overlay = build_overlay(steps, plan, [None], burn=False)
    draw_overlay(frame, overlay, Painter(640, 360), 1.0)
    assert frame[150, 150, 0] == 200 and frame[10, 10, 0] < 100  # inside kept, outside dimmed
    # The 8 px block at x=408..415 holds 2 black and 6 bright columns: their mean, then dimmed by the spotlight.
    assert frame[55, 412, 0] == frame[50, 415, 0] and 0 < frame[55, 412, 0] < 80
    assert frame[200, 300:380, 2].max() > 200  # the red outline (BGRA: red is channel 2)


def test_narrator_caches_clips(tmp_path: Path) -> None:
    calls: list[str] = []

    def synthesize(text: str, voice: str, rate: str) -> tuple[bytes, list[dict[str, object]]]:
        calls.append(text)
        return b"ID3fake", [{"offset": 0, "duration": 10_000_000, "text": text}]

    narrator = Narrator(tmp_path, "ffprobe", synthesize, measure=lambda _probe, _path: 1.25)
    first = narrator.speak("Hello there.", "voice-a")
    again = narrator.speak("Hello there.", "voice-a")
    other = narrator.speak("Hello there.", "voice-b")
    assert calls == ["Hello there.", "Hello there."] and first.path == again.path != other.path
    assert first.duration == 1.25 and first.sentences == (Sentence(0.0, 1.0, "Hello there."),)


def sine_mp3(seconds: float) -> bytes:
    result = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-f", "mp3", "-"], capture_output=True, check=True,
    )  # fmt: skip
    return result.stdout


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg missing")
def test_real_render_holds_for_speech_and_writes_subtitles(tmp_path: Path) -> None:
    source = tmp_path / "rec.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=3",
         "-pix_fmt", "yuv420p", str(source)], check=True,
    )  # fmt: skip
    video = probe_source("ffprobe", source, run_command)
    assert (video.width, video.height, video.fps, video.has_audio) == (320, 240, 10.0, False)

    def synthesize(text: str, voice: str, rate: str) -> tuple[bytes, list[dict[str, object]]]:
        return sine_mp3(2.0), [{"offset": 0, "duration": 19_000_000, "text": text}]

    from screen_recorder_mcp.narration import audio_duration

    narrator = Narrator(tmp_path / "cache", "ffprobe", synthesize, audio_duration)
    steps = parse_steps([
        {"at": 0, "say": "First step.", "annotations": [{"type": "text", "x": 10, "y": 10, "text": "Step 1"}]},
        {"at": 1, "annotations": [{"type": "arrow", "from": [20, 200], "to": [160, 120]}]},
    ])  # fmt: skip
    result = render_manual(source, video, steps, tmp_path / "rec-manual.mp4", "ffmpeg", narrator, subtitles="track")
    assert result.holds and result.holds[0].at == 0.0 and result.narrated_steps == 1  # say first, then the step plays
    out = probe_source("ffprobe", result.output, run_command)
    assert out.has_audio and out.duration == pytest.approx(result.duration, abs=0.25)
    assert result.duration == pytest.approx(3.0 + result.holds[0].seconds, abs=0.01)
    assert result.subtitles == "track"
    subs = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(result.output), "-map", "0:s:0", "-f", "srt", "-"],
        capture_output=True, check=True,
    )  # fmt: skip
    assert b"First step." in subs.stdout
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ["rec-manual.mp4", "rec.mp4"]  # no loose .srt


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg missing")
def test_make_manual_tool_without_voice_registers_the_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from screen_recorder_mcp import tools as tools_module
    from screen_recorder_mcp.service import RecordingRequest
    from screen_recorder_mcp.tools import RecorderTools
    from tests.fakes import FakeRecorderService

    source = tmp_path / "rec.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=2",
         "-pix_fmt", "yuv420p", str(source)], check=True,
    )  # fmt: skip
    service = FakeRecorderService()
    service.start(RecordingRequest(target="Notepad"))
    service.stop()
    monkeypatch.setattr(tools_module, "_media_binaries", lambda: ("ffmpeg", "ffprobe"))
    body = RecorderTools(lambda: service).make_manual(
        [{"at": 0, "say": "ignored without voice", "annotations": [{"type": "box", "x": 5, "y": 5, "width": 50, "height": 40}]}],
        path=str(source), narrate=False,
    )
    assert Path(str(body["output"])).name == "rec-manual.mp4" and body["holds"] == [] and body["narrated_steps"] == 0
    assert service.history(1)[0].path == tmp_path / "rec-manual.mp4"
    assert json.dumps(body)  # JSON-safe for MCP


def test_captions_move_to_the_top_when_the_step_points_at_the_bottom() -> None:
    low = Step(0.0, "a", items=(Annotation("arrow", points=((10, 100), (300, 340))),))
    high = Step(3.0, "b", items=(Annotation("box", rect=Rect(10, 10, 50, 50)),))
    plan = Plan(Timeline(), (StepWindow(0.0, 3.0, 0.3), StepWindow(3.0, 6.0, 3.3)), 6.0)
    clips = [clip("a", 1.0, (Sentence(0, 1, "a"),)), clip("b", 1.0, (Sentence(0, 1, "b"),))]
    overlay = build_overlay([low, high], plan, clips, burn=True, video_height=360)
    assert [top for *_, top in overlay.captions] == [True, False]
    painter = Painter(640, 360)
    assert painter.caption("a", top=True).y < 40 < painter.caption("a").y
