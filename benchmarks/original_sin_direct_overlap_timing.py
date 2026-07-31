"""Shared timing helpers for strict direct-overlap extraction rounds."""
from __future__ import annotations

from pathlib import Path
from typing import Any


class DirectOverlapTimingError(RuntimeError):
    pass


def safe_segment_bounds(
    *,
    segments: list[dict[str, Any]],
    segment_start: int,
    segment_end: int,
    adjacent_guard_seconds: float,
    requested_segment_tail_seconds: float,
) -> dict[str, float]:
    start_seconds = float(segments[segment_start]["start"])
    end_seconds = float(segments[segment_end]["end"])
    previous_end = (
        float(segments[segment_start - 1]["end"])
        if segment_start > 0
        else start_seconds
    )
    next_start = (
        float(segments[segment_end + 1]["start"])
        if segment_end + 1 < len(segments)
        else end_seconds + requested_segment_tail_seconds + 1.0
    )
    leading_gap = max(0.0, start_seconds - previous_end)
    trailing_gap = max(0.0, next_start - end_seconds)
    leading_guard = min(adjacent_guard_seconds, leading_gap * 0.4)
    trailing_guard = min(adjacent_guard_seconds, trailing_gap * 0.4)
    minimum_start = previous_end + leading_guard
    maximum_end = next_start - trailing_guard
    requested_end = end_seconds + requested_segment_tail_seconds
    minimum_end = min(maximum_end, requested_end)
    if maximum_end <= minimum_start or minimum_end <= minimum_start:
        raise DirectOverlapTimingError(
            f"No safe source window for transcript segments {segment_start}-{segment_end}."
        )
    return {
        "transcript_segment_start_seconds": start_seconds,
        "transcript_segment_end_seconds": end_seconds,
        "previous_segment_end_seconds": previous_end,
        "next_segment_start_seconds": next_start,
        "leading_gap_seconds": leading_gap,
        "trailing_gap_seconds": trailing_gap,
        "leading_guard_seconds": leading_guard,
        "trailing_guard_seconds": trailing_guard,
        "minimum_source_start_seconds": minimum_start,
        "minimum_source_end_seconds": minimum_end,
        "maximum_source_end_seconds": maximum_end,
        "requested_segment_tail_seconds": requested_segment_tail_seconds,
        "available_segment_tail_seconds": max(0.0, maximum_end - end_seconds),
        "required_segment_tail_seconds": max(0.0, minimum_end - end_seconds),
    }


def append_deterministic_silence(path: Path, milliseconds: int) -> dict[str, Any]:
    import numpy as np
    import soundfile as sf

    if milliseconds < 0:
        raise DirectOverlapTimingError("Appended silence may not be negative.")
    audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    if audio.size == 0:
        raise DirectOverlapTimingError(f"Cannot append silence to empty audio: {path}")
    frames = round(int(rate) * milliseconds / 1000)
    if frames:
        silence = np.zeros((frames, audio.shape[1]), dtype=np.float32)
        audio = np.concatenate([audio, silence], axis=0)
    sf.write(str(path), audio, int(rate), subtype="PCM_16")
    return {
        "appended_silence_milliseconds": milliseconds,
        "appended_silence_frames": frames,
        "sample_rate": int(rate),
    }
