"""CPU colour conversion for decoded video frames (YUV 4:2:0 planar -> 32-bit RGB).

QVideoFrame.toImage() converts on the GPU; when VRAM is exhausted (a render or a game
holding it) that took 76-94 ms per frame and playback dropped to ~10 fps. numpy at
display resolution keeps it to a few ms and never touches the GPU.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

Plane = npt.NDArray[np.uint8]

# BT.601 limited range in 8.8 fixed point: what ffmpeg's default rgb->yuv420p writes,
# and the recorder feeds ffmpeg BGRA without colour tags.
_Y_SCALE = 298
_V_TO_R = 409
_U_TO_G = -100
_V_TO_G = -208
_U_TO_B = 516


def output_size(source_width: int, source_height: int, target_width: int, target_height: int) -> tuple[int, int]:
    """Size to convert at: the source fitted into the target (aspect kept), never upscaled.

    Converting at display size makes the cost follow the player, not the recording: a 1500 px wide
    window shown 760 px wide converts ~4x fewer pixels.
    Example: output_size(1920, 1080, 760, 430) -> (760, 428)
    """
    if target_width <= 0 or target_height <= 0:
        return source_width, source_height
    scale = min(1.0, target_width / source_width, target_height / source_height)
    return max(2, round(source_width * scale)), max(2, round(source_height * scale))


def plane_view(raw: bytes | memoryview, bytes_per_line: int, rows: int, width: int) -> Plane:
    """2-D view of one plane without its row padding.

    Example: plane_view(bytes(1920 * 1080), 1920, 1080, 1920).shape -> (1080, 1920)
    """
    plane = np.frombuffer(raw, dtype=np.uint8, count=bytes_per_line * rows)
    return plane.reshape(rows, bytes_per_line)[:, :width]


def yuv420_to_rgb32(y: Plane, u: Plane, v: Plane, size: tuple[int, int] | None = None) -> Plane:
    """Full-size Y plus half-size U/V -> (h, w, 4) uint8 in QImage.Format_RGB32 byte order (B, G, R, 255).

    `size` (width, height) resamples nearest-neighbour while converting; None keeps the source size.
    Example: yuv420_to_rgb32(np.full((4, 4), 235, np.uint8), *(np.full((2, 2), 128, np.uint8),) * 2)[0, 0]
             -> [255, 255, 255, 255]
    """
    width, height = size or (y.shape[1], y.shape[0])
    rows = np.arange(height) * y.shape[0] // height
    cols = np.arange(width) * y.shape[1] // width
    luma = (y[np.ix_(rows, cols)].astype(np.int32) - 16) * _Y_SCALE
    chroma_u = u[np.ix_(rows // 2, cols // 2)].astype(np.int32) - 128
    chroma_v = v[np.ix_(rows // 2, cols // 2)].astype(np.int32) - 128
    out = np.empty((height, width, 4), dtype=np.uint8)
    out[..., 2] = _to_byte(luma + _V_TO_R * chroma_v)
    out[..., 1] = _to_byte(luma + _U_TO_G * chroma_u + _V_TO_G * chroma_v)
    out[..., 0] = _to_byte(luma + _U_TO_B * chroma_u)
    out[..., 3] = 255
    return out


def _to_byte(fixed_point: npt.NDArray[np.int32]) -> npt.NDArray[np.uint8]:
    return np.clip((fixed_point + 128) >> 8, 0, 255).astype(np.uint8)
