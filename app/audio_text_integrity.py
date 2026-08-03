from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import librosa
import numpy as np

from fish_cloud_tts import normalized_words, terminal_text_matches, word_error_rate
from model_registry import model_spec, resolve_model_path


_SIBILANT_SUFFIXES = (
    "s",
    "ss",
    "ce",
    "se",
    "x",
    "z",
    "sh",
    "ch",
)
_SIBILANT_EXCEPTIONS = frozenset(
    {
        "as",
        "does",
        "has",
        "his",
        "is",
        "says",
        "was",
    }
)


@dataclass(frozen=True)
class TerminalAcousticFeatures:
    expected_sibilant: bool
    high_frequency_ms: float
    high_zero_crossing_ms: float
    maximum_high_frequency_ratio: float
    maximum_zero_crossing_rate: float
    weak_sibilant_release: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TextIntegrityAssessment:
    transcript: str
    word_error_rate: float
    terminal_text_passed: bool
    unexpected_repetitions: tuple[str, ...]
    terminal_acoustics: TerminalAcousticFeatures | None

    @property
    def needs_review(self) -> bool:
        return bool(
            self.word_error_rate > 0.08
            or not self.terminal_text_passed
            or self.unexpected_repetitions
            or (
                self.terminal_acoustics is not None
                and self.terminal_acoustics.weak_sibilant_release
            )
        )

    def reasons(self) -> list[str]:
        reasons: list[str] = []
        if self.word_error_rate > 0.08:
            reasons.append("word_error_rate")
        if not self.terminal_text_passed:
            reasons.append("terminal_text_mismatch")
        if self.unexpected_repetitions:
            reasons.append("unexpected_repetition")
        if (
            self.terminal_acoustics is not None
            and self.terminal_acoustics.weak_sibilant_release
        ):
            reasons.append("weak_terminal_sibilant")
        return reasons

    def as_dict(self) -> dict[str, Any]:
        return {
            "transcript": self.transcript,
            "word_error_rate": round(self.word_error_rate, 6),
            "terminal_text_passed": self.terminal_text_passed,
            "unexpected_repetitions": list(self.unexpected_repetitions),
            "terminal_acoustics": (
                self.terminal_acoustics.as_dict()
                if self.terminal_acoustics is not None
                else None
            ),
            "needs_review": self.needs_review,
            "reasons": self.reasons(),
        }


def unexpected_repetitions(reference: str, hypothesis: str) -> tuple[str, ...]:
    expected = normalized_words(reference)
    actual = normalized_words(hypothesis)
    expected_repeats = {
        word
        for word, following in zip(expected, expected[1:])
        if word == following
    }
    repeated: list[str] = []
    for word, following in zip(actual, actual[1:]):
        if (
            word == following
            and word not in expected_repeats
            and word not in repeated
        ):
            repeated.append(word)
    return tuple(repeated)


def expects_terminal_sibilant(text: str) -> bool:
    words = normalized_words(text)
    if not words:
        return False
    final = words[-1]
    return bool(
        len(final) > 2
        and final not in _SIBILANT_EXCEPTIONS
        and final.endswith(_SIBILANT_SUFFIXES)
    )


def _final_word_times(words: Sequence[Mapping[str, Any]]) -> tuple[float, float] | None:
    if not words:
        return None
    final = words[-1]
    try:
        start = float(final.get("start"))
        end = float(final.get("end"))
    except (TypeError, ValueError):
        return None
    if not np.isfinite(start) or not np.isfinite(end) or end <= start:
        return None
    return start, end


