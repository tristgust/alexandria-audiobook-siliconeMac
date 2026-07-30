#!/usr/bin/env python3
"""Add objective text/start/loudness diagnostics to the five-Voice audition."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

import mlx_whisper
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = ROOT / ".omo/evidence/five-recurring-voice-acceptance-v1"
WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"


def normalized_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", str(text).casefold())


def word_error_rate(expected: str, observed: str) -> float:
    left = normalized_words(expected)
    right = normalized_words(observed)
    previous = list(range(len(right) + 1))
    for index, left_word in enumerate(left, start=1):
        current = [index]
        for right_index, right_word in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_word != right_word),
                )
            )
        previous = current
    return previous[-1] / max(1, len(left))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def silence_seconds(mono: np.ndarray, sample_rate: int, *, leading: bool) -> float:
    if not len(mono):
        return 0.0
    threshold = 10.0 ** (-42.0 / 20.0)
    active = np.flatnonzero(np.abs(mono) >= threshold)
    if not len(active):
        return len(mono) / sample_rate
    samples = int(active[0]) if leading else int(len(mono) - active[-1] - 1)
    return samples / sample_rate


def audio_metrics(path: Path) -> dict[str, Any]:
    audio, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = np.mean(audio, axis=1, dtype=np.float32)
    rms = float(np.sqrt(np.mean(np.square(mono, dtype=np.float64)))) if len(mono) else 0.0
    peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
    return {
        "sample_rate": int(sample_rate),
        "duration_seconds": len(mono) / int(sample_rate) if sample_rate else 0.0,
        "rms_dbfs": 20.0 * math.log10(max(rms, 1e-12)),
        "peak_dbfs": 20.0 * math.log10(max(peak, 1e-12)),
        "clipped_sample_ratio": float(np.mean(np.abs(mono) >= 0.999)) if len(mono) else 0.0,
        "leading_silence_seconds": silence_seconds(mono, int(sample_rate), leading=True),
        "trailing_silence_seconds": silence_seconds(mono, int(sample_rate), leading=False),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE)
    args = parser.parse_args()
    evidence = args.evidence_root.expanduser().resolve()
    summary_path = evidence / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    rows = []
    for row in summary["lines"]:
        path = evidence / row["audio_path"]
        result = mlx_whisper.transcribe(
            str(path),
            path_or_hf_repo=WHISPER_MODEL,
            language="en",
            condition_on_previous_text=False,
            word_timestamps=True,
            verbose=False,
        )
        transcript = str(result.get("text") or "").strip()
        segments = result.get("segments") if isinstance(result, dict) else None
        first_segment_start = None
        if isinstance(segments, list) and segments:
            try:
                first_segment_start = float(segments[0].get("start"))
            except (TypeError, ValueError, AttributeError):
                first_segment_start = None
        expected_words = normalized_words(row["text"])
        observed_words = normalized_words(transcript)
        diagnostics = audio_metrics(path)
        record = {
            "index": row["index"],
            "speaker": row["speaker"],
            "audio_sha256": sha256_file(path),
            "automatic_transcript": transcript,
            "word_error_rate": word_error_rate(row["text"], transcript),
            "exact_normalized_text": expected_words == observed_words,
            "expected_first_word": expected_words[0] if expected_words else None,
            "observed_first_word": observed_words[0] if observed_words else None,
            "first_word_present": bool(
                expected_words and observed_words and expected_words[0] == observed_words[0]
            ),
            "first_segment_start_seconds": first_segment_start,
            "audio": diagnostics,
            "whisper_model": WHISPER_MODEL,
        }
        record["automatic_gate_passed"] = bool(
            record["word_error_rate"] <= 0.15
            and record["first_word_present"]
            and diagnostics["leading_silence_seconds"] <= 0.35
            and diagnostics["clipped_sample_ratio"] <= 0.0001
            and diagnostics["peak_dbfs"] <= -0.05
        )
        rows.append(record)
        print(
            json.dumps(
                {
                    "index": record["index"],
                    "speaker": record["speaker"],
                    "wer": record["word_error_rate"],
                    "first_word_present": record["first_word_present"],
                    "leading_silence": diagnostics["leading_silence_seconds"],
                    "gate": record["automatic_gate_passed"],
                }
            )
        )
    summary["objective"] = {
        "rows": rows,
        "all_automatic_gates_passed": all(row["automatic_gate_passed"] for row in rows),
        "exact_transcript_count": sum(row["exact_normalized_text"] for row in rows),
        "first_word_pass_count": sum(row["first_word_present"] for row in rows),
        "maximum_word_error_rate": max(row["word_error_rate"] for row in rows),
        "maximum_leading_silence_seconds": max(
            row["audio"]["leading_silence_seconds"] for row in rows
        ),
        "whisper_model": WHISPER_MODEL,
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary["objective"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
