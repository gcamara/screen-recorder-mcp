"""Default-microphone capture on macOS through PortAudio (sounddevice).

ScreenCaptureKit only records the mic from macOS 15; this keeps macOS 13+.
The host app needs the Microphone permission.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import sounddevice

from screen_recorder_mcp.wav_writer import AlignedWavWriter, float_to_int16


class MicAudioRecorder:
    """Example: rec = MicAudioRecorder(Path("mic.wav")); rec.start(); ...; rec.stop()"""

    def __init__(self, output: Path) -> None:
        self._output = output
        self._stream: sounddevice.InputStream | None = None
        self._writer: AlignedWavWriter | None = None

    def start(self) -> None:
        device = sounddevice.query_devices(kind="input")
        rate = int(device["default_samplerate"])
        channels = min(2, int(device["max_input_channels"]))
        if channels < 1:
            raise RuntimeError(f"default input {device['name']!r} has no channels; expected a mic")
        self._writer = AlignedWavWriter(self._output, rate, channels)
        self._stream = sounddevice.InputStream(
            samplerate=rate, channels=channels, dtype="float32", callback=self._on_block
        )
        self._stream.start()

    def pause(self) -> None:
        if self._writer is not None:
            self._writer.pause()

    def resume(self) -> None:
        if self._writer is not None:
            self._writer.resume()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
        if self._writer is not None:
            self._writer.close()

    def _on_block(self, block: npt.NDArray[np.float32], frames: int, _time: Any, _status: Any) -> None:
        if self._writer is not None:
            self._writer.write_pcm(float_to_int16(block.reshape(-1)), frames)