def terminal_acoustic_features(
    audio_path: str | Path,
    *,
    expected_text: str,
    transcript_words: Sequence[Mapping[str, Any]],
) -> TerminalAcousticFeatures | None:
    expected_sibilant = expects_terminal_sibilant(expected_text)
    times = _final_word_times(transcript_words)
    if not expected_sibilant or times is None:
        return None

    source = Path(audio_path).expanduser().resolve()
    waveform, sample_rate = librosa.load(source, sr=None, mono=True)
    if waveform.size == 0 or sample_rate <= 0:
        return None

    start_seconds, end_seconds = times
    start = max(0, int((start_seconds - 0.02) * sample_rate))
    end = min(waveform.size, int((end_seconds + 0.04) * sample_rate))
    segment = np.asarray(waveform[start:end], dtype=np.float32)
    frame_length = max(128, int(round(sample_rate * 0.03)))
    hop_length = max(64, int(round(sample_rate * 0.005)))
    if segment.size < frame_length:
        return None

    n_fft = 1
    while n_fft < frame_length:
        n_fft *= 2
    spectrum = np.abs(
        librosa.stft(
            segment,
            n_fft=n_fft,
            hop_length=hop_length,
            win_length=frame_length,
            center=False,
        )
    ) ** 2
    frequencies = librosa.fft_frequencies(sr=sample_rate, n_fft=n_fft)
    high_frequency_ratio = spectrum[frequencies >= 4000].sum(axis=0) / (
        spectrum.sum(axis=0) + 1e-12
    )
    zero_crossing_rate = librosa.feature.zero_crossing_rate(
        segment,
        frame_length=frame_length,
        hop_length=hop_length,
        center=False,
    )[0]
    high_frequency_frames = int(np.count_nonzero(high_frequency_ratio > 0.20))
    high_zero_crossing_frames = int(np.count_nonzero(zero_crossing_rate > 0.25))
    high_frequency_ms = high_frequency_frames * hop_length / sample_rate * 1000
    high_zero_crossing_ms = high_zero_crossing_frames * hop_length / sample_rate * 1000
    weak = high_frequency_ms < 15.0 and high_zero_crossing_ms < 15.0
    return TerminalAcousticFeatures(
        expected_sibilant=True,
        high_frequency_ms=round(high_frequency_ms, 3),
        high_zero_crossing_ms=round(high_zero_crossing_ms, 3),
        maximum_high_frequency_ratio=round(float(high_frequency_ratio.max()), 6),
        maximum_zero_crossing_rate=round(float(zero_crossing_rate.max()), 6),
        weak_sibilant_release=weak,
    )


