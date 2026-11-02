"""Still frames from a finished recording, picked and sized so an AI model can look at them.

ffmpeg selects and decodes the frames (full resolution PNGs on disk); Pillow makes the
small inline copies and the contact sheet, so no ffmpeg font setup (drawtext) is needed.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.media_tools import CommandRunner, run_command

_PTS_TIME = re.compile(r"Parsed_showinfo.*?\bpts_time:\s*(-?[\d.]+)")
_FRAME_GLOB = "frame-*.png"
_LABEL_HEIGHT = 22
_SHEET_GAP = 4


class FrameMode(str, Enum):
    CHANGES = "changes"  # drop frames identical to the last kept one (mpdecimate)
    SCENES = "scenes"  # only frames that differ a lot from the previous one
    INTERVAL = "interval"  # one frame every `interval_seconds`
    TIMESTAMPS = "timestamps"  # exactly the requested moments


@dataclass(frozen=True)
class FrameRequest:
    mode: FrameMode = FrameMode.CHANGES
    interval_seconds: float = 1.0
    scene_threshold: float = 0.1
    timestamps: Sequence[float] = ()
    start_seconds: float | None = None
    end_seconds: float | None = None
    max_frames: int = 12


@dataclass(frozen=True)
class Frame:
    seconds: float
    path: Path  # full-resolution PNG


@dataclass(frozen=True)
class FrameSet:
    frames: list[Frame]
    found: int  # frames the filter selected before thinning to max_frames
    directory: Path


def select_filter(request: FrameRequest) -> str:
    """ffmpeg -vf chain for CHANGES / SCENES / INTERVAL; showinfo logs each kept frame's time.

    Example: select_filter(FrameRequest(FrameMode.SCENES, scene_threshold=0.2))
             -> "select='eq(n\\,0)+gt(scene\\,0.2)',showinfo"
    """
    if request.mode is FrameMode.CHANGES:
        chain = "mpdecimate"
    elif request.mode is FrameMode.SCENES:
        chain = f"select='eq(n\\,0)+gt(scene\\,{request.scene_threshold})'"
    elif request.mode is FrameMode.INTERVAL:
        step = request.interval_seconds
        chain = f"select='isnan(prev_selected_t)+gte(t-prev_selected_t\\,{step})'"
    else:
        raise ValueError(f"select_filter() has no filter for mode {request.mode.value!r}")
    trim = _trim_filter(request.start_seconds, request.end_seconds)
    return ",".join(part for part in (trim, chain, "showinfo") if part)


def _trim_filter(start: float | None, end: float | None) -> str:
    bounds = [f"start={start}" if start is not None else "", f"end={end}" if end is not None else ""]
    joined = ":".join(bound for bound in bounds if bound)
    return f"trim={joined}" if joined else ""


def build_select_args(ffmpeg: str, source: Path, request: FrameRequest, out_dir: Path) -> list[str]:
    """Decode the selected frames to out_dir/frame-0001.png …; showinfo needs loglevel info.

    -copyts keeps the recording's own timestamps when -ss fast-seeks to start_seconds.
    """
    seek = ["-ss", f"{request.start_seconds:.3f}"] if request.start_seconds else []
    return [
        ffmpeg, "-hide_banner", "-loglevel", "info", "-nostats", "-y",
        *seek, "-copyts", "-i", str(source), "-an",
        "-vf", select_filter(request), "-fps_mode", "vfr",
        str(out_dir / "frame-%04d.png"),
    ]  # fmt: skip


def build_single_frame_args(ffmpeg: str, source: Path, seconds: float, output: Path) -> list[str]:
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{seconds:.3f}", "-i", str(source), "-an", "-frames:v", "1", str(output),
    ]  # fmt: skip


def parse_frame_times(stderr: str) -> list[float]:
    """pts_time of every frame showinfo printed, in output order.

    Example: parse_frame_times("[Parsed_showinfo_2 @ 0x1] n: 0 pts: 512 pts_time:0.033 ...") -> [0.033]
    """
    return [float(match) for match in _PTS_TIME.findall(stderr)]


def evenly_spaced(count: int, limit: int) -> list[int]:
    """Indices of `limit` items spread over `count`, always keeping the first and last.

    Example: evenly_spaced(10, 4) -> [0, 3, 6, 9]
    """
    if limit < 1:
        raise ValueError(f"max_frames={limit} is invalid; expected an integer >= 1")
    if count <= limit:
        return list(range(count))
    if limit == 1:
        return [0]
    return [round(i * (count - 1) / (limit - 1)) for i in range(limit)]


def frames_dir_for(source: Path) -> Path:
    """First free '<stem>-frames/N' folder next to the recording, so earlier samples stay readable.

    Example: frames_dir_for(Path("v/rec.mp4")) -> v/rec-frames/1
    """
    base = source.with_name(f"{source.stem}-frames")
    counter = 1
    while (base / str(counter)).exists():
        counter += 1
    return base / str(counter)


class FrameExtractor:
    """ffmpeg-backed frame sampling; the runner is injected so tests need no binaries.

    Example: FrameExtractor("ffmpeg").sample(Path("rec.mp4"), FrameRequest(max_frames=8))
    """

    def __init__(self, ffmpeg: str, runner: CommandRunner = run_command) -> None:
        self._ffmpeg = ffmpeg
        self._run = runner

    def sample(self, source: Path, request: FrameRequest) -> FrameSet:
        if not source.exists():
            raise FileNotFoundError(f"recording {source} does not exist")
        out_dir = frames_dir_for(source)
        out_dir.mkdir(parents=True)
        if request.mode is FrameMode.TIMESTAMPS:
            frames = self._at_timestamps(source, request.timestamps[: request.max_frames], out_dir)
            return FrameSet(frames, len(request.timestamps), out_dir)
        result = self._run(build_select_args(self._ffmpeg, source, request, out_dir))
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg frame sampling of {source} exited {result.returncode}: {_tail(result.stderr)}")
        paths = sorted(out_dir.glob(_FRAME_GLOB))
        times = parse_frame_times((result.stderr or b"").decode(errors="replace"))
        if len(times) != len(paths):
            raise RuntimeError(f"ffmpeg wrote {len(paths)} frames but logged {len(times)} timestamps for {source}")
        keep = set(evenly_spaced(len(paths), request.max_frames))
        for index, path in enumerate(paths):
            if index not in keep:
                path.unlink()
        frames = [Frame(round(times[i], 3), paths[i]) for i in sorted(keep)]
        return FrameSet(frames, len(paths), out_dir)

    def _at_timestamps(self, source: Path, timestamps: Sequence[float], out_dir: Path) -> list[Frame]:
        if not timestamps:
            raise ValueError("mode='timestamps' needs a non-empty `timestamps` list of seconds")
        frames = []
        for number, seconds in enumerate(timestamps, start=1):
            output = out_dir / f"frame-{number:04d}.png"
            result = self._run(build_single_frame_args(self._ffmpeg, source, seconds, output))
            if result.returncode != 0 or not output.exists():
                raise RuntimeError(f"ffmpeg found no frame at {seconds}s in {source}: {_tail(result.stderr)}")
            frames.append(Frame(round(float(seconds), 3), output))
        return frames


def _tail(stderr: bytes | None) -> str:
    """Last lines only: with loglevel info the full stderr is mostly banner and stream info."""
    lines = (stderr or b"").decode(errors="replace").strip().splitlines()
    return "\n".join(lines[-5:])


def format_seconds(seconds: float) -> str:
    """Example: format_seconds(83.25) -> "1:23.250" """
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)}:{rest:06.3f}"


def inline_jpeg(path: Path, max_width: int, crop: Rect | None = None, quality: int = 80) -> bytes:
    """JPEG bytes of a frame, optionally cropped, never wider than max_width."""
    with Image.open(path) as image:
        picture = image.convert("RGB")
    if crop is not None:
        picture = picture.crop(_clamped_box(crop, picture.width, picture.height))
    picture = _fit_width(picture, max_width)
    buffer = io.BytesIO()
    picture.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def _clamped_box(crop: Rect, width: int, height: int) -> tuple[int, int, int, int]:
    left, top = max(0, crop.x), max(0, crop.y)
    right, bottom = min(width, crop.x + crop.width), min(height, crop.y + crop.height)
    if right <= left or bottom <= top:
        raise ValueError(f"crop {crop.as_dict()} lies outside the {width}x{height} frame")
    return left, top, right, bottom


def _fit_width(picture: Image.Image, max_width: int) -> Image.Image:
    if picture.width <= max_width:
        return picture
    height = round(picture.height * max_width / picture.width)
    return picture.resize((max_width, height), Image.Resampling.LANCZOS)


def build_contact_sheet(frames: Sequence[Frame], columns: int, tile_width: int, output: Path) -> Path:
    """Grid of frames, each captioned with its time, saved as a PNG.

    Example: build_contact_sheet(frame_set.frames, 4, 320, Path("sheet.png"))
    """
    if not frames:
        raise ValueError("contact sheet needs at least one frame; the filter selected none")
    if columns < 1:
        raise ValueError(f"columns={columns} is invalid; expected an integer >= 1")
    tiles = []
    for frame in frames:
        with Image.open(frame.path) as image:
            tiles.append(_fit_width(image.convert("RGB"), tile_width))
    tile_w = max(tile.width for tile in tiles)
    tile_h = max(tile.height for tile in tiles) + _LABEL_HEIGHT
    columns = min(columns, len(tiles))
    rows = -(-len(tiles) // columns)
    sheet = Image.new(
        "RGB",
        (columns * tile_w + (columns + 1) * _SHEET_GAP, rows * tile_h + (rows + 1) * _SHEET_GAP),
        (24, 24, 24),
    )
    draw = ImageDraw.Draw(sheet)
    font = _label_font()
    for index, (frame, tile) in enumerate(zip(frames, tiles)):
        row, column = divmod(index, columns)
        x = _SHEET_GAP + column * (tile_w + _SHEET_GAP)
        y = _SHEET_GAP + row * (tile_h + _SHEET_GAP)
        draw.text((x + 4, y + 3), f"#{index + 1}  {format_seconds(frame.seconds)}", fill=(255, 220, 90), font=font)
        sheet.paste(tile, (x, y + _LABEL_HEIGHT))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)
    return output


def _label_font() -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=15)
    except TypeError:  # Pillow < 10.1 has no sized default font
        return ImageFont.load_default()
