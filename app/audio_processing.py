from __future__ import annotations

import math
import re
import shutil
import subprocess
import tempfile
import textwrap
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import numpy as np
import soundfile as sf


CLONE_REFERENCE_RELEASE_MS = 20
CLONE_REFERENCE_TRAILING_SILENCE_MS = 240


class AudioProcessingError(RuntimeError):
    """Raised when Alexandria cannot decode or normalize an audio source."""


class GeneratedSpeechTooShortError(AudioProcessingError):
    """Raised when generated speech cannot contain its complete requested text."""


class GeneratedSpeechTooLongError(AudioProcessingError):
    """Raised when generated speech exceeds its text-derived duration bound."""


def voice_design_max_tokens(text: str) -> int:
    """Return a generous but finite Qwen audio-token budget for one line."""
    character_count = len(" ".join(str(text or "").split()))
    expected_ceiling_seconds = max(8.0, (character_count / 5.0) + 6.0)
    return max(128, min(768, math.ceil(expected_ceiling_seconds * 12.5)))


def generated_speech_duration_bounds(text: str) -> tuple[float, float]:
    """Return conservative spoken-duration bounds for authored text."""
    character_count = len(" ".join(str(text or "").split()))
    return max(0.35, character_count / 32.0), max(8.0, (character_count / 6.0) + 4.0)


def production_speech_max_tokens(text: str, configured_max: int | None = None) -> int:
    """Bound Qwen generation without truncating Alexandria's longest lines."""
    _, maximum_duration = generated_speech_duration_bounds(text)
    # One token beyond the accepted duration makes cap exhaustion fail closed
    # in the duration check, while ordinary EOS-complete speech is unchanged.
    text_budget = max(76, math.ceil(maximum_duration * 12.5) + 1)
    return text_budget if configured_max is None else min(text_budget, max(76, int(configured_max)))


def split_generated_speech(text: str, max_chars: int = 96) -> list[str]:
    """Split long speech at sentence boundaries, then bounded word boundaries."""
    clean = " ".join(str(text or "").split())
    if not clean:
        return []
    if len(clean) <= max_chars:
        return [clean]
    pieces = re.split(r"(?<=[.!?])\s+", clean)
    segments: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current} {piece}".strip()
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            segments.append(current)
        wrapped = textwrap.wrap(piece, width=max_chars, break_on_hyphens=False)
        segments.extend(wrapped[:-1])
        current = wrapped[-1]
    if current:
        segments.append(current)
    return segments


def validate_generated_speech_duration(duration_seconds: float, text: str) -> None:
    """Reject audio whose duration is implausible for its requested text."""
    duration = float(duration_seconds)
    character_count = len(" ".join(str(text or "").split()))
    minimum_duration, maximum_duration = generated_speech_duration_bounds(text)
    if duration < minimum_duration:
        raise GeneratedSpeechTooShortError(
            "Generated speech is too short for the requested text "
            f"({duration:.2f}s for {character_count} characters)."
        )
    if duration > maximum_duration:
        raise GeneratedSpeechTooLongError(
            "Generated speech is too long for the requested text "
            f"({duration:.2f}s for {character_count} characters)."
        )


def prepare_generated_speech_audio(
    audio: np.ndarray,
    sample_rate: int,
    text: str,
) -> np.ndarray:
    """Trim harmless edge silence and reject pathological TTS output."""
    rate = int(sample_rate)
    if rate <= 0:
        raise AudioProcessingError("Generated audio sample rate must be positive.")
    waveform = np.asarray(audio, dtype=np.float32).reshape(-1)
    if waveform.size == 0 or not np.all(np.isfinite(waveform)):
        raise AudioProcessingError("Generated speech returned invalid or empty audio.")

    peak = float(np.max(np.abs(waveform)))
    if peak < 1e-4:
        raise AudioProcessingError("Generated speech returned effectively silent audio.")
    silence_threshold = max(1e-5, peak * 0.01)
    voiced = np.abs(waveform) > silence_threshold
    voiced_indexes = np.flatnonzero(voiced)
    if voiced_indexes.size == 0:
        raise AudioProcessingError("Generated speech returned effectively silent audio.")

    first_voiced = int(voiced_indexes[0])
    last_voiced = int(voiced_indexes[-1])
    edge_padding = int(round(rate * 0.25))
    start = max(0, first_voiced - edge_padding)
    end = min(waveform.size, last_voiced + edge_padding + 1)
    prepared = waveform[start:end]
    duration = prepared.size / rate
    validate_generated_speech_duration(duration, text)
    return prepared


def _ffmpeg_decode_mono(source: Path, *, sample_rate: int) -> tuple[np.ndarray, int]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AudioProcessingError(
            "This audio source requires FFmpeg, but ffmpeg was not found."
        )
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        decoded = Path(handle.name)
    try:
        process = subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-ac",
                "1",
                "-ar",
                str(int(sample_rate)),
                "-c:a",
                "pcm_f32le",
                str(decoded),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if process.returncode != 0:
            message = process.stderr.strip() or "FFmpeg could not decode the audio."
            raise AudioProcessingError(message)
        audio, decoded_rate = sf.read(
            decoded,
            dtype="float32",
            always_2d=True,
        )
    finally:
        decoded.unlink(missing_ok=True)
    return np.mean(audio, axis=1, dtype=np.float32), int(decoded_rate)


