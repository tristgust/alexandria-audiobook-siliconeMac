from __future__ import annotations

from collections.abc import Callable
import re

import numpy as np

from audio_processing import (
    AudioProcessingError,
    GeneratedSpeechTooLongError,
    GeneratedSpeechTooShortError,
    prepare_generated_speech_audio,
    split_generated_speech,
)


BoundedSpeechGenerator = Callable[[str], tuple[np.ndarray, int]]


def _split_at_sentence_boundary(text: str) -> tuple[str, str] | None:
    pieces = re.split(r"(?<=[.!?])\s+", " ".join(str(text or "").split()))
    for split_at in range(1, len(pieces)):
        before = " ".join(pieces[:split_at])
        after = " ".join(pieces[split_at:])
        if len(before.split()) >= 2 and len(after.split()) >= 2:
            return before, after
    return None


def _split_at_balanced_word_boundary(text: str) -> tuple[str, str] | None:
    words = " ".join(str(text or "").split()).split(" ")
    if len(words) < 4:
        return None
    split_at = min(
        range(2, len(words) - 1),
        key=lambda index: abs(
            len(" ".join(words[:index])) - len(" ".join(words[index:]))
        ),
    )
    return " ".join(words[:split_at]), " ".join(words[split_at:])


def generate_adaptive_custom_speech(
    text: str,
    generate: BoundedSpeechGenerator,
) -> tuple[np.ndarray, int, int]:
    """Recover repeated duration failures at safe speech boundaries."""
    generated: list[tuple[np.ndarray, int]] = []

    def collect(segment: str) -> None:
        try:
            audio, sample_rate = generate(segment)
        except (GeneratedSpeechTooLongError, GeneratedSpeechTooShortError) as exc:
            fallback = (
                _split_at_sentence_boundary(segment)
                if isinstance(exc, GeneratedSpeechTooShortError)
                else None
            ) or _split_at_balanced_word_boundary(segment)
            if fallback is None:
                raise
            for part in fallback:
                collect(part)
        else:
            generated.append(
                (np.asarray(audio, dtype=np.float32).reshape(-1), int(sample_rate))
            )

    for segment in split_generated_speech(text) or [text]:
        collect(segment)

    sample_rate = generated[0][1]
    if any(rate != sample_rate for _audio, rate in generated):
        raise AudioProcessingError("Custom speech segments returned different sample rates.")
    pause = np.zeros(int(sample_rate * 0.10), dtype=np.float32)
    pieces: list[np.ndarray] = []
    for index, (audio, _rate) in enumerate(generated):
        pieces.append(audio)
        if index < len(generated) - 1:
            pieces.append(pause.copy())
    joined = pieces[0] if len(pieces) == 1 else np.concatenate(pieces)
    return (
        prepare_generated_speech_audio(joined, sample_rate, text),
        sample_rate,
        len(generated),
    )
