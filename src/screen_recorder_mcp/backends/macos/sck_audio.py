"""App or whole-system audio through ScreenCaptureKit (macOS 13+).

For a window target the filter includes only the owning app, so the WAV holds
that app's sound alone. For a monitor target it holds every app's sound.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import CoreMedia
import ScreenCaptureKit as SCK

from screen_recorder_mcp.backends.macos import sck_content
from screen_recorder_mcp.backends.macos.sample_buffers import audio_bytes
from screen_recorder_mcp.backends.macos.sck_stream import CaptureStream
from screen_recorder_mcp.wav_writer import AlignedWavWriter, planar_float_to_int16

_SAMPLE_RATE = 48_000
_CHANNELS = 2
# The audio stream still renders video; keep that side as cheap as possible.
_TINY_VIDEO_SIDE = 2
_SLOW_VIDEO_INTERVAL = CoreMedia.CMTimeMake(1, 1)
_SILENCE_TICK_SECONDS = 0.05


class SckAudioRecorder:
    """Writes app (pid > 0) or all-apps (pid == 0) audio to a WAV.

    Example: rec = SckAudioRecorder(pid=812, display_id=1, output=Path("a.wav"))
             rec.start(); ...; rec.stop()
    """

    def __init__(self, pid: int, display_id: int, output: Path) -> None:
        self._pid = pid
        self._display_id = display_id
        self._output = output
        self._writer: AlignedWavWriter | None = None
        self._lock = threading.Lock()
        self._stream: CaptureStream | None = None
        self._silence_stop = threading.Event()
        self._error: Exception | None = None

    def start(self) -> None:
        content_filter = self._content_filter(sck_content.shareable_content())
        self._writer = AlignedWavWriter(self._output, _SAMPLE_RATE, _CHANNELS)
        self._stream = CaptureStream(
            content_filter, _audio_configuration(), screen=None, audio=self._on_audio,
            stopped=lambda _error: None,
        )  # fmt: skip
        self._stream.start()
        threading.Thread(target=self._fill_silence_until_stopped, daemon=True).start()

    def pause(self) -> None:
        with self._lock:
            if self._writer is not None:
                self._writer.pause()

    def resume(self) -> None:
        with self._lock:
            if self._writer is not None:
                self._writer.resume()

    def stop(self) -> None:
        self._silence_stop.set()
        if self._stream is not None:
            self._stream.stop()
        with self._lock:
            if self._writer is not None:
                self._writer.close()
        if self._error is not None:
            raise RuntimeError(f"app audio capture failed: {self._error}")

    def _content_filter(self, content: Any) -> Any:
        display = sck_content.find_sc_display(content, self._display_id)
        if self._pid == 0:
            return SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(display, [])
        application = sck_content.find_sc_application(content, self._pid)
        return SCK.SCContentFilter.alloc().initWithDisplay_includingApplications_exceptingWindows_(
            display, [application], []
        )

    def _on_audio(self, sample_buffer: Any) -> None:
        try:
            raw = audio_bytes(sample_buffer)
            if raw:
                self._write(*planar_float_to_int16(raw, _CHANNELS))
        except Exception as error:  # surfaced in stop(); never raise into SCK's queue
            self._error = error

    def _write(self, pcm: bytes, frames: int) -> None:
        with self._lock:
            if self._writer is not None and not self._silence_stop.is_set():
                self._writer.write_pcm(pcm, frames)

    def _fill_silence_until_stopped(self) -> None:
        # SCK sends nothing while the app is quiet; keep the WAV on the clock.
        while not self._silence_stop.wait(_SILENCE_TICK_SECONDS):
            with self._lock:
                if self._writer is not None:
                    self._writer.fill_silence()


def _audio_configuration() -> Any:
    configuration = SCK.SCStreamConfiguration.alloc().init()
    configuration.setCapturesAudio_(True)
    configuration.setSampleRate_(_SAMPLE_RATE)
    configuration.setChannelCount_(_CHANNELS)
    configuration.setExcludesCurrentProcessAudio_(True)
    configuration.setWidth_(_TINY_VIDEO_SIDE)
    configuration.setHeight_(_TINY_VIDEO_SIDE)
    configuration.setMinimumFrameInterval_(_SLOW_VIDEO_INTERVAL)
    return configuration
