"""Clock-aligned int16 WAV writing shared by every audio backend."""

from __future__ import annotations

import threading
import time
import wave
from pathlib import Path
from typing import Callable

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
        self._paused_since: float | None = None

    def pause(self, now: float) -> None:
        """Catch up to `now`, then stop expecting audio until resume().

        Example: filler.pause(1.0); filler.resume(4.0)  # the 3 s gap is never padded
        """
        if self._paused_since is not None:
            return
        self._paused_since = now

    def resume(self, now: float) -> None:
        """Move the origin forward by the paused span so no silence is owed for it."""
        if self._paused_since is None:
            return
        self._origin += now - self._paused_since
        self._paused_since = None

    @property
    def paused(self) -> bool:
        return self._paused_since is not None

    def record(self, frame_count: int) -> None:
        self.frames_written += frame_count

    def silence_due(self, now: float) -> bytes:
        """Zero samples needed to catch up with `now`, ignoring gaps under the slack.

        Example: SilenceGapFiller(1000, 1, 0.0).silence_due(1.0) -> 2000 zero bytes
        """
        if self._paused_since is not None:
            return b""
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
    """WAV sink that pads silence gaps and drops audio while paused.

    Producer (device thread) and pause()/resume() (service thread) may race,
    hence the lock.

    Example:
        writer = AlignedWavWriter(Path("a.wav"), rate=48000, channels=2)
        writer.write_pcm(pcm_bytes, frame_count); writer.pause(); writer.resume(); writer.close()
    """

    def __init__(
        self, path: Path, rate: int, channels: int, clock: Callable[[], float] = time.perf_counter
    ) -> None:
        self._wav = wave.open(str(path), "wb")
        self._wav.setnchannels(channels)
        self._wav.setsampwidth(2)
        self._wav.setframerate(rate)
        self._clock = clock
        self._lock = threading.Lock()
        self._gaps = SilenceGapFiller(rate, channels, clock())

    def write_pcm(self, pcm: bytes, frame_count: int) -> None:
        with self._lock:
            if self._gaps.paused:
                return
            self._wav.writeframes(self._gaps.silence_due(self._clock()))
            self._wav.writeframes(pcm)
            self._gaps.record(frame_count)

    def fill_silence(self) -> None:
        with self._lock:
            self._wav.writeframes(self._gaps.silence_due(self._clock()))

    def pause(self) -> None:
        # Pad up to now first so audio before the pause keeps its video alignment.
        with self._lock:
            self._wav.writeframes(self._gaps.silence_due(self._clock()))
            self._gaps.pause(self._clock())

    def resume(self) -> None:
        with self._lock:
            self._gaps.resume(self._clock())

    def close(self) -> None:
        with self._lock:
            self._wav.writeframes(self._gaps.silence_due(self._clock()))
            self._wav.close()
