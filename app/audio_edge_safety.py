from __future__ import annotations

from typing import Final

from pydub import AudioSegment


CLICK_SAFE_FADE_IN_MS: Final = 3
CLICK_SAFE_ENDPOINT_RATIO: Final = 0.01


def needs_click_safe_fade_in(segment: AudioSegment) -> bool:
    """Return whether the decoded first frame exceeds the safe endpoint."""
    if not isinstance(segment, AudioSegment):
        return False

    first_frame = segment.get_sample_slice(0, 1).get_array_of_samples()
    if not first_frame:
        return False

    endpoint = max(abs(int(sample)) for sample in first_frame)
    threshold = segment.max_possible_amplitude * CLICK_SAFE_ENDPOINT_RATIO
    return endpoint >= threshold


def ensure_click_safe_fade_in(segment: AudioSegment) -> AudioSegment:
    """Ramp an abrupt first frame without changing duration or safe starts."""
    if not needs_click_safe_fade_in(segment):
        return segment

    return segment.fade_in(min(CLICK_SAFE_FADE_IN_MS, len(segment)))
