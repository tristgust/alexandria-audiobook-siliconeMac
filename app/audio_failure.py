"""Safe, auditable failure values for generated audio.

Generation backends can return provider errors that contain local paths,
request payloads, or other implementation details.  Chunk state is exposed
through the Produce API, so that boundary accepts only a small, explicit
failure vocabulary.  The one useful provider detail we retain is the
measured duration for the authored-text bounds because it is safe and helps a
user decide whether to retry or edit a line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from audio_artifacts import AudioArtifactError
from audio_processing import GeneratedSpeechTooLongError, GeneratedSpeechTooShortError
from sound_effects import SOUND_EFFECT_BACKEND_MESSAGE


MAX_PUBLIC_FAILURE_CHARS: Final = 240
GENERIC_AUDIO_FAILURE_MESSAGE: Final = (
    "Audio generation failed. Retry this line after reviewing the operation log."
)
ENGINE_UNAVAILABLE_MESSAGE: Final = "TTS engine is not initialized."
TEMP_AUDIO_MISSING_MESSAGE: Final = "Generated audio was not produced."
LEGACY_GENERATION_FAILED_MESSAGE: Final = "Generation failed"

_BOUNDED_MESSAGE: Final = re.compile(
    r"^Generated speech is (too short|too long) for the requested text "
    r"\(\d+(?:\.\d{1,3})?s for \d+ characters\)\.$"
)

_SAFE_CODE_MESSAGES: Final = {
    "audio_generation_failed": GENERIC_AUDIO_FAILURE_MESSAGE,
    "audio_engine_unavailable": ENGINE_UNAVAILABLE_MESSAGE,
    "audio_temp_missing": TEMP_AUDIO_MISSING_MESSAGE,
    "sound_effect_backend_unavailable": SOUND_EFFECT_BACKEND_MESSAGE,
}


@dataclass(frozen=True, slots=True)
class AudioFailure:
    """A failure safe to persist in chunk state and expose in Produce."""

    code: str
    message: str


def _bounded_failure(message: str) -> AudioFailure | None:
    """Return a bounded-duration failure only when its shape is trusted."""
    normalized = " ".join(message.split())
    if len(normalized) > MAX_PUBLIC_FAILURE_CHARS:
        return None
    match = _BOUNDED_MESSAGE.fullmatch(normalized)
    if match is None:
        return None
    code = (
        "audio_duration_insufficient"
        if match.group(1) == "too short"
        else "audio_duration_excessive"
    )
    return AudioFailure(code=code, message=normalized)


def normalize_audio_failure(
    error: AudioFailure | BaseException | str | None,
) -> AudioFailure:
    """Normalize an internal failure before it is persisted or returned.

    Known duration exceptions preserve their exact measured message.  All
    other exception text is intentionally replaced by a stable, auditable
    code/message pair so paths and provider payloads cannot leak through the
    public chunk projection.
    """
    if isinstance(error, AudioFailure):
        return error

    if isinstance(error, (GeneratedSpeechTooShortError, GeneratedSpeechTooLongError)):
        bounded = _bounded_failure(str(error))
        if bounded is not None:
            return bounded
        code = (
            "audio_duration_insufficient"
            if isinstance(error, GeneratedSpeechTooShortError)
            else "audio_duration_excessive"
        )
        return AudioFailure(
            code=code,
            message="Generated speech duration did not satisfy the authored text bounds.",
        )

    if isinstance(error, AudioArtifactError):
        if error.code in {"audio_duration_insufficient", "audio_duration_excessive"}:
            bounded = _bounded_failure(str(error))
            if bounded is not None:
                return bounded
            return AudioFailure(
                code=error.code,
                message="Generated speech duration did not satisfy the authored text bounds.",
            )
        if error.code == "audio_file_missing":
            return AudioFailure(
                code="audio_temp_missing",
                message=TEMP_AUDIO_MISSING_MESSAGE,
            )

    if isinstance(error, BaseException):
        bounded = _bounded_failure(str(error))
        if bounded is not None:
            return bounded

    if isinstance(error, str):
        bounded = _bounded_failure(error)
        if bounded is not None:
            return bounded
        if error.strip() == LEGACY_GENERATION_FAILED_MESSAGE:
            return AudioFailure(
                code="audio_generation_failed",
                message=LEGACY_GENERATION_FAILED_MESSAGE,
            )
        if error.strip() == "TTS engine not initialized":
            return AudioFailure(
                code="audio_engine_unavailable",
                message=ENGINE_UNAVAILABLE_MESSAGE,
            )
        if error.strip() == "Temp audio file not found":
            return AudioFailure(
                code="audio_temp_missing",
                message=TEMP_AUDIO_MISSING_MESSAGE,
            )

    return AudioFailure(
        code="audio_generation_failed",
        message=GENERIC_AUDIO_FAILURE_MESSAGE,
    )


def public_audio_failure(
    error: str | None,
    code: str | None = None,
) -> AudioFailure | None:
    """Project old persisted values into the safe public failure contract."""
    if not isinstance(error, str):
        return None

    bounded = _bounded_failure(error)
    if bounded is not None:
        return bounded

    normalized_code = code.strip() if isinstance(code, str) else ""
    safe_message = _SAFE_CODE_MESSAGES.get(normalized_code)
    if safe_message is not None:
        return AudioFailure(code=normalized_code, message=safe_message)

    # Records written by the first persistence patch used this phrase.  Keep
    # those records auditable while redacting any arbitrary legacy text.
    if error.strip() == LEGACY_GENERATION_FAILED_MESSAGE:
        return AudioFailure(
            code="audio_generation_failed",
            message=LEGACY_GENERATION_FAILED_MESSAGE,
        )
    if error.strip() == GENERIC_AUDIO_FAILURE_MESSAGE:
        return AudioFailure(
            code="audio_generation_failed",
            message=GENERIC_AUDIO_FAILURE_MESSAGE,
        )
    return None
