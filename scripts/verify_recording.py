"""End-to-end check on a real desktop (Windows or macOS).

Records a window for 5 s while a red window covers it, then a 3 s clip with
audio, and asserts on the results with ffprobe/ffmpeg. Prints PASS/FAIL lines.

Usage: uv run python scripts/verify_recording.py <window query> [--capture=auto|gpu|cpu]
       (e.g. "TextEdit", "notepad")
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from screen_recorder_mcp.backends import current_backend
from screen_recorder_mcp.capture import AudioMode
from screen_recorder_mcp.frame_sources import CaptureMode
from screen_recorder_mcp.geometry import Rect
from screen_recorder_mcp.session_factory import build_session, find_ffmpeg, plan_recording
from screen_recorder_mcp.targets import CaptureTarget, resolve_target

_RED_COVER_SECONDS = 3000  # ms the red window stays up
_MODE = "auto"  # overridden by --capture=cpu|gpu


def main(query: str) -> int:
    backend = current_backend()
    backend.prepare_process()
    target = resolve_target(query, backend.windows, backend.monitor_rects)
    print(f"target: {target.label} {target.rect.as_dict()}")
    out_dir = Path(tempfile.mkdtemp(prefix="screen-recorder-verify-"))
    failures = _check_occlusion(backend, target, out_dir, None)
    failures += _check_occlusion(backend, target, out_dir, Rect(10, 40, 301, 201))
    failures += _check_audio(backend, target, out_dir)
    print(f"{'PASS' if failures == 0 else 'FAIL'}: {failures} failure(s); videos in {out_dir}")
    return 1 if failures else 0


def _record(backend, target: CaptureTarget, out_dir: Path, region, audio, seconds, cover) -> Path:
    plan = plan_recording(target, region, 30, audio, out_dir)
    session = build_session(backend, plan, audio, True, CaptureMode(_MODE))
    session.start()
    cover_process = _open_red_cover(target.rect) if cover else None
    time.sleep(seconds)
    if cover_process is not None:
        cover_process.wait()
    return session.stop()


def _open_red_cover(rect: Rect) -> subprocess.Popen[bytes]:
    script = (
        "import tkinter as tk;r=tk.Tk();r.overrideredirect(1);"
        f"r.geometry('{rect.width}x{rect.height}+{rect.x}+{rect.y}');r.configure(bg='red');"
        f"r.attributes('-topmost',1);r.after({_RED_COVER_SECONDS},r.destroy);r.mainloop()"
    )
    return subprocess.Popen([sys.executable, "-c", script])


def _check_occlusion(backend, target: CaptureTarget, out_dir: Path, region: Rect | None) -> int:
    video = _record(backend, target, out_dir, region, AudioMode.NONE, 5, cover=True)
    probe = _probe(video)
    red_share = _red_share(_frame_at(video, 2.0))
    label = "crop" if region else "full"
    failures = _expect(f"{label}: duration ~5s", abs(probe["duration"] - 5) < 0.5, probe)
    failures += _expect(f"{label}: even size", probe["width"] % 2 == 0 and probe["height"] % 2 == 0, probe)
    return failures + _expect(f"{label}: covering window not recorded", red_share < 0.01, f"{red_share:.2%} red")


def _check_audio(backend, target: CaptureTarget, out_dir: Path) -> int:
    video = _record(backend, target, out_dir, None, AudioMode.SYSTEM, 3, cover=False)
    probe = _probe(video)
    return _expect("audio: has an audio stream", probe["has_audio"], probe)


def _probe(video: Path) -> dict[str, object]:
    ffprobe = str(Path(find_ffmpeg()).with_name("ffprobe" + Path(find_ffmpeg()).suffix))
    raw = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
         "-of", "json", str(video)], capture_output=True, check=True,
    ).stdout  # fmt: skip
    parsed = json.loads(raw)
    video_stream = next(s for s in parsed["streams"] if s["codec_type"] == "video")
    return {
        "duration": float(parsed["format"]["duration"]),
        "width": video_stream["width"],
        "height": video_stream["height"],
        "has_audio": any(s["codec_type"] == "audio" for s in parsed["streams"]),
    }


def _frame_at(video: Path, seconds: float) -> np.ndarray:
    probe = _probe(video)
    raw = subprocess.run(
        [find_ffmpeg(), "-v", "error", "-ss", str(seconds), "-i", str(video), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True,
    ).stdout  # fmt: skip
    return np.frombuffer(raw, np.uint8).reshape(int(probe["height"]), int(probe["width"]), 3)


def _red_share(frame: np.ndarray) -> float:
    red = (frame[..., 0] > 200) & (frame[..., 1] < 60) & (frame[..., 2] < 60)
    return float(red.mean())


def _expect(name: str, ok: bool, detail: object) -> int:
    print(f"{'PASS' if ok else 'FAIL'}  {name}  ({detail})")
    return 0 if ok else 1


if __name__ == "__main__":
    flags = [arg for arg in sys.argv[1:] if arg.startswith("--capture=")]
    positional = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    if flags:
        _MODE = flags[0].split("=", 1)[1]
    sys.exit(main(positional[0] if positional else "notepad"))
