"""Pure geometry helpers: rects, even-size rounding and frame fitting."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

FrameArray = npt.NDArray[np.uint8]


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def as_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


def even_floor(value: int) -> int:
    # libx264 + yuv420p rejects odd dimensions.
    return value - (value % 2)


def clamp_crop(crop: Rect, frame_width: int, frame_height: int) -> Rect:
    """Clip a crop rect to the frame and round its size down to even pixels.

    Example: clamp_crop(Rect(-5, 0, 101, 50), 80, 40) -> Rect(0, 0, 80, 40)
    """
    left = max(0, min(crop.x, frame_width))
    top = max(0, min(crop.y, frame_height))
    right = max(left, min(crop.x + crop.width, frame_width))
    bottom = max(top, min(crop.y + crop.height, frame_height))
    clamped = Rect(left, top, even_floor(right - left), even_floor(bottom - top))
    if clamped.width < 2 or clamped.height < 2:
        raise ValueError(
            f"crop {crop.as_dict()} leaves no area inside a {frame_width}x{frame_height} frame"
        )
    return clamped


def crop_frame(frame: FrameArray, crop: Rect | None) -> FrameArray:
    if crop is None:
        return frame
    return frame[crop.y : crop.y + crop.height, crop.x : crop.x + crop.width]


def fit_frame(frame: FrameArray, width: int, height: int) -> FrameArray:
    """Pad with black or trim so a frame matches the locked output size.

    The rawvideo pipe to ffmpeg needs a constant size, but the window may be
    resized mid-recording.

    Example: fit_frame(np.zeros((10, 12, 4), np.uint8), 8, 14).shape -> (14, 8, 4)
    """
    if frame.shape[0] == height and frame.shape[1] == width:
        return frame
    fitted = np.zeros((height, width, frame.shape[2]), dtype=frame.dtype)
    rows = min(height, frame.shape[0])
    cols = min(width, frame.shape[1])
    fitted[:rows, :cols] = frame[:rows, :cols]
    return fitted
