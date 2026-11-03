"""Text-to-speech for video manuals through edge-tts (Microsoft Edge's online voices).

edge-tts is optional (`pip install edge-tts`): the text is sent to Microsoft's service, so
nothing here runs unless a manual asks for narration. Clips are cached by (voice, rate, text),
so re-rendering a manual after moving an arrow does not synthesize again.

DEFAULT_VOICE is multilingual: it reads whatever language the text is written in.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from screen_recorder_mcp.media_tools import run_command

DEFAULT_VOICE = "en-US-EmmaMultilingualNeural"
_TICKS_PER_SECOND = 10_000_000  # edge-tts offsets are in 100 ns units


@dataclass(frozen=True)
class Sentence:
    start: float  # seconds from the start of the clip
    end: float
    text: str


@dataclass(frozen=True)
class SpeechClip:
    text: str
    path: Path  # mp3
    duration: float  # seconds of audio in the file
    sentences: tuple[Sentence, ...]  # for subtitles


Synthesizer = Callable[[str, str, str], tuple[bytes, list[dict[str, object]]]]


def edge_synthesize(text: str, voice: str, rate: str) -> tuple[bytes, list[dict[str, object]]]:
    """mp3 bytes + SentenceBoundary marks from edge-tts; raises a clear error when it is missing."""
    try:
        import edge_tts
    except ImportError:
        raise RuntimeError("narration needs edge-tts; install it with `uv pip install edge-tts`") from None

    async def collect() -> tuple[bytes, list[dict[str, object]]]:
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        audio, marks = bytearray(), []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
            elif chunk["type"] in ("SentenceBoundary", "WordBoundary"):
                marks.append({key: value for key, value in chunk.items() if key != "data"})
        return bytes(audio), marks

    return asyncio.run(collect())


def sentences_from_marks(marks: list[dict[str, object]]) -> tuple[Sentence, ...]:
    """Example: sentences_from_marks([{"offset": 5e6, "duration": 2e7, "text": "Hi."}]) -> (Sentence(0.5, 2.5, "Hi."),)"""
    sentences = []
    for mark in marks:
        start = float(mark["offset"]) / _TICKS_PER_SECOND  # type: ignore[arg-type]
        end = start + float(mark["duration"]) / _TICKS_PER_SECOND  # type: ignore[arg-type]
        sentences.append(Sentence(round(start, 3), round(end, 3), str(mark.get("text", "")).strip()))
    return tuple(sentences)


def audio_duration(ffprobe: str, path: Path) -> float:
    result = run_command([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe could not read {path}: {(result.stderr or b'').decode(errors='replace')}")
    return float(result.stdout.decode().strip() or 0)


class Narrator:
    """Example: Narrator(cache_dir, ffprobe).speak("Click Save.", DEFAULT_VOICE).duration -> 1.9"""

    def __init__(self, cache_dir: Path, ffprobe: str, synthesize: Synthesizer = edge_synthesize,
                 measure: Callable[[str, Path], float] = audio_duration) -> None:  # fmt: skip
        self._cache_dir = cache_dir
        self._ffprobe = ffprobe
        self._synthesize = synthesize
        self._measure = measure

    def speak(self, text: str, voice: str = DEFAULT_VOICE, rate: str = "+0%") -> SpeechClip:
        key = hashlib.sha1(json.dumps([voice, rate, text]).encode()).hexdigest()[:16]
        audio_path = self._cache_dir / f"tts-{key}.mp3"
        marks_path = self._cache_dir / f"tts-{key}.json"
        if audio_path.exists() and marks_path.exists():
            marks = json.loads(marks_path.read_text(encoding="utf-8"))
        else:
            audio, marks = self._synthesize(text, voice, rate)
            if not audio:
                raise RuntimeError(f"edge-tts returned no audio for voice {voice!r}; check the voice name")
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            audio_path.write_bytes(audio)
            marks_path.write_text(json.dumps(marks), encoding="utf-8")
        duration = self._measure(self._ffprobe, audio_path)
        return SpeechClip(text, audio_path, duration, sentences_from_marks(marks))


def list_voices(language: str | None = None) -> list[dict[str, str]]:
    """Voice names for `voice`, filtered by a locale prefix such as "en", "ru" or "de-DE"."""
    try:
        import edge_tts
    except ImportError:
        raise RuntimeError("listing voices needs edge-tts; install it with `uv pip install edge-tts`") from None
    voices = asyncio.run(edge_tts.list_voices())
    wanted = (language or "").lower()
    return [
        {"name": v["ShortName"], "locale": v["Locale"], "gender": v["Gender"]}
        for v in voices
        if not wanted or v["Locale"].lower().startswith(wanted) or "multilingual" in v["ShortName"].lower()
    ]

