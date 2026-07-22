from __future__ import annotations

import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import numpy as np
import soundfile as sf


class AudioProcessingError(RuntimeError):
    """Raised when Alexandria cannot decode or normalize an audio source."""


def _ffmpeg_decode_mono(
    source: Path,
    *,
    sample_rate: int,
) -> tuple[np.ndarray, int]:
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
