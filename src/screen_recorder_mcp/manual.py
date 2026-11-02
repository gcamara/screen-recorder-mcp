"""Video manuals: timed annotations and narration rendered over a finished recording.

A manual is a list of steps. Each step starts at a moment of the SOURCE video, may say something
(edge-tts) and shows annotations while it lasts: text blocks, arrows, outlines, numbered badges,
a spotlight that dims everything else, and blur for secrets.

Timing: narration is synthesized first, so every clip's length is known. When a step's speech
does not fit before the next step begins, the video holds a frame until the voice finishes (a
"hold") and everything after it moves later. By default the hold is at the START of the step:
the manual says what will happen over a still frame, then the action plays ("say, then do").
hold="end" freezes the step's last frame instead. Annotations belong to steps, so they stay
with their sentence however the timeline shifts.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from screen_recorder_mcp.geometry import FrameArray, Rect
from screen_recorder_mcp.input_overlay import blend_image
from screen_recorder_mcp.narration import DEFAULT_VOICE, Narrator, Sentence, SpeechClip
from screen_recorder_mcp.proc import hidden_window_flags

ANNOTATION_TYPES = ("text", "arrow", "box", "ellipse", "badge", "spotlight", "blur")
SUBTITLE_MODES = ("none", "file", "burn")
HOLD_MODES = ("start", "end")
DEFAULT_COLOR = "#ff4757"
SPEECH_LEAD = 0.3  # seconds between a step starting and its voice
SPEECH_TAIL = 0.4  # breathing room after the voice before the next step
FADE_SECONDS = 0.25


# --- script model ------------------------------------------------------------


@dataclass(frozen=True)
class Annotation:
    kind: str
    rect: Rect | None = None  # box / ellipse / spotlight / blur; text: x, y (+ width = wrap width)
    points: tuple[tuple[int, int], tuple[int, int]] | None = None  # arrow: from, to
    text: str = ""  # text block / badge label / caption
    start: float = 0.0  # seconds after the step begins
    end: float | None = None  # seconds after the step begins; None = until the step ends
    color: str = DEFAULT_COLOR


@dataclass(frozen=True)
class Step:
    at: float  # source-video seconds where the step begins
    say: str | None = None
    items: tuple[Annotation, ...] = ()
    until: float | None = None  # source seconds where the step ends; default: the next step
    hold: str = "start"  # where to freeze when the speech is longer than the step: "start" | "end"


def parse_steps(raw: Sequence[dict[str, object]]) -> list[Step]:
    """Steps from the MCP JSON. Coordinates are video pixels (the same as frame_at / crop).

    Example: parse_steps([{"at": 1.5, "say": "Click Save.", "annotations": [
                 {"type": "arrow", "from": [900, 120], "to": [1040, 64]},
                 {"type": "text", "x": 620, "y": 140, "text": "Saves the draft"}]}])
    """
    steps = [_parse_step(index, item) for index, item in enumerate(raw)]
    if not steps:
        raise ValueError("a manual needs at least one step, e.g. [{'at': 0, 'say': '...'}]")
    return sorted(steps, key=lambda step: step.at)


def _parse_step(index: int, item: dict[str, object]) -> Step:
    if "at" not in item:
        raise ValueError(f"step {index} needs 'at' (seconds in the recording), got keys {sorted(item)}")
    items = tuple(_parse_annotation(index, n, a) for n, a in enumerate(item.get("annotations") or []))  # type: ignore[arg-type]
    say = item.get("say")
    until = item.get("until")
    hold = str(item.get("hold", "start"))
    if hold not in HOLD_MODES:
        raise ValueError(f"step {index}: hold must be one of {list(HOLD_MODES)}, got {hold!r}")
    return Step(float(item["at"]), None if not say else str(say), items,  # type: ignore[arg-type]
                None if until is None else float(until), hold)  # type: ignore[arg-type]  # fmt: skip


def _parse_annotation(step: int, index: int, raw: dict[str, object]) -> Annotation:
    where = f"step {step} annotation {index}"
    kind = str(raw.get("type", ""))
    if kind not in ANNOTATION_TYPES:
        raise ValueError(f"{where}: type must be one of {list(ANNOTATION_TYPES)}, got {kind!r}")
    common = {
        "start": float(raw.get("start", 0.0)),  # type: ignore[arg-type]
        "end": None if raw.get("end") is None else float(raw["end"]),  # type: ignore[arg-type]
        "color": str(raw.get("color", DEFAULT_COLOR)),
        "text": str(raw.get("text", "")),
    }
    if kind == "arrow":
        start, end = raw.get("from"), raw.get("to")
        if not (isinstance(start, (list, tuple)) and isinstance(end, (list, tuple)) and len(start) == len(end) == 2):
            raise ValueError(f"{where}: an arrow needs 'from': [x, y] and 'to': [x, y]")
        return Annotation(kind, points=((int(start[0]), int(start[1])), (int(end[0]), int(end[1]))), **common)  # type: ignore[arg-type]
    if kind in ("text", "badge"):
        if "x" not in raw or "y" not in raw or not common["text"]:
            raise ValueError(f"{where}: a {kind} needs 'x', 'y' and 'text'")
        width = int(raw.get("width", 0))  # type: ignore[arg-type]
        return Annotation(kind, rect=Rect(int(raw["x"]), int(raw["y"]), width, 0), **common)  # type: ignore[arg-type]
    missing = [key for key in ("x", "y", "width", "height") if key not in raw]
    if missing:
        raise ValueError(f"{where}: a {kind} needs x, y, width, height; missing {missing}")
    rect = Rect(int(raw["x"]), int(raw["y"]), int(raw["width"]), int(raw["height"]))  # type: ignore[arg-type]
    return Annotation(kind, rect=rect, **common)  # type: ignore[arg-type]


# --- timeline ----------------------------------------------------------------


@dataclass(frozen=True)
class Hold:
    at: float  # source seconds where the hold is inserted
    seconds: float
    frame: float | None = None  # source seconds of the frame shown; default: just before `at`

    @property
    def shown(self) -> float:
        return max(0.0, self.at - 0.001) if self.frame is None else self.frame


@dataclass(frozen=True)
class Timeline:
    """Source time ↔ output time once the holds are inserted."""

    holds: tuple[Hold, ...] = ()  # in timeline order (several may share `at`)

    @property
    def extra(self) -> float:
        return sum(hold.seconds for hold in self.holds)

    def to_output(self, t: float) -> float:
        """Output time of source time t; a hold at t comes BEFORE the content at t.

        Example: Timeline((Hold(2.0, 1.5),)).to_output(3.0) -> 4.5
        """
        return t + sum(hold.seconds for hold in self.holds if hold.at <= t)

    def to_source(self, t_out: float) -> float:
        """Source time shown at output time t_out (inside a hold: the held frame).

        Example: Timeline((Hold(2.0, 1.5),)).to_source(2.7) -> 2.0 - 1/1000
        """
        shift = 0.0
        for hold in self.holds:
            begins = hold.at + shift
            if t_out < begins:
                break
            if t_out < begins + hold.seconds:
                return hold.shown
            shift += hold.seconds
        return t_out - shift


@dataclass(frozen=True)
class StepWindow:
    start: float  # output seconds
    end: float  # output seconds, hold included
    speech_at: float | None  # output seconds where its clip starts


@dataclass(frozen=True)
class Plan:
    timeline: Timeline
    windows: tuple[StepWindow, ...]
    duration: float  # output seconds


def plan_manual(steps: Sequence[Step], speech: Sequence[float | None], source_duration: float) -> Plan:
    """Insert holds so each step's speech fits, walking the steps in order.

    Example: plan_manual([Step(0, "a"), Step(1)], [2.0, None], 5.0).timeline.holds -> (Hold(0, 1.7, 0),)
    """
    if len(speech) != len(steps):
        raise ValueError(f"{len(speech)} speech lengths for {len(steps)} steps")
    holds: list[Hold] = []
    windows: list[StepWindow] = []
    shift = 0.0
    for index, (step, seconds) in enumerate(zip(steps, speech)):
        if not 0 <= step.at <= source_duration:
            raise ValueError(f"step {index} at={step.at} is outside the {source_duration:.2f}s recording")
        end = _step_end(steps, index, source_duration)
        needed = 0.0 if seconds is None else SPEECH_LEAD + seconds + SPEECH_TAIL
        extra = round(max(0.0, needed - (end - step.at)), 3)
        start_out = step.at + shift
        if extra and step.hold == "start":
            holds.append(Hold(step.at, extra, frame=step.at))  # the step's first frame, before its action
            shift += extra
        end_out = end + shift
        if extra and step.hold == "end":
            holds.append(Hold(end, extra))
            shift += extra
            end_out += extra
        speech_at = None if seconds is None else round(start_out + SPEECH_LEAD, 3)
        windows.append(StepWindow(round(start_out, 3), round(end_out, 3), speech_at))
    return Plan(Timeline(tuple(holds)), tuple(windows), round(source_duration + shift, 3))


def _step_end(steps: Sequence[Step], index: int, source_duration: float) -> float:
    step = steps[index]
    if step.until is not None:
        return min(step.until, source_duration)
    return steps[index + 1].at if index + 1 < len(steps) else source_duration


# --- drawing -----------------------------------------------------------------

_SYSTEM_FONTS = ("segoeui.ttf", "arial.ttf", "/System/Library/Fonts/Helvetica.ttc", "DejaVuSans.ttf")
_BOLD_FONTS = ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf")


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for name in (*(_BOLD_FONTS if bold else ()), *_SYSTEM_FONTS):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"color {color!r} must be '#rrggbb'")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


@dataclass(frozen=True)
class Sprite:
    image: FrameArray  # BGRA
    x: int
    y: int


def _to_sprite(image: Image.Image, x: int, y: int) -> Sprite:
    rgba = np.asarray(image)
    return Sprite(rgba[..., [2, 1, 0, 3]].copy(), x, y)


class Painter:
    """Pre-renders each annotation once (as a BGRA sprite) at the video's scale."""

    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.scale = max(0.6, height / 1080)
        self._cache: dict[int, Sprite] = {}

    def sprite(self, annotation: Annotation) -> Sprite:
        key = id(annotation)
        if key not in self._cache:
            self._cache[key] = getattr(self, f"_{annotation.kind}")(annotation)
        return self._cache[key]

    def _text(self, a: Annotation, caption: bool = False) -> Sprite:
        s = self.scale
        font = _font(round((30 if caption else 26) * s))
        wrap_px = a.rect.width if a.rect and a.rect.width else int(self.width * (0.7 if caption else 0.4))
        lines = _wrap(a.text, font, wrap_px)
        pad, bar = round(14 * s), (0 if caption else round(6 * s))
        line_h = round(font.size * 1.3)  # type: ignore[union-attr]
        text_w = max(font.getbbox(line)[2] for line in lines)
        width, height = text_w + 2 * pad + bar, line_h * len(lines) + 2 * pad - round(line_h * 0.2)
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=round(10 * s), fill=(18, 18, 22, 228))
        if bar:
            draw.rounded_rectangle((0, 0, bar + round(4 * s), height - 1), radius=round(10 * s), fill=(*_rgb(a.color), 255))
            draw.rectangle((bar, 0, bar + round(4 * s), height - 1), fill=(18, 18, 22, 228))
        for number, line in enumerate(lines):
            draw.text((bar + pad, pad + number * line_h), line, font=font, fill=(255, 255, 255, 255))
        if caption:
            return _to_sprite(image, (self.width - width) // 2, self.height - height - round(36 * s))
        assert a.rect is not None
        x = min(max(0, a.rect.x), max(0, self.width - width))
        y = min(max(0, a.rect.y), max(0, self.height - height))
        return _to_sprite(image, x, y)

    def caption(self, text: str, top: bool = False) -> Sprite:
        key = hash((text, top))
        if key not in self._cache:
            sprite = self._text(Annotation("text", text=text), caption=True)
            self._cache[key] = Sprite(sprite.image, sprite.x, round(36 * self.scale)) if top else sprite
        return self._cache[key]

    def _arrow(self, a: Annotation) -> Sprite:
        assert a.points is not None
        s = self.scale
        (x0, y0), (x1, y1) = a.points
        width, head = round(7 * s), round(30 * s)
        pad = head + round(8 * s)
        left, top = min(x0, x1) - pad, min(y0, y1) - pad
        image = Image.new("RGBA", (abs(x1 - x0) + 2 * pad, abs(y1 - y0) + 2 * pad), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        start, tip = np.array([x0 - left, y0 - top], float), np.array([x1 - left, y1 - top], float)
        direction = tip - start
        length = float(np.hypot(*direction)) or 1.0
        unit = direction / length
        normal = np.array([-unit[1], unit[0]])
        base = tip - unit * head
        triangle = [tuple(tip), tuple(base + normal * head * 0.55), tuple(base - normal * head * 0.55)]
        color = (*_rgb(a.color), 255)
        outline = round(3 * s)
        draw.line([tuple(start), tuple(base)], fill=(255, 255, 255, 235), width=width + 2 * outline)
        draw.polygon(_grow(triangle, outline), fill=(255, 255, 255, 235))
        draw.line([tuple(start), tuple(base)], fill=color, width=width)
        draw.polygon(triangle, fill=color)
        return _to_sprite(image, left, top)

    def _outline(self, a: Annotation, ellipse: bool) -> Sprite:
        assert a.rect is not None
        s = self.scale
        width = round(5 * s)
        pad = width + round(3 * s)
        image = Image.new("RGBA", (a.rect.width + 2 * pad, a.rect.height + 2 * pad), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        box = (pad, pad, pad + a.rect.width, pad + a.rect.height)
        halo = (box[0] - 2, box[1] - 2, box[2] + 2, box[3] + 2)
        if ellipse:
            draw.ellipse(halo, outline=(255, 255, 255, 200), width=width + 4)
            draw.ellipse(box, outline=(*_rgb(a.color), 255), width=width)
        else:
            radius = round(8 * s)
            draw.rounded_rectangle(halo, radius=radius + 2, outline=(255, 255, 255, 200), width=width + 4)
            draw.rounded_rectangle(box, radius=radius, outline=(*_rgb(a.color), 255), width=width)
        return _to_sprite(image, a.rect.x - pad, a.rect.y - pad)

    def _box(self, a: Annotation) -> Sprite:
        return self._outline(a, ellipse=False)

    def _ellipse(self, a: Annotation) -> Sprite:
        return self._outline(a, ellipse=True)

    def _badge(self, a: Annotation) -> Sprite:
        assert a.rect is not None
        s = self.scale
        size = round(46 * s)
        font = _font(round(26 * s), bold=True)
        image = Image.new("RGBA", (size + 4, size + 4), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((0, 0, size + 3, size + 3), fill=(255, 255, 255, 235))
        draw.ellipse((2, 2, size + 1, size + 1), fill=(*_rgb(a.color), 255))
        draw.text(((size + 4) / 2, (size + 4) / 2), a.text, font=font, fill=(255, 255, 255, 255), anchor="mm")
        return _to_sprite(image, a.rect.x - (size + 4) // 2, a.rect.y - (size + 4) // 2)


def _wrap(text: str, font: ImageFont.ImageFont | ImageFont.FreeTypeFont, max_px: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        words, line = paragraph.split(), ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if font.getbbox(candidate)[2] <= max_px or not line:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines or [""]


def _grow(triangle: list[tuple[float, float]], by: float) -> list[tuple[float, float]]:
    cx = sum(p[0] for p in triangle) / 3
    cy = sum(p[1] for p in triangle) / 3
    out = []
    for x, y in triangle:
        dx, dy = x - cx, y - cy
        norm = (dx * dx + dy * dy) ** 0.5 or 1.0
        out.append((x + dx / norm * by * 1.8, y + dy / norm * by * 1.8))
    return out


def fade(t: float, start: float, end: float) -> float:
    """Opacity at t for something shown from start to end, fading in and out.

    Example: fade(1.0, 0.0, 5.0) -> 1.0; fade(0.1, 0.0, 5.0) -> 0.4
    """
    if t < start or t >= end:
        return 0.0
    return max(0.0, min(1.0, (t - start) / FADE_SECONDS, (end - t) / FADE_SECONDS))


def spotlight(frame: FrameArray, rect: Rect, alpha: float) -> None:
    """Dim everything outside rect, in place."""
    keep = frame[max(0, rect.y) : rect.y + rect.height, max(0, rect.x) : rect.x + rect.width, :3].copy()
    frame[..., :3] = (frame[..., :3].astype(np.float32) * (1 - 0.6 * alpha)).astype(np.uint8)
    frame[max(0, rect.y) : rect.y + rect.height, max(0, rect.x) : rect.x + rect.width, :3] = keep


def pixelate(frame: FrameArray, rect: Rect, block: int) -> None:
    """Hide a region (passwords, tokens) behind coarse blocks, in place."""
    x0, y0 = max(0, rect.x), max(0, rect.y)
    x1, y1 = min(frame.shape[1], rect.x + rect.width), min(frame.shape[0], rect.y + rect.height)
    for y in range(y0, y1, block):
        for x in range(x0, x1, block):
            cell = frame[y : min(y + block, y1), x : min(x + block, x1), :3]
            cell[...] = cell.reshape(-1, 3).mean(axis=0).astype(np.uint8)


@dataclass
class Overlay:
    """Everything to draw, already on the output timeline."""

    shapes: list[tuple[Annotation, float, float]] = field(default_factory=list)  # annotation, start, end
    captions: list[tuple[str, float, float, bool]] = field(default_factory=list)  # text, start, end, at top


def annotation_bottom(item: Annotation) -> int:
    """Lowest video y an annotation reaches (text blocks are assumed ~2 lines tall)."""
    if item.points is not None:
        return max(item.points[0][1], item.points[1][1])
    if item.rect is None:
        return 0
    return item.rect.y + (item.rect.height or 90)


def build_overlay(steps: Sequence[Step], plan: Plan, clips: Sequence[SpeechClip | None], burn: bool,
                  video_height: int = 1080) -> Overlay:  # fmt: skip
    overlay = Overlay()
    for step, window, clip in zip(steps, plan.windows, clips):
        # Subtitles live at the bottom unless this step points at something down there.
        top = any(annotation_bottom(i) > video_height * 0.72 for i in step.items if i.kind != "spotlight")
        for item in step.items:
            start = window.start + item.start
            end = window.end if item.end is None else min(window.end, window.start + item.end)
            overlay.shapes.append((item, start, end))
        if burn and clip is not None and window.speech_at is not None:
            for sentence in clip.sentences or (_whole(clip),):
                start, end = window.speech_at + sentence.start, window.speech_at + sentence.end + 0.2
                overlay.captions.append((sentence.text, start, end, top))
    return overlay


def _whole(clip: SpeechClip) -> Sentence:
    """One subtitle for the whole clip when the voice reported no sentence boundaries."""
    return Sentence(0.0, clip.duration, clip.text)


def draw_overlay(frame: FrameArray, overlay: Overlay, painter: Painter, t: float) -> None:
    # Spotlight and blur change the picture itself, so they go first; sprites are drawn on top.
    for item, start, end in overlay.shapes:
        alpha = fade(t, start, end)
        if alpha <= 0:
            continue
        if item.kind == "spotlight" and item.rect is not None:
            spotlight(frame, item.rect, alpha)
        elif item.kind == "blur" and item.rect is not None:
            pixelate(frame, item.rect, max(8, round(14 * painter.scale)))
    for item, start, end in overlay.shapes:
        alpha = fade(t, start, end)
        if alpha > 0 and item.kind not in ("spotlight", "blur"):
            sprite = painter.sprite(item)
            blend_image(frame, sprite.image, sprite.x, sprite.y, alpha)
    for text, start, end, top in overlay.captions:
        alpha = fade(t, start, end)
        if alpha > 0:
            sprite = painter.caption(text, top)
            blend_image(frame, sprite.image, sprite.x, sprite.y, alpha)


# --- subtitles ---------------------------------------------------------------


def srt_time(seconds: float) -> str:
    """Example: srt_time(83.25) -> "00:01:23,250" """
    millis = round(seconds * 1000)
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def build_srt(plan: Plan, clips: Sequence[SpeechClip | None]) -> str:
    blocks = []
    for window, clip in zip(plan.windows, clips):
        if clip is None or window.speech_at is None:
            continue
        for sentence in clip.sentences or (_whole(clip),):
            start, end = window.speech_at + sentence.start, window.speech_at + sentence.end
            blocks.append(f"{len(blocks) + 1}\n{srt_time(start)} --> {srt_time(end)}\n{sentence.text}\n")
    return "\n".join(blocks)


# --- rendering ---------------------------------------------------------------


@dataclass(frozen=True)
class SourceVideo:
    width: int
    height: int
    fps: float
    duration: float
    has_audio: bool


def probe_source(ffprobe: str, path: Path, runner: Callable[[list[str]], subprocess.CompletedProcess[bytes]]) -> SourceVideo:
    """Size, frame rate, duration and audio presence of the recording to annotate."""
    args = [ffprobe, "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate:format=duration",
            "-of", "json", str(path)]  # fmt: skip
    result = runner(args)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe of {path} exited {result.returncode}: {(result.stderr or b'').decode(errors='replace')}")
    document = json.loads(result.stdout.decode(errors="replace"))
    streams = document.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise RuntimeError(f"{path} has no video stream")
    numerator, _, denominator = str(video.get("r_frame_rate", "30/1")).partition("/")
    fps = float(numerator) / float(denominator or 1) if float(denominator or 1) else 30.0
    return SourceVideo(
        int(video["width"]), int(video["height"]), round(fps, 3) if 1 <= fps <= 240 else 30.0,
        float(document.get("format", {}).get("duration") or 0), any(s.get("codec_type") == "audio" for s in streams),
    )


@dataclass(frozen=True)
class ManualResult:
    output: Path
    subtitles: Path | None
    duration: float
    holds: tuple[Hold, ...]
    narrated_steps: int


def render_manual(
    source: Path, video: SourceVideo, steps: Sequence[Step], output: Path, ffmpeg: str,
    narrator: Narrator | None = None, voice: str = DEFAULT_VOICE, rate: str = "+0%", subtitles: str = "file",
    progress: Callable[[float], None] | None = None,
) -> ManualResult:  # fmt: skip
    """Synthesize, plan the holds, draw every output frame, mix the audio, write <output> (+ .srt)."""
    if subtitles not in SUBTITLE_MODES:
        raise ValueError(f"subtitles must be one of {list(SUBTITLE_MODES)}, got {subtitles!r}")
    wants_voice = any(step.say for step in steps)
    if wants_voice and narrator is None:
        raise ValueError("steps have 'say' but narration is off; pass narrate=true or drop 'say'")
    clips = [narrator.speak(step.say, voice, rate) if (narrator and step.say) else None for step in steps]
    plan = plan_manual(steps, [None if clip is None else clip.duration for clip in clips], video.duration)
    overlay = build_overlay(steps, plan, clips, burn=subtitles == "burn", video_height=video.height)
    silent = output.with_name(f"{output.stem}.video-only.mp4")
    _render_video(source, video, plan, overlay, silent, ffmpeg, progress)
    try:
        _mux_audio(source, video, plan, clips, silent, output, ffmpeg)
    finally:
        silent.unlink(missing_ok=True)
    srt_path = None
    if subtitles != "none" and any(clips):
        srt_path = output.with_suffix(".srt")
        srt_path.write_text(build_srt(plan, clips), encoding="utf-8")
    return ManualResult(output, srt_path, plan.duration, plan.timeline.holds, sum(1 for c in clips if c))


def _render_video(source: Path, video: SourceVideo, plan: Plan, overlay: Overlay, output: Path, ffmpeg: str,
                  progress: Callable[[float], None] | None) -> None:  # fmt: skip
    width, height, fps = video.width, video.height, video.fps
    frame_bytes = width * height * 4
    decoder = subprocess.Popen(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(source), "-an", "-vf", f"fps={fps}",
         "-f", "rawvideo", "-pix_fmt", "bgra", "-"],
        stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, creationflags=hidden_window_flags(),
    )  # fmt: skip
    encoder = subprocess.Popen(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgra",
         "-s", f"{width}x{height}", "-framerate", f"{fps}", "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, creationflags=hidden_window_flags(),
    )  # fmt: skip
    assert decoder.stdout is not None and encoder.stdin is not None
    painter = Painter(width, height)
    total = max(1, round(plan.duration * fps))
    current: FrameArray | None = None
    current_index = -1
    try:
        for n in range(total):
            t_out = n / fps
            wanted = min(int(plan.timeline.to_source(t_out) * fps + 1e-6), round(video.duration * fps) - 1)
            while current_index < wanted:
                raw = decoder.stdout.read(frame_bytes)
                if len(raw) < frame_bytes:
                    break  # source ran out a frame early: keep the last one
                current = np.frombuffer(raw, np.uint8).reshape(height, width, 4)
                current_index += 1
            if current is None:
                raise RuntimeError(f"ffmpeg decoded no frames from {source}")
            frame = current.copy()
            draw_overlay(frame, overlay, painter, t_out)
            encoder.stdin.write(frame.tobytes())
            if progress is not None and n % max(1, round(fps)) == 0:
                progress(n / total)
    finally:
        encoder.stdin.close()
        decoder.stdout.close()
        decoder.kill()
        decoder.wait()
        if encoder.wait(timeout=300) != 0:
            raise RuntimeError(f"ffmpeg could not encode {output}")


def audio_filter(video: SourceVideo, plan: Plan, clip_count: int, first_clip_input: int,
                 clip_starts: Sequence[float]) -> str:  # fmt: skip
    """filter_complex that re-times the original audio around the holds and lays the clips on top.

    Example: audio_filter(video_without_audio, plan, 1, 1, [0.3]) -> "[1:a]adelay=300:all=1[n0];[n0]anull[aout]"
    """
    parts, labels = [], []
    if video.has_audio:
        cuts = [0.0, *(hold.at for hold in plan.timeline.holds), video.duration]
        for k, (a, b) in enumerate(zip(cuts, cuts[1:])):
            if b <= a:
                continue
            delay = round(plan.timeline.to_output(a) * 1000)
            parts.append(f"[1:a]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS,adelay={delay}:all=1[o{k}]")
            labels.append(f"[o{k}]")
    for k, start in enumerate(clip_starts):
        parts.append(f"[{first_clip_input + k}:a]adelay={round(start * 1000)}:all=1[n{k}]")
        labels.append(f"[n{k}]")
    if len(labels) == 1:
        parts.append(f"{labels[0]}anull[aout]")
    else:
        parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest[aout]")
    return ";".join(parts)


def _mux_audio(source: Path, video: SourceVideo, plan: Plan, clips: Sequence[SpeechClip | None], silent: Path,
               output: Path, ffmpeg: str) -> None:  # fmt: skip
    voiced = [(clip, window) for clip, window in zip(clips, plan.windows) if clip is not None and window.speech_at is not None]
    if not voiced and not video.has_audio:
        silent.replace(output)
        return
    inputs = ["-i", str(silent)]
    if video.has_audio:
        inputs += ["-i", str(source)]
    first_clip = 2 if video.has_audio else 1
    for clip, _ in voiced:
        inputs += ["-i", str(clip.path)]  # type: ignore[union-attr]
    graph = audio_filter(video, plan, len(voiced), first_clip, [w.speech_at for _, w in voiced])  # type: ignore[misc]
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex", graph,
         "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
         "-t", f"{plan.duration:.3f}", "-movflags", "+faststart", str(output)],
        stdin=subprocess.DEVNULL, capture_output=True, creationflags=hidden_window_flags(), check=False,
    )  # fmt: skip
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio mix for {output} failed: {result.stderr.decode(errors='replace')[-400:]}")

