from __future__ import annotations

from collections.abc import Callable

import numpy as np

from audio_processing import (
    AudioProcessingError,
    GeneratedSpeechTooLongError,
    GeneratedSpeechTooShortError,
)
from synthesis_windows import (
    assemble_synthesis_segments,
    plan_synthesis_segments,
    split_segment_for_retry,
)


BoundedSpeechGenerator = Callable[[str], tuple[np.ndarray, int]]


def generate_adaptive_custom_speech_with_receipt(
    text: str,
    generate: BoundedSpeechGenerator,
) -> tuple[np.ndarray, int, int, dict]:
    """Recover repeated duration failures at safe speech boundaries."""
    plan = plan_synthesis_segments(text, backend_id="qwen3_custom")
    generated: list[dict] = []
    leaf_segments: list[dict] = []

    def collect(segment: dict) -> None:
        generation_text = str(segment["generation_text"])
        try:
            audio, sample_rate = generate(generation_text)
        except (GeneratedSpeechTooLongError, GeneratedSpeechTooShortError) as exc:
            fallback = split_segment_for_retry(
                segment,
                minimum_words=2,
                prefer_sentence=isinstance(exc, GeneratedSpeechTooShortError),
            )
            if fallback is None:
                raise
            for part in fallback:
                collect(part)
        else:
            leaf_segments.append(segment)
            generated.append(
                {
                    "segment_id": segment["segment_id"],
                    "audio": np.asarray(audio, dtype=np.float32).reshape(-1),
                    "sample_rate": int(sample_rate),
                }
            )

    for segment in plan["segments"]:
        collect(segment)
    if not generated:
        raise AudioProcessingError("Custom speech produced no internal segment results.")
    leaf_plan = {
        **plan,
        "segments": leaf_segments,
        "segment_count": len(leaf_segments),
    }
    joined, sample_rate, receipt = assemble_synthesis_segments(
        leaf_plan,
        generated,
    )
    return joined, sample_rate, len(generated), receipt


def generate_adaptive_custom_speech(
    text: str,
    generate: BoundedSpeechGenerator,
) -> tuple[np.ndarray, int, int]:
    audio, sample_rate, segment_count, _receipt = (
        generate_adaptive_custom_speech_with_receipt(text, generate)
    )
    return audio, sample_rate, segment_count
