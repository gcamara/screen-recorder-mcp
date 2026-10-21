"""Clock-aligned int16 WAV writing shared by every audio backend."""

from __future__ import annotations

import time
import wave
from pathlib import Path

import numpy as np
import numpy.typing as npt

_GAP_SLACK_SECONDS = 0.1


class SilenceGapFiller:
    """Keeps the WAV aligned to wall-clock time.

    Loopback/app audio delivers no packets while nothing plays, so without
    filling, audio after a pause would drift earlier than the video.
    """

    def __init__(self, rate: int, channels: int, origin: float) -> None:
        self._rate = rate
        self._channels = channels
        self._origin = origin
        self.frames_written = 0

    def record(self, frame_count: int) -> None:
        self.frames_written += frame_count

    def silence_due(self, now: float) -> bytes:
        """Zero samples needed to catch up with `now`, ignoring gaps under the slack.

        Example: SilenceGapFiller(1000, 1, 0.0).silence_due(1.0) -> 2000 zero bytes
        """
        missing = int((now - self._origin) * self._rate) - self.frames_written
        if missing < self._rate * _GAP_SLACK_SECONDS:
            return b""
        self.frames_written += missing
        return bytes(missing * self._channels * 2)


def float_to_int16(samples: npt.NDArray[np.float32]) -> bytes:
    """Interleaved float32 in [-1, 1] → little-endian int16 PCM bytes.

    Example: float_to_int16(np.array([1.0, -1.0], np.float32)) -> b'\\xff\\x7f\\x01\\x80'
    """
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * 32767).astype("<i2").tobytes()


def planar_float_to_int16(raw: bytes, channels: int) -> tuple[bytes, int]:
    """Non-interleaved float32 planes (ScreenCaptureKit's audio layout) → interleaved int16.

    Returns (pcm_bytes, frame_count).
    Example: planar_float_to_int16(np.array([1, 1, 0, 0], np.float32).tobytes(), 2)
             -> (int16 of [1, 0, 1, 0] scaled, 2)
    """
    samples = np.frombuffer(raw, dtype=np.float32)
    if channels < 1 or samples.size % channels:
        raise ValueError(
            f"{samples.size} float samples don't split into {channels} channel planes"
        )
    frames = samples.size // channels
    interleaved = samples.reshape(channels, frames).T.reshape(-1)
    return float_to_int16(interleaved), frames


class AlignedWavWriter:
    """WAV sink that pads silence gaps; safe to call from one producer thread.

    Example:
        writer = AlignedWavWriter(Path("a.wav"), rate=48000, channels=2)
        writer.write_pcm(pcm_bytes, frame_count); writer.fill_silence(); writer.close()
    """

    def __init__(self, path: Path, rate: int, channels: int) -> None:
        self._wav = wave.open(str(path), "wb")
        self._wav.setnchannels(channels)
        self._wav.setsampwidth(2)
        self._wav.setframerate(rate)
        self._gaps = SilenceGapFiller(rate, channels, time.perf_counter())

    def write_pcm(self, pcm: bytes, frame_count: int) -> None:
        self.fill_silence()
        self._wav.writeframes(pcm)
        self._gaps.record(frame_count)

    def fill_silence(self) -> None:
        self._wav.writeframes(self._gaps.silence_due(time.perf_counter()))

    def close(self) -> None:
        self.fill_silence()
        self._wav.close()
