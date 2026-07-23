from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
import soundfile as sf

from audio_processing import AudioProcessingError, decode_audio_mono


SUPPORTED_MEDIA_EXTENSIONS = frozenset(
    {
        ".wav",
        ".mp3",
        ".flac",
        ".ogg",
        ".m4a",
        ".mp4",
        ".mov",
        ".mkv",
        ".webm",
    }
)
from hf_access import snapshot_download_with_public_fallback
from model_registry import is_registered_model, model_spec, resolve_model_path


TARGET_SAMPLE_RATE = 24_000
DEFAULT_WHISPER_MODEL = os.environ.get(
    "ALEXANDRIA_WHISPER_MODEL",
    model_spec("mlx_whisper_large_v3_turbo").repo_id,
)
MIN_CLIP_SECONDS = 1.2
PREFERRED_MIN_SECONDS = 2.0
PREFERRED_MAX_SECONDS = 12.0
HARD_MAX_SECONDS = 18.0
BOUNDARY_GAP_SECONDS = 0.65
CLIP_PADDING_SECONDS = 0.12


class AudioPreparerError(RuntimeError):
    """Raised when an owned-recording dataset cannot be prepared safely."""


@dataclass(frozen=True)
class TranscriptCandidate:
    start_seconds: float
    end_seconds: float
    text: str
    confidence: float
    segment_index: int


@dataclass(frozen=True)
class AcceptedClip:
    filename: str
    text: str
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    confidence: float
    snr_db: float
    source_segment_index: int


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _clean_transcript(value: object) -> str:
    text = " ".join(str(value or "").replace("\ufffd", "").split())
    return text.strip()


