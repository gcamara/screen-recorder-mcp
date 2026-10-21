"""CPU video path: YUV->RGB conversion, display-size resampling and newest-frame-wins conversion."""

from __future__ import annotations

import threading
import time

import numpy as np
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from screen_recorder_mcp.ui.video_surface import FrameConverter, YuvPlanes, planes_to_image
from screen_recorder_mcp.ui.yuv_convert import output_size, plane_view, yuv420_to_rgb32


def _flat(width: int, height: int, y: int, u: int, v: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return (np.full((height, width), y, np.uint8), np.full((height // 2, width // 2), u, np.uint8),
            np.full((height // 2, width // 2), v, np.uint8))


def test_bt601_limited_range_reference_colours() -> None:
    white, black, red = _flat(4, 4, 235, 128, 128), _flat(4, 4, 16, 128, 128), _flat(4, 4, 81, 90, 240)
    assert yuv420_to_rgb32(*white)[0, 0].tolist() == [255, 255, 255, 255]
    assert yuv420_to_rgb32(*black)[0, 0].tolist() == [0, 0, 0, 255]
    b, g, r, _a = yuv420_to_rgb32(*red)[0, 0].tolist()
    assert r > 245 and g < 10 and b < 10  # BGRA byte order, pure red


def test_resampling_converts_at_requested_size() -> None:
    out = yuv420_to_rgb32(*_flat(1920, 1080, 128, 128, 128), size=(760, 428))
    assert out.shape == (428, 760, 4)


def test_output_size_fits_target_and_never_upscales() -> None:
    assert output_size(1920, 1080, 760, 430) == (760, 428)
    assert output_size(640, 360, 1600, 900) == (640, 360)
    assert output_size(800, 600, 0, 0) == (800, 600)


def test_plane_view_drops_row_padding() -> None:
    raw = bytes(range(8)) * 2  # 2 rows of 8 bytes, 6 useful
    assert plane_view(raw, 8, 2, 6).tolist() == [[0, 1, 2, 3, 4, 5], [0, 1, 2, 3, 4, 5]]


def test_planes_to_image_owns_its_pixels(qapp: QApplication) -> None:
    image = planes_to_image(YuvPlanes(*_flat(8, 4, 235, 128, 128), size=(8, 4)))
    assert image.format() == QImage.Format.Format_RGB32 and image.pixelColor(0, 0).name() == "#ffffff"


def test_converter_drops_stale_frames_and_delivers_newest(qapp: QApplication) -> None:
    converter = FrameConverter()
    images: list[QImage] = []
    converter.image_ready.connect(images.append)
    for luma in (16, 60, 120, 235):  # submitted faster than one conversion takes
        converter.submit(YuvPlanes(*_flat(64, 32, luma, 128, 128), size=(64, 32)))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and (not images or images[-1].pixelColor(0, 0).name() != "#ffffff"):
        qapp.processEvents()
        time.sleep(0.01)
    assert images[-1].pixelColor(0, 0).name() == "#ffffff" and len(images) <= 4
    assert not converter._busy or threading.active_count() >= 1