def decode_audio_mono(
    source_path: str | Path,
    *,
    sample_rate: int,
) -> tuple[np.ndarray, int]:
    """Decode one audio source to mono float32 without importing SciPy/Librosa."""
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise AudioProcessingError(f"Audio source does not exist: {source}")
    if source.stat().st_size == 0:
        raise AudioProcessingError("Audio source is empty.")
    target_rate = int(sample_rate)
    if target_rate <= 0:
        raise AudioProcessingError("Target sample rate must be positive.")

    try:
        audio, original_rate = sf.read(
            source,
            dtype="float32",
            always_2d=True,
        )
        waveform = np.mean(audio, axis=1, dtype=np.float32)
    except Exception:
        return _ffmpeg_decode_mono(source, sample_rate=target_rate)

    if waveform.size == 0:
        raise AudioProcessingError("Audio source is empty.")
    original_rate = int(original_rate)
    if original_rate == target_rate:
        return waveform, original_rate

    try:
        import soxr

        resampled = soxr.resample(
            waveform,
            original_rate,
            target_rate,
            quality="HQ",
        )
    except Exception:
        return _ffmpeg_decode_mono(source, sample_rate=target_rate)
    return np.asarray(resampled, dtype=np.float32).reshape(-1), target_rate


def write_canonical_wav(
    source_path: str | Path,
    target_path: str | Path,
    *,
    sample_rate: int,
    subtype: str = "PCM_16",
) -> Path:
    source = Path(source_path).expanduser().resolve()
    target = Path(target_path).expanduser().resolve()
    waveform, decoded_rate = decode_audio_mono(
        source,
        sample_rate=sample_rate,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    sf.write(
        target,
        waveform,
        decoded_rate,
        subtype=subtype,
    )
    try:
        info = sf.info(target)
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise AudioProcessingError(
            f"Canonical WAV could not be verified: {exc}"
        ) from exc
    if info.format != "WAV" or info.frames <= 0:
        target.unlink(missing_ok=True)
        raise AudioProcessingError(
            "Canonical audio is not a valid non-empty WAV file."
        )
    return target


@contextmanager
def temporary_mono_wav(
    source_path: str | Path,
    *,
    sample_rate: int,
) -> Iterator[Path]:
    """Yield a mono WAV at the exact model rate, cleaning temporary bytes."""
    source = Path(source_path).expanduser().resolve()
    try:
        info = sf.info(source)
    except Exception:
        info = None
    if (
        info is not None
        and info.format == "WAV"
        and int(info.channels) == 1
        and int(info.samplerate) == int(sample_rate)
        and int(info.frames) > 0
    ):
        yield source
        return

    with tempfile.NamedTemporaryFile(
        prefix="alexandria-reference-",
        suffix=".wav",
        delete=False,
    ) as handle:
        prepared = Path(handle.name)
    try:
        write_canonical_wav(
            source,
            prepared,
            sample_rate=sample_rate,
            subtype="FLOAT",
        )
        yield prepared
    finally:
        prepared.unlink(missing_ok=True)


@contextmanager
def temporary_clone_reference_wav(
    source_path: str | Path,
    *,
    sample_rate: int,
) -> Iterator[Path]:
    """Yield a clone reference with a clean acoustic boundary after speech.

    Qwen ICL generation continues directly after the reference codec tokens.
    A hard-cut reference can therefore leak its final vocal posture into the
    first phoneme of every generated line. Preserve the reference content,
    release its final 20 ms to zero, and append three 80 ms silence frames so
    target generation begins from a stable silent boundary.
    """
    with temporary_mono_wav(
        source_path,
        sample_rate=sample_rate,
    ) as normalized_reference:
        decoded, decoded_rate = sf.read(
            normalized_reference,
            dtype="float32",
            always_2d=True,
        )
        waveform = np.mean(decoded, axis=1, dtype=np.float32)
        if waveform.size == 0 or not np.all(np.isfinite(waveform)):
            raise AudioProcessingError("Clone reference returned invalid audio.")

        release_samples = min(
            waveform.size,
            max(1, round(decoded_rate * CLONE_REFERENCE_RELEASE_MS / 1000.0)),
        )
        conditioned = waveform.copy()
        conditioned[-release_samples:] *= np.linspace(
            1.0,
            0.0,
            release_samples,
            dtype=np.float32,
        )
        silence_samples = max(
            1,
            round(
                decoded_rate
                * CLONE_REFERENCE_TRAILING_SILENCE_MS
                / 1000.0
            ),
        )
        conditioned = np.concatenate(
            (conditioned, np.zeros(silence_samples, dtype=np.float32))
        )

        with tempfile.NamedTemporaryFile(
            prefix="alexandria-clone-reference-",
            suffix=".wav",
            delete=False,
        ) as handle:
            prepared = Path(handle.name)
        try:
            sf.write(
                prepared,
                conditioned,
                decoded_rate,
                subtype="FLOAT",
            )
            yield prepared
        finally:
            prepared.unlink(missing_ok=True)