def transcript_words(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for segment in result.get("segments") or []:
        if not isinstance(segment, Mapping):
            continue
        for word in segment.get("words") or []:
            if isinstance(word, Mapping):
                words.append(dict(word))
    return words


def assess_transcription(
    *,
    expected_text: str,
    transcript_result: Mapping[str, Any],
    audio_path: str | Path,
) -> TextIntegrityAssessment:
    transcript = str(transcript_result.get("text") or "").strip()
    words = transcript_words(transcript_result)
    return TextIntegrityAssessment(
        transcript=transcript,
        word_error_rate=word_error_rate(expected_text, transcript),
        terminal_text_passed=terminal_text_matches(expected_text, transcript),
        unexpected_repetitions=unexpected_repetitions(expected_text, transcript),
        terminal_acoustics=terminal_acoustic_features(
            audio_path,
            expected_text=expected_text,
            transcript_words=words,
        ),
    )


def _atomic_json_write(value: Any, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": 1, "chunks": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"schema_version": 1, "chunks": {}}
    if not isinstance(value, dict) or not isinstance(value.get("chunks"), dict):
        return {"schema_version": 1, "chunks": {}}
    return value


def audit_project_audio(
    project_root: str | Path,
    *,
    output_path: str | Path | None = None,
    checkpoint_every: int = 25,
    max_new_chunks: int | None = None,
) -> dict[str, Any]:
    import mlx_whisper

    root = Path(project_root).expanduser().resolve()
    chunks_path = root / "chunks.json"
    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    if not isinstance(chunks, list):
        raise ValueError("chunks.json must contain a list.")
    output = (
        Path(output_path).expanduser().resolve()
        if output_path is not None
        else root / "audio_text_integrity.json"
    )
    report = _load_checkpoint(output)
    existing = report["chunks"]
    model_path = str(resolve_model_path(model_spec("mlx_whisper_base").repo_id))
    started = time.time()
    processed = 0
    eligible_indices: list[int] = []
    for index, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            continue
        relative = str(chunk.get("audio_path") or "").strip()
        if (
            chunk.get("status") == "done"
            and chunk.get("audio_state") == "current"
            and relative
        ):
            source = (root / relative).resolve()
            if source.is_relative_to(root) and source.is_file():
                eligible_indices.append(index)

    for index in eligible_indices:
        chunk = chunks[index]
        if not isinstance(chunk, dict):
            continue
        relative = str(chunk.get("audio_path") or "").strip()
        if (
            chunk.get("status") != "done"
            or chunk.get("audio_state") != "current"
            or not relative
        ):
            continue
        source = (root / relative).resolve()
        if not source.is_relative_to(root) or not source.is_file():
            continue
        sha256 = str(chunk.get("audio_sha256") or "")
        checkpoint = existing.get(str(index))
        if (
            isinstance(checkpoint, dict)
            and checkpoint.get("audio_sha256") == sha256
            and checkpoint.get("text") == chunk.get("text")
        ):
            continue
        result = mlx_whisper.transcribe(
            str(source),
            path_or_hf_repo=model_path,
            language="en",
            word_timestamps=True,
            condition_on_previous_text=False,
            verbose=False,
        )
        assessment = assess_transcription(
            expected_text=str(chunk.get("text") or ""),
            transcript_result=result,
            audio_path=source,
        )
        existing[str(index)] = {
            "index": index,
            "speaker": chunk.get("speaker"),
            "text": chunk.get("text"),
            "instruct": chunk.get("instruct"),
            "audio_path": relative,
            "audio_sha256": sha256,
            "generated_at_utc": chunk.get("generated_at_utc"),
            "cloud_provider": chunk.get("cloud_provider"),
            "generation_provenance": chunk.get("generation_provenance"),
            **assessment.as_dict(),
        }
        processed += 1
        if processed % max(1, int(checkpoint_every)) == 0:
            report.update(
                {
                    "schema_version": 1,
                    "project_root": str(root),
                    "updated_at_utc": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                    ),
                    "complete": False,
                }
            )
            _atomic_json_write(report, output)
        if max_new_chunks is not None and processed >= max(0, int(max_new_chunks)):
            break

    complete_indices = {
        index
        for index in eligible_indices
        if isinstance(existing.get(str(index)), dict)
        and existing[str(index)].get("audio_sha256")
            == str(chunks[index].get("audio_sha256") or "")
        and existing[str(index)].get("text") == chunks[index].get("text")
    }
    complete = len(complete_indices) == len(eligible_indices)
    entries = [
        existing[str(index)]
        for index in eligible_indices
        if isinstance(existing.get(str(index)), dict)
    ]
    flagged = [entry for entry in entries if entry.get("needs_review")]
    reason_counts: dict[str, int] = {}
    speaker_counts: dict[str, int] = {}
    for entry in flagged:
        speaker = str(entry.get("speaker") or "Unknown")
        speaker_counts[speaker] = speaker_counts.get(speaker, 0) + 1
        for reason in entry.get("reasons") or []:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    report.update(
        {
            "schema_version": 1,
            "project_root": str(root),
            "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "complete": complete,
            "elapsed_seconds": round(time.time() - started, 3),
            "processed_this_run": processed,
            "eligible_chunk_count": len(eligible_indices),
            "audited_chunk_count": len(entries),
            "remaining_chunk_count": max(0, len(eligible_indices) - len(complete_indices)),
            "flagged_chunk_count": len(flagged),
            "reason_counts": dict(sorted(reason_counts.items())),
            "speaker_counts": dict(
                sorted(speaker_counts.items(), key=lambda item: (-item[1], item[0]))
            ),
        }
    )
    _atomic_json_write(report, output)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit Alexandria generated audio against authored text."
    )
    parser.add_argument("project_root")
    parser.add_argument("--output")
    parser.add_argument("--checkpoint-every", type=int, default=25)
    parser.add_argument("--max-new-chunks", type=int)
    arguments = parser.parse_args()
    report = audit_project_audio(
        arguments.project_root,
        output_path=arguments.output,
        checkpoint_every=arguments.checkpoint_every,
        max_new_chunks=arguments.max_new_chunks,
    )
    print(
        json.dumps(
            {
                key: report.get(key)
                for key in (
                    "complete",
                    "elapsed_seconds",
                    "processed_this_run",
                    "eligible_chunk_count",
                    "audited_chunk_count",
                    "remaining_chunk_count",
                    "flagged_chunk_count",
                    "reason_counts",
                    "speaker_counts",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
