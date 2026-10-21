"""Pure text formatting for durations, sizes and labels (no Qt, easy to test)."""

from __future__ import annotations

from datetime import datetime


def format_clock(seconds: float) -> str:
    """mm:ss under an hour, h:mm:ss above; negative input is a bug, not clamped silently.

    Example: format_clock(65.4) -> "01:05"
    """
    if seconds < 0:
        raise ValueError(f"seconds={seconds!r} must be >= 0")
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_clock_tenths(seconds: float) -> str:
    """Like format_clock with one decimal, for trim handles.

    Example: format_clock_tenths(3.26) -> "00:03.2"
    """
    if seconds < 0:
        raise ValueError(f"seconds={seconds!r} must be >= 0")
    tenths = int(seconds * 10)
    return f"{format_clock(tenths / 10)}.{tenths % 10}"


def format_bytes(size: int) -> str:
    """Human size with one decimal above KB.

    Example: format_bytes(1_572_864) -> "1.5 MB"
    """
    if size < 0:
        raise ValueError(f"size={size!r} must be >= 0")
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB"):
        value /= 1024
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
    raise AssertionError("unreachable")


def format_created(moment: datetime, now: datetime) -> str:
    """Relative day label used in the history list.

    Example: format_created(now.replace(hour=9), now) -> "Today, 09:00"
    """
    days = (now.date() - moment.date()).days
    clock = moment.strftime("%H:%M")
    if days == 0:
        return f"Today, {clock}"
    if days == 1:
        return f"Yesterday, {clock}"
    return moment.strftime("%d %b %Y, ") + clock


def format_resolution(width: int, height: int) -> str:
    """Example: format_resolution(1920, 1080) -> "1920 x 1080"."""
    return f"{width} x {height}"


def estimate_gif_bytes(duration_seconds: float, width: int, height: int, high_quality: bool) -> int:
    """Rough GIF size hint for the export dialog (the real size is known only after encoding).

    High quality keeps the full size at 15 fps; low halves each side at 10 fps. The
    bytes-per-pixel constant is an empirical average for screen content.
    """
    scale = 1.0 if high_quality else 0.5
    fps = 15 if high_quality else 10
    frames = max(1, int(duration_seconds * fps))
    return int(width * scale * height * scale * frames * 0.035)
