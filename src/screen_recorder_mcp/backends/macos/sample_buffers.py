"""Copy pixels/audio out of CMSampleBuffers delivered by ScreenCaptureKit."""

from __future__ import annotations

from typing import Any

import CoreMedia
import numpy as np
import Quartz
import ScreenCaptureKit as SCK

from screen_recorder_mcp.geometry import FrameArray


def is_complete_frame(sample_buffer: Any) -> bool:
    """SCK also sends idle/blank status buffers; only Complete ones carry new pixels."""
    attachments = CoreMedia.CMSampleBufferGetSampleAttachmentsArray(sample_buffer, False)
    if not attachments:
        return False
    status = attachments[0].get(SCK.SCStreamFrameInfoStatus)
    return status is not None and int(status) == SCK.SCFrameStatusComplete


def bgra_frame(sample_buffer: Any) -> FrameArray | None:
    """Copy a 32BGRA pixel buffer into an owned (height, width, 4) array.

    Example: on a complete 1920x1080 frame -> ndarray of shape (1080, 1920, 4)
    """
    pixel_buffer = CoreMedia.CMSampleBufferGetImageBuffer(sample_buffer)
    if pixel_buffer is None:
        return None
    Quartz.CVPixelBufferLockBaseAddress(pixel_buffer, Quartz.kCVPixelBufferLock_ReadOnly)
    try:
        return _copy_pixels(pixel_buffer)
    finally:
        Quartz.CVPixelBufferUnlockBaseAddress(pixel_buffer, Quartz.kCVPixelBufferLock_ReadOnly)


def _copy_pixels(pixel_buffer: Any) -> FrameArray:
    width = Quartz.CVPixelBufferGetWidth(pixel_buffer)
    height = Quartz.CVPixelBufferGetHeight(pixel_buffer)
    row_bytes = Quartz.CVPixelBufferGetBytesPerRow(pixel_buffer)
    base = Quartz.CVPixelBufferGetBaseAddress(pixel_buffer)
    raw = np.frombuffer(base.as_buffer(row_bytes * height), dtype=np.uint8)
    # Rows are padded to row_bytes; drop the padding, then own the memory.
    return raw.reshape(height, row_bytes)[:, : width * 4].reshape(height, width, 4).copy()


def audio_bytes(sample_buffer: Any) -> bytes | None:
    """Raw bytes of an audio sample buffer's block buffer (float32 planes for SCK)."""
    block_buffer = CoreMedia.CMSampleBufferGetDataBuffer(sample_buffer)
    if block_buffer is None:
        return None
    length = CoreMedia.CMBlockBufferGetDataLength(block_buffer)
    status, data = CoreMedia.CMBlockBufferCopyDataBytes(block_buffer, 0, length, None)
    if status != 0:
        raise RuntimeError(f"CMBlockBufferCopyDataBytes returned OSStatus {status}; expected 0")
    return bytes(data)
