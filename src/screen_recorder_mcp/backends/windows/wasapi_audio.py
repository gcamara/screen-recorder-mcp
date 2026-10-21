"""Opt-in audio capture to WAV on Windows: system loopback or the default microphone.

System audio is a WASAPI loopback of the default output device, so it holds
the sound of every app, not just the recorded window.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pyaudiowpatch as pyaudio

from screen_recorder_mcp.capture import AudioMode
from screen_recorder_mcp.wav_writer import AlignedWavWriter

_CHUNK_FRAMES = 1024
_IDLE_POLL_SECONDS = 0.01


class WasapiAudioRecorder:
    """Records one WASAPI input (loopback or mic) on its own thread.

    Example: rec = WasapiAudioRecorder(AudioMode.SYSTEM, Path("a.wav")); rec.start(); rec.stop()
    """

    def __init__(self, mode: AudioMode, output: Path) -> None:
        if mode is AudioMode.NONE:
            raise ValueError(f"audio mode {mode.value!r} needs no recorder; expected system|mic")
        self._mode = mode
        self._output = output
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: Exception | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._record_safely, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        if self._error is not None:
            raise RuntimeError(f"audio capture ({self._mode.value}) failed: {self._error}")

    def _record_safely(self) -> None:
        try:
            self._record()
        except Exception as error:  # surfaced to the caller in stop()
            self._error = error

    def _record(self) -> None:
        audio = pyaudio.PyAudio()
        try:
            device = _pick_device(audio, self._mode)
            self._stream_to_wav(audio, device)
        finally:
            audio.terminate()

    def _stream_to_wav(self, audio: pyaudio.PyAudio, device: dict[str, object]) -> None:
        channels = int(device["maxInputChannels"])  # type: ignore[call-overload]
        rate = int(device["defaultSampleRate"])  # type: ignore[call-overload]
        stream = audio.open(
            format=pyaudio.paInt16, channels=channels, rate=rate, input=True,
            input_device_index=int(device["index"]),  # type: ignore[call-overload]
            frames_per_buffer=_CHUNK_FRAMES,
        )  # fmt: skip
        writer = AlignedWavWriter(self._output, rate, channels)
        try:
            while not self._stop.is_set():
                _drain(stream, writer)
        finally:
            writer.close()
            stream.stop_stream()
            stream.close()


def _drain(stream: pyaudio.Stream, writer: AlignedWavWriter) -> None:
    available = stream.get_read_available()
    if available > 0:
        writer.write_pcm(stream.read(available, exception_on_overflow=False), available)
        return
    # Non-blocking poll: loopback is silent-without-packets, and a blocking
    # read() would hang stop().
    writer.fill_silence()
    time.sleep(_IDLE_POLL_SECONDS)


def _pick_device(audio: pyaudio.PyAudio, mode: AudioMode) -> dict[str, object]:
    if mode is AudioMode.SYSTEM:
        return dict(audio.get_default_wasapi_loopback())
    return dict(audio.get_default_input_device_info())