def _clamp_probability(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return max(0.0, min(1.0, number))


def _segment_confidence(segment: dict[str, Any]) -> float:
    words = segment.get("words")
    probabilities: list[float] = []
    if isinstance(words, list):
        for word in words:
            if not isinstance(word, dict) or "probability" not in word:
                continue
            probabilities.append(_clamp_probability(word.get("probability")))
    if probabilities:
        return float(sum(probabilities) / len(probabilities))

    try:
        average_log_probability = float(segment.get("avg_logprob"))
        confidence = math.exp(average_log_probability)
    except (TypeError, ValueError, OverflowError):
        confidence = 0.0

    no_speech_probability = _clamp_probability(
        segment.get("no_speech_prob"),
        default=0.0,
    )
    return _clamp_probability(confidence * (1.0 - no_speech_probability))


def _word_text(word: dict[str, Any]) -> str:
    return str(word.get("word", word.get("text", "")))


def _word_probability(word: dict[str, Any], fallback: float) -> float:
    if "probability" not in word:
        return fallback
    return _clamp_probability(word.get("probability"), default=fallback)


def _valid_word(word: object) -> bool:
    if not isinstance(word, dict):
        return False
    try:
        start = float(word.get("start"))
        end = float(word.get("end"))
    except (TypeError, ValueError):
        return False
    return end > start and bool(_clean_transcript(_word_text(word)))


def _candidate_from_words(
    words: list[dict[str, Any]],
    *,
    segment_index: int,
    fallback_confidence: float,
) -> TranscriptCandidate | None:
    if not words:
        return None
    text = _clean_transcript("".join(_word_text(word) for word in words))
    if not text:
        return None
    probabilities = [
        _word_probability(word, fallback_confidence)
        for word in words
    ]
    return TranscriptCandidate(
        start_seconds=float(words[0]["start"]),
        end_seconds=float(words[-1]["end"]),
        text=text,
        confidence=float(sum(probabilities) / len(probabilities)),
        segment_index=segment_index,
    )


def _split_words_into_candidates(
    words: list[dict[str, Any]],
    *,
    segment_index: int,
    fallback_confidence: float,
) -> list[TranscriptCandidate]:
    usable = [word for word in words if _valid_word(word)]
    if not usable:
        return []

    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []

    for position, word in enumerate(usable):
        if current:
            proposed_duration = float(word["end"]) - float(current[0]["start"])
            if proposed_duration > HARD_MAX_SECONDS:
                groups.append(current)
                current = []

        current.append(word)
        duration = float(current[-1]["end"]) - float(current[0]["start"])
        next_word = usable[position + 1] if position + 1 < len(usable) else None
        gap = (
            float(next_word["start"]) - float(current[-1]["end"])
            if next_word is not None
            else 0.0
        )
        ends_sentence = _clean_transcript(_word_text(current[-1])).endswith(
            (".", "?", "!", ";", ":")
        )
        natural_boundary = (
            duration >= PREFERRED_MIN_SECONDS
            and (ends_sentence or gap >= BOUNDARY_GAP_SECONDS)
        )
        forced_boundary = duration >= PREFERRED_MAX_SECONDS
        final_word = next_word is None

        if natural_boundary or forced_boundary or final_word:
            groups.append(current)
            current = []

    if current:
        groups.append(current)

    # Merge a very short trailing fragment into its predecessor when doing so
    # remains within the hard maximum. This avoids unusable one-word clips.
    if len(groups) >= 2:
        trailing = groups[-1]
        trailing_duration = float(trailing[-1]["end"]) - float(trailing[0]["start"])
        merged_duration = float(trailing[-1]["end"]) - float(groups[-2][0]["start"])
        if trailing_duration < MIN_CLIP_SECONDS and merged_duration <= HARD_MAX_SECONDS:
            groups[-2].extend(trailing)
            groups.pop()

    candidates = [
        _candidate_from_words(
            group,
            segment_index=segment_index,
            fallback_confidence=fallback_confidence,
        )
        for group in groups
    ]
    return [candidate for candidate in candidates if candidate is not None]


def transcript_candidates(result: dict[str, Any]) -> list[TranscriptCandidate]:
    segments = result.get("segments")
    if not isinstance(segments, list):
        raise AudioPreparerError(
            "Whisper returned no timestamped segments. The audio could not be "
            "converted into reviewable clips."
        )

    candidates: list[TranscriptCandidate] = []
    for segment_index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            continue
        confidence = _segment_confidence(segment)
        words = segment.get("words")
        if isinstance(words, list):
            candidates.extend(
                _split_words_into_candidates(
                    words,
                    segment_index=segment_index,
                    fallback_confidence=confidence,
                )
            )
            continue

        text = _clean_transcript(segment.get("text"))
        try:
            start = float(segment.get("start"))
            end = float(segment.get("end"))
        except (TypeError, ValueError):
            continue
        if text and end > start:
            candidates.append(
                TranscriptCandidate(
                    start_seconds=start,
                    end_seconds=end,
                    text=text,
                    confidence=confidence,
                    segment_index=segment_index,
                )
            )

    if not candidates:
        raise AudioPreparerError(
            "Whisper returned no usable spoken segments. Check that the source "
            "contains clear speech and uses the selected language."
        )
    return candidates


def _frame_rms(
    audio: np.ndarray,
    *,
    frame_length: int = 1024,
    hop_length: int = 256,
) -> np.ndarray:
    samples = np.asarray(audio, dtype=np.float32)
    if samples.size < frame_length:
        samples = np.pad(samples, (0, frame_length - samples.size))
    padding = frame_length // 2
    padded = np.pad(samples, (padding, padding))
    maximum_start = max(0, padded.size - frame_length)
    starts = np.arange(0, maximum_start + 1, hop_length, dtype=np.int64)
    squared = np.square(padded, dtype=np.float64)
    cumulative = np.concatenate(
        (np.zeros(1, dtype=np.float64), np.cumsum(squared, dtype=np.float64))
    )
    energy = cumulative[starts + frame_length] - cumulative[starts]
    return np.sqrt(energy / float(frame_length)).astype(np.float32)


def estimate_snr_db(
    audio: np.ndarray,
    noise_reference: np.ndarray | None = None,
) -> float:
    samples = np.asarray(audio, dtype=np.float32)
    if samples.size == 0:
        return float("-inf")
    if samples.size < 1024:
        samples = np.pad(samples, (0, 1024 - samples.size))

    rms = _frame_rms(samples)
    finite = rms[np.isfinite(rms)]
    if finite.size == 0:
        return float("-inf")

    signal = float(np.percentile(finite, 75))

    noise_samples = (
        np.asarray(noise_reference, dtype=np.float32)
        if noise_reference is not None
        else np.empty(0, dtype=np.float32)
    )
    if noise_samples.size:
        if noise_samples.size < 1024:
            noise_samples = np.pad(
                noise_samples,
                (0, 1024 - noise_samples.size),
            )
        noise_rms = _frame_rms(noise_samples)
        finite_noise = noise_rms[np.isfinite(noise_rms)]
        noise = (
            float(np.percentile(finite_noise, 20))
            if finite_noise.size
            else float(np.percentile(finite, 10))
        )
    else:
        noise = float(np.percentile(finite, 10))
    if signal <= 1e-8:
        return float("-inf")
    if noise <= 1e-8:
        return 60.0
    return float(max(-20.0, min(60.0, 20.0 * math.log10(signal / noise))))


def _normalize_clip(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    clip = np.asarray(audio, dtype=np.float32).copy()
    if clip.size == 0:
        return clip
    peak = float(np.max(np.abs(clip)))
    if peak > 1e-8:
        clip *= 0.891250938 / peak  # -1 dBFS peak.

    fade_samples = min(int(sample_rate * 0.005), clip.size // 2)
    if fade_samples > 0:
        fade = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
        clip[:fade_samples] *= fade
        clip[-fade_samples:] *= fade[::-1]
    return np.clip(clip, -1.0, 1.0)


def _reference_score(clip: AcceptedClip) -> float:
    duration_penalty = abs(clip.duration_seconds - 7.0) * 0.75
    extreme_duration_penalty = (
        12.0
        if clip.duration_seconds < 2.0 or clip.duration_seconds > 15.0
        else 0.0
    )
    return (
        clip.confidence * 100.0
        + min(clip.snr_db, 50.0) * 0.45
        - duration_penalty
        - extreme_duration_penalty
    )


def _default_transcriber(
    audio_path: Path,
    *,
    language: str,
    model: str,
) -> dict[str, Any]:
    try:
        import mlx_whisper
    except ImportError as exc:
        raise AudioPreparerError(
            "mlx-whisper is not installed or could not load. Re-run the "
            "Alexandria Pinokio Install action."
        ) from exc

    model_path = Path(model).expanduser()
    if model_path.exists():
        resolved_model = model_path.resolve()
    elif is_registered_model(model):
        resolved_model = resolve_model_path(model, local_files_only=True)
    else:
        resolved_model = snapshot_download_with_public_fallback(
            model,
            local_files_only=True,
        )

    return mlx_whisper.transcribe(
        str(audio_path),
        path_or_hf_repo=str(resolved_model),
        language=language or None,
        word_timestamps=True,
        condition_on_previous_text=False,
        no_speech_threshold=0.6,
        logprob_threshold=-1.0,
        compression_ratio_threshold=2.4,
        verbose=False,
    )


def _write_zip(source_dir: Path, output_path: Path) -> None:
    temporary_zip = output_path.with_suffix(output_path.suffix + ".tmp")
    if temporary_zip.exists():
        temporary_zip.unlink()
    try:
        with zipfile.ZipFile(
            temporary_zip,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            for path in sorted(source_dir.iterdir(), key=lambda item: item.name):
                if path.is_file():
                    archive.write(path, arcname=path.name)
        os.replace(temporary_zip, output_path)
    finally:
        if temporary_zip.exists():
            temporary_zip.unlink()


def prepare_dataset(
    *,
    audio_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    language: str = "en",
    min_confidence: float = 0.85,
    min_snr: float = 25.0,
    model: str = DEFAULT_WHISPER_MODEL,
    transcriber: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    source = Path(audio_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    if not source.is_file():
        raise AudioPreparerError(f"Audio source does not exist: {source}")
    if source.suffix.lower() not in SUPPORTED_MEDIA_EXTENSIONS:
        raise AudioPreparerError(
            "Unsupported media format. Use WAV, MP3, FLAC, OGG, M4A, "
            "MP4, MOV, MKV, or WEBM."
        )
    if output.suffix.lower() != ".zip":
        raise AudioPreparerError("Output filename must end in .zip")
    if output.exists():
        raise AudioPreparerError(
            f"Output already exists and will not be overwritten: {output.name}"
        )
    if not 0.0 <= float(min_confidence) <= 1.0:
        raise AudioPreparerError("Minimum confidence must be between 0 and 1.")
    if float(min_snr) < 0.0:
        raise AudioPreparerError("Minimum SNR must be non-negative.")

    output.parent.mkdir(parents=True, exist_ok=True)
    print(f"Loading audio: {source}", flush=True)
    try:
        waveform, sample_rate = decode_audio_mono(
            source,
            sample_rate=TARGET_SAMPLE_RATE,
        )
    except (AudioProcessingError, OSError, ValueError) as exc:
        raise AudioPreparerError(
            f"Audio could not be decoded: {exc}. Verify that FFmpeg is installed."
        ) from exc
    if waveform.size == 0:
        raise AudioPreparerError("Audio source is empty.")
    duration_seconds = waveform.size / float(sample_rate)
    print(
        f"Loaded {duration_seconds:.2f} seconds at {sample_rate} Hz mono.",
        flush=True,
    )

    run_transcriber = transcriber or _default_transcriber
    print(f"Transcribing with {model} ({language})...", flush=True)
    result = run_transcriber(
        source,
        language=language,
        model=model,
    )
    candidates = transcript_candidates(result)
    print(f"Whisper produced {len(candidates)} candidate clip(s).", flush=True)

    rejected = {
        "empty": 0,
        "duration": 0,
        "confidence": 0,
        "snr": 0,
    }
    accepted: list[AcceptedClip] = []

    with tempfile.TemporaryDirectory(
        prefix="alexandria-preparer-",
        dir=str(output.parent),
    ) as temporary:
        dataset_dir = Path(temporary) / "dataset"
        dataset_dir.mkdir(parents=True)

        for candidate_index, candidate in enumerate(candidates):
            start = max(0.0, candidate.start_seconds - CLIP_PADDING_SECONDS)
            end = min(duration_seconds, candidate.end_seconds + CLIP_PADDING_SECONDS)
            clip_duration = end - start
            if not candidate.text:
                rejected["empty"] += 1
                continue
            if clip_duration < MIN_CLIP_SECONDS or clip_duration > HARD_MAX_SECONDS:
                rejected["duration"] += 1
                continue
            if candidate.confidence < float(min_confidence):
                rejected["confidence"] += 1
                continue

            start_frame = max(0, int(round(start * sample_rate)))
            end_frame = min(waveform.size, int(round(end * sample_rate)))
            clip_audio = waveform[start_frame:end_frame]
            noise_window_frames = int(round(0.5 * sample_rate))
            before_noise = waveform[
                max(0, start_frame - noise_window_frames):start_frame
            ]
            after_noise = waveform[
                end_frame:min(waveform.size, end_frame + noise_window_frames)
            ]
            noise_reference = np.concatenate(
                [before_noise, after_noise]
            )
            snr_db = estimate_snr_db(
                clip_audio,
                noise_reference=noise_reference,
            )
            if snr_db < float(min_snr):
                rejected["snr"] += 1
                continue

            filename = f"sample_{len(accepted):04d}.wav"
            normalized = _normalize_clip(clip_audio, sample_rate)
            sf.write(
                dataset_dir / filename,
                normalized,
                sample_rate,
                subtype="PCM_16",
            )
            accepted.append(
                AcceptedClip(
                    filename=filename,
                    text=candidate.text,
                    start_seconds=round(start, 3),
                    end_seconds=round(end, 3),
                    duration_seconds=round(clip_duration, 3),
                    confidence=round(candidate.confidence, 4),
                    snr_db=round(snr_db, 2),
                    source_segment_index=candidate.segment_index,
                )
            )
            print(
                f"Accepted {filename}: {clip_duration:.2f}s · "
                f"confidence {candidate.confidence:.2f} · SNR {snr_db:.1f} dB",
                flush=True,
            )

        if not accepted:
            rejection_summary = ", ".join(
                f"{key}={value}" for key, value in rejected.items()
            )
            raise AudioPreparerError(
                "No clips passed the current filters "
                f"({rejection_summary}). Check the language and audio, then "
                "lower Confidence or Minimum SNR deliberately."
            )

        reference = max(accepted, key=_reference_score)
        shutil.copy2(dataset_dir / reference.filename, dataset_dir / "ref.wav")
        (dataset_dir / "ref_text.txt").write_text(
            reference.text,
            encoding="utf-8",
        )

        metadata_path = dataset_dir / "metadata.jsonl"
        with metadata_path.open("w", encoding="utf-8") as handle:
            for clip in accepted:
                record = {
                    "audio_filepath": clip.filename,
                    "text": clip.text,
                    "ref_audio": "ref.wav",
                    "duration_seconds": clip.duration_seconds,
                    "transcript_confidence": clip.confidence,
                    "snr_db": clip.snr_db,
                    "source_start_seconds": clip.start_seconds,
                    "source_end_seconds": clip.end_seconds,
                    "source_segment_index": clip.source_segment_index,
                    "review_status": "unreviewed",
                }
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        manifest = {
            "schema_version": 1,
            "source_audio": source.name,
            "source_audio_sha256": _sha256_file(source),
            "source_duration_seconds": round(duration_seconds, 3),
            "sample_rate": sample_rate,
            "language": language,
            "transcription_backend": "mlx-whisper",
            "transcription_model": model,
            "minimum_confidence": float(min_confidence),
            "minimum_snr_db": float(min_snr),
            "candidate_count": len(candidates),
            "accepted_count": len(accepted),
            "rejected": rejected,
            "reference_sample": reference.filename,
            "reference_text": reference.text,
            "clips": [asdict(clip) for clip in accepted],
            "review_required": True,
            "same_speaker_assertion_required": True,
        }
        (dataset_dir / "preparation_manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        _write_zip(dataset_dir, output)

    print(
        f"Prepared {len(accepted)} clip(s); reference={reference.filename}.",
        flush=True,
    )
    print(f"Dataset ZIP: {output}", flush=True)
    return {
        "output_path": str(output),
        "sample_count": len(accepted),
        "candidate_count": len(candidates),
        "reference_sample": reference.filename,
        "rejected": rejected,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Transcribe one owned recording and package filtered 24 kHz clips "
            "for Alexandria review."
        )
    )
    parser.add_argument("--audio", required=True, help="Input audio file")
    parser.add_argument("--output", required=True, help="Output .zip path")
    parser.add_argument("--lang", default="en", help="Whisper language code")
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.85,
        help="Minimum transcript confidence from 0 to 1",
    )
    parser.add_argument(
        "--min-snr",
        type=float,
        default=25.0,
        help="Minimum estimated signal-to-noise ratio in dB",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_WHISPER_MODEL,
        help="MLX Whisper model path or public Hugging Face repo",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        prepare_dataset(
            audio_path=args.audio,
            output_path=args.output,
            language=args.lang,
            min_confidence=args.min_confidence,
            min_snr=args.min_snr,
            model=args.model,
        )
    except AudioPreparerError as exc:
        print(f"Error: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
