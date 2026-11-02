"""Burn input feedback into the video: a halo around the cursor, click ripples and keystroke chips.

Drawn into each frame before it reaches ffmpeg, so it also works for window recordings (an
on-screen overlay window would not be part of a window capture). The OS part only reports
input (see InputTracker); everything here is platform-neutral numpy + Pillow.

Privacy: keys are kept in memory for a few seconds to draw the chips and are never logged.
KeyMode.ALL shows whatever is typed, passwords included, so it is opt-in.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from screen_recorder_mcp.geometry import FrameArray, Rect


class KeyMode(str, Enum):
    OFF = "off"
    SHORTCUTS = "shortcuts"  # keys pressed with Ctrl / Alt / Win held, and F1-F24
    ALL = "all"  # every key, typed text included


@dataclass(frozen=True)
class OverlayOptions:
    cursor_halo: bool = False
    show_clicks: bool = False
    show_keys: KeyMode = KeyMode.OFF

    @property
    def enabled(self) -> bool:
        return self.cursor_halo or self.show_clicks or self.show_keys is not KeyMode.OFF

    @property
    def wants_keys(self) -> bool:
        return self.show_keys is not KeyMode.OFF


# --- input events ------------------------------------------------------------

MODIFIERS = ("Ctrl", "Alt", "Shift", "Win")
CHIP_SECONDS = 2.0  # how long a keystroke chip stays (it fades over the last third)
RIPPLE_SECONDS = 0.45
MAX_CHIPS = 4
_TYPING_MERGE_SECONDS = 1.0


@dataclass(frozen=True)
class Click:
    at: float  # perf_counter seconds
    x: int  # screen pixels
    y: int
    button: str  # "left" | "right" | "middle"


@dataclass
class Chip:
    text: str
    at: float  # last update; the chip fades relative to this
    typing: bool = False  # plain characters keep extending the same chip
    count: int = 1  # the same shortcut pressed again (or auto-repeat) shows as "Ctrl + Z ×3"

    @property
    def label(self) -> str:
        return self.text if self.count == 1 else f"{self.text} ×{self.count}"


@dataclass
class InputSnapshot:
    cursor: tuple[int, int] | None
    buttons_down: frozenset[str]
    clicks: list[Click]
    chips: list[Chip]


class KeyChips:
    """Turns key presses into the chips shown on screen.

    Example: chips.press("C", {"Ctrl"}, 1.0) -> chip "Ctrl + C"; typing "h","i" -> one chip "hi"
    """

    def __init__(self, mode: KeyMode) -> None:
        self._mode = mode
        self._chips: list[Chip] = []

    def press(self, key: str, held: set[str], now: float, char: str | None = None) -> None:
        """key: display name ("A", "Enter", "F5"); char: the typed character, if any."""
        if key in MODIFIERS or self._mode is KeyMode.OFF:
            return
        command = held & {"Ctrl", "Alt", "Win"}
        if command or (key.startswith("F") and key[1:].isdigit()):
            self._add(" + ".join([*(m for m in MODIFIERS if m in held), key]), now)
            return
        if self._mode is not KeyMode.ALL:
            return
        if char is not None and char.isprintable():
            last = self._chips[-1] if self._chips else None
            if last is not None and last.typing and now - last.at <= _TYPING_MERGE_SECONDS:
                last.text, last.at = (last.text + char)[-24:], now
                return
            self._add(char, now, typing=True)
            return
        self._add(" + ".join([*(m for m in ("Shift",) if m in held), key]), now)

    def _add(self, text: str, now: float, typing: bool = False) -> None:
        last = self._chips[-1] if self._chips else None
        if last is not None and not typing and not last.typing and last.text == text and now - last.at < CHIP_SECONDS:
            last.count, last.at = last.count + 1, now
            return
        self._chips.append(Chip(text, now, typing))
        del self._chips[:-MAX_CHIPS]

    def visible(self, now: float) -> list[Chip]:
        self._chips = [chip for chip in self._chips if now - chip.at < CHIP_SECONDS]
        return [Chip(c.text, c.at, c.typing, c.count) for c in self._chips]


class NullInputTracker:
    """For platforms without input hooks yet: reports nothing, so nothing is drawn."""

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def snapshot(self, now: float) -> InputSnapshot:
        return InputSnapshot(None, frozenset(), [], [])


class InputTracker(Protocol):
    """OS input feed for one recording. start/stop bracket the recording; snapshot is thread-safe."""

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def snapshot(self, now: float) -> InputSnapshot: ...


@dataclass
class InputLog:
    """Thread-safe state the OS hooks write into and the frame pacer reads from."""

    options: OverlayOptions
    cursor_position: Callable[[], tuple[int, int] | None]
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _buttons: set[str] = field(default_factory=set)
    _held: set[str] = field(default_factory=set)
    _clicks: list[Click] = field(default_factory=list)
    _chips: KeyChips | None = None

    def __post_init__(self) -> None:
        self._chips = KeyChips(self.options.show_keys)

    def mouse_down(self, button: str, x: int, y: int, now: float | None = None) -> None:
        with self._lock:
            self._buttons.add(button)
            self._clicks.append(Click(now if now is not None else time.perf_counter(), x, y, button))
            del self._clicks[:-8]

    def mouse_up(self, button: str) -> None:
        with self._lock:
            self._buttons.discard(button)

    def key_down(self, key: str, char: str | None = None, now: float | None = None) -> None:
        with self._lock:
            if key in MODIFIERS:
                self._held.add(key)
                return
            assert self._chips is not None
            self._chips.press(key, set(self._held), now if now is not None else time.perf_counter(), char)

    def key_up(self, key: str) -> None:
        with self._lock:
            self._held.discard(key)

    def snapshot(self, now: float) -> InputSnapshot:
        with self._lock:
            assert self._chips is not None
            self._clicks = [c for c in self._clicks if now - c.at < RIPPLE_SECONDS]
            return InputSnapshot(
                self.cursor_position(), frozenset(self._buttons), list(self._clicks), self._chips.visible(now)
            )


# --- geometry ----------------------------------------------------------------


@dataclass(frozen=True)
class FrameMapping:
    """Screen pixel → output-video pixel, for the target's CURRENT position (windows move).

    frame_size: captured frame size (before crop); crop: in frame pixels or None.
    """

    target_rect: Callable[[], Rect]
    frame_size: tuple[int, int]
    crop: Rect | None

    def to_output(self, x: int, y: int) -> tuple[float, float, float]:
        """(x, y, scale) in output pixels; scale is output pixels per screen pixel.

        Example: FrameMapping(lambda: Rect(100, 50, 800, 600), (1600, 1200), None).to_output(500, 350)
                 -> (800.0, 600.0, 2.0)
        """
        rect = self.target_rect()
        scale = self.frame_size[0] / rect.width if rect.width > 0 else 1.0
        fx, fy = (x - rect.x) * scale, (y - rect.y) * scale
        if self.crop is not None:
            fx, fy = fx - self.crop.x, fy - self.crop.y
        return fx, fy, scale


# --- drawing -----------------------------------------------------------------

_HALO_BGR = (60, 220, 255)  # warm yellow
_LEFT_BGR = (255, 170, 60)  # blue
_RIGHT_BGR = (40, 140, 255)  # orange
_MIDDLE_BGR = (200, 120, 200)
_BUTTON_COLORS = {"left": _LEFT_BGR, "right": _RIGHT_BGR, "middle": _MIDDLE_BGR}
_HALO_RADIUS = 30  # screen pixels; scaled with the capture
_HALO_ALPHA = 0.35


def blend_disc(frame: FrameArray, cx: float, cy: float, radius: float, bgr: tuple[int, int, int],
               alpha: float, ring: float | None = None) -> None:  # fmt: skip
    """Alpha-blend a filled disc (or a ring of width `ring`) into a BGRA frame, in place, clipped."""
    height, width = frame.shape[:2]
    x0, x1 = max(0, int(cx - radius - 1)), min(width, int(cx + radius + 2))
    y0, y1 = max(0, int(cy - radius - 1)), min(height, int(cy + radius + 2))
    if x0 >= x1 or y0 >= y1 or alpha <= 0:
        return
    ys, xs = np.ogrid[y0:y1, x0:x1]
    distance = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
    coverage = np.clip(radius + 0.5 - distance, 0.0, 1.0)  # 1 px anti-aliased edge
    if ring is not None:
        coverage *= np.clip(distance - (radius - ring) + 0.5, 0.0, 1.0)
    weight = (coverage * alpha)[..., None]
    region = frame[y0:y1, x0:x1, :3].astype(np.float32)
    color = np.array(bgr, dtype=np.float32)
    frame[y0:y1, x0:x1, :3] = (region * (1 - weight) + color * weight).astype(np.uint8)


def blend_image(frame: FrameArray, image: FrameArray, x: int, y: int, alpha: float) -> None:
    """Alpha-blend a BGRA image (its own alpha times `alpha`) at (x, y), clipped to the frame."""
    height, width = frame.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(width, x + image.shape[1]), min(height, y + image.shape[0])
    if x0 >= x1 or y0 >= y1:
        return
    patch = image[y0 - y : y1 - y, x0 - x : x1 - x].astype(np.float32)
    weight = patch[..., 3:4] / 255.0 * alpha
    region = frame[y0:y1, x0:x1, :3].astype(np.float32)
    frame[y0:y1, x0:x1, :3] = (region * (1 - weight) + patch[..., :3] * weight).astype(np.uint8)


class ChipPainter:
    """Renders keystroke chips with Pillow's bundled font (no font files needed); cached by text."""

    def __init__(self, font_px: int) -> None:
        self._font_px = font_px
        self._font = _font(font_px)
        self._cache: dict[str, FrameArray] = {}

    def chip(self, text: str) -> FrameArray:
        cached = self._cache.get(text)
        if cached is not None:
            return cached
        pad_x, pad_y = self._font_px * 0.7, self._font_px * 0.45
        left, top, right, bottom = self._font.getbbox(text)
        width, height = int(right - left + 2 * pad_x), int(self._font_px + 2 * pad_y)
        image = Image.new("RGBA", (max(width, height), height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, image.width - 1, height - 1), radius=height // 3, fill=(20, 20, 24, 215))
        draw.text((pad_x - left, (height - (bottom - top)) / 2 - top), text, font=self._font, fill=(255, 255, 255, 255))
        rgba = np.asarray(image)
        bgra = rgba[..., [2, 1, 0, 3]].copy()
        if len(self._cache) > 64:
            self._cache.clear()
        self._cache[text] = bgra
        return bgra


# Pillow's bundled font is Latin-only; typed Cyrillic or Greek needs a system font.
_SYSTEM_FONTS = ("segoeui.ttf", "arial.ttf", "/System/Library/Fonts/Helvetica.ttc", "DejaVuSans.ttf")


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for name in _SYSTEM_FONTS:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 has no sized default font
        return ImageFont.load_default()


class OverlayRenderer:
    """Draws one InputSnapshot onto a frame copy. Example: renderer.draw(frame, snapshot, now)"""

    def __init__(self, options: OverlayOptions, mapping: FrameMapping, output_size: tuple[int, int]) -> None:
        self._options = options
        self._mapping = mapping
        self._output_size = output_size
        self._chips = ChipPainter(max(14, round(output_size[1] / 34)))

    def draw(self, frame: FrameArray, snapshot: InputSnapshot, now: float) -> None:
        if snapshot.cursor is not None and (self._options.cursor_halo or self._options.show_clicks):
            x, y, scale = self._mapping.to_output(*snapshot.cursor)
            if self._options.cursor_halo:
                blend_disc(frame, x, y, _HALO_RADIUS * scale, _HALO_BGR, _HALO_ALPHA)
            if self._options.show_clicks:
                for button in snapshot.buttons_down:
                    blend_disc(frame, x, y, 9 * scale, _BUTTON_COLORS.get(button, _LEFT_BGR), 0.75)
        if self._options.show_clicks:
            for click in snapshot.clicks:
                self._ripple(frame, click, now)
        if self._options.wants_keys and snapshot.chips:
            self._draw_chips(frame, snapshot.chips, now)

    def _ripple(self, frame: FrameArray, click: Click, now: float) -> None:
        progress = (now - click.at) / RIPPLE_SECONDS
        if not 0 <= progress < 1:
            return
        x, y, scale = self._mapping.to_output(click.x, click.y)
        radius = (10 + 34 * progress) * scale
        bgr = _BUTTON_COLORS.get(click.button, _LEFT_BGR)
        blend_disc(frame, x, y, radius, bgr, 0.9 * (1 - progress), ring=max(2.0, 4 * scale))

    def _draw_chips(self, frame: FrameArray, chips: list[Chip], now: float) -> None:
        images = [(self._chips.chip(chip.label), chip_alpha(now - chip.at)) for chip in chips]
        gap = max(6, self._chips._font_px // 2)  # noqa: SLF001 - same painter, sizing only
        total = sum(image.shape[1] for image, _ in images) + gap * (len(images) - 1)
        width, height = self._output_size
        x = (width - total) // 2
        y = height - images[0][0].shape[0] - max(12, height // 18)
        for image, alpha in images:
            blend_image(frame, image, x, y, alpha)
            x += image.shape[1] + gap


def chip_alpha(age: float) -> float:
    """Fully opaque for the first two thirds, then fades out.

    Example: chip_alpha(0.5) -> 1.0; chip_alpha(CHIP_SECONDS) -> 0.0
    """
    fade_from = CHIP_SECONDS * 2 / 3
    if age <= fade_from:
        return 1.0
    return max(0.0, 1 - (age - fade_from) / (CHIP_SECONDS - fade_from))


class OverlayPipeline:
    """Tracker + renderer for one recording; RecordingSession binds it once the frame size is known.

    Example: decorate = pipeline.bind((1920, 1080), None, (1920, 1080)); frame = decorate(frame)
    """

    def __init__(self, options: OverlayOptions, tracker: InputTracker, target_rect: Callable[[], Rect],
                 clock: Callable[[], float] = time.perf_counter) -> None:  # fmt: skip
        self.options = options
        self._tracker = tracker
        self._target_rect = target_rect
        self._clock = clock

    def start(self) -> None:
        self._tracker.start()

    def stop(self) -> None:
        self._tracker.stop()

    def bind(
        self, frame_size: tuple[int, int], crop: Rect | None, output_size: tuple[int, int]
    ) -> Callable[[FrameArray], FrameArray]:
        renderer = OverlayRenderer(self.options, FrameMapping(self._target_rect, frame_size, crop), output_size)

        def decorate(frame: FrameArray) -> FrameArray:
            now = self._clock()
            snapshot = self._tracker.snapshot(now)
            if snapshot.cursor is None and not snapshot.clicks and not snapshot.chips:
                return frame
            drawn = np.array(frame, copy=True)  # the slot frame is shared with the capture thread
            renderer.draw(drawn, snapshot, now)
            return drawn

        return decorate
