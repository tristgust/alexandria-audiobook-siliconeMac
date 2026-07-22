#!/usr/bin/env python3
"""Evaluate one cumulative Round 1 review group.

The evaluator is local-only and model-blind in its public output. It computes:
- pinned Whisper Base transcript and WER against each target line;
- Qwen speaker-embedding cosine against the correct identity reference for each
  sample, including style-specific Ryan and model-native anchors;
- compact onset/silence diagnostics useful for spotting clicks or empty audio.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata as metadata
import json
import re
import sys
import types
import unicodedata
from pathlib import Path
from typing import Any

import mlx.core as mx
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))

from run_multimodel_round1_mlx import (  # noqa: E402
    disable_optional_sklearn,
    exact_snapshot,
    load_model,
    prepared_reference_wav,
    sha256_file,
    sha256_text,
)

DEFAULT_EVIDENCE = ROOT / ".omo" / "evidence" / "b17-t05-multimodel-round1"
WHISPER_REPO = "mlx-community/whisper-base-mlx"
WHISPER_REVISION = "1e3e249fb8d01c655324bd6841b1deadffd6d04c"
WHISPER_VERSION = "0.4.3"
_WORD_PATTERN = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)


def normalized_words(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return [word.replace("’", "'") for word in _WORD_PATTERN.findall(normalized)]


def word_error_rate(reference: str, hypothesis: str) -> float:
    left = normalized_words(reference)
    right = normalized_words(hypothesis)
    if not left:
        return 0.0 if not right else 1.0
    previous = list(range(len(right) + 1))
    for index, left_word in enumerate(left, start=1):
        current = [index]
        for column, right_word in enumerate(right, start=1):
            current.append(
                min(
                    previous[column - 1] + (left_word != right_word),
                    previous[column] + 1,
                    current[column - 1] + 1,
                )
            )
        previous = current
    return previous[-1] / len(left)


def _median_filter(volume: Any, kernel_size: Any = None) -> np.ndarray:
    array = np.asarray(volume)
    if kernel_size is None:
        sizes = (3,) * array.ndim
    elif isinstance(kernel_size, int):
        sizes = (kernel_size,) * array.ndim
    else:
        sizes = tuple(int(value) for value in kernel_size)
    if any(value <= 0 or value % 2 == 0 for value in sizes):
        raise ValueError("Median filter sizes must be positive odd integers.")
    padded = np.pad(
        array,
        tuple((value // 2, value // 2) for value in sizes),
        mode="constant",
    )
    windows = np.lib.stride_tricks.sliding_window_view(padded, sizes)
    axes = tuple(range(array.ndim, windows.ndim))
    return np.median(windows, axis=axes).astype(array.dtype, copy=False)


def load_whisper() -> Any:
    if metadata.version("mlx-whisper") != WHISPER_VERSION:
        raise RuntimeError(f"mlx-whisper=={WHISPER_VERSION} is required.")
    prior = {
        name: module
        for name, module in list(sys.modules.items())
        if name == "scipy" or name.startswith("scipy.")
    }
    for name in prior:
        sys.modules.pop(name, None)
    scipy = types.ModuleType("scipy")
    signal = types.ModuleType("scipy.signal")
    signal.medfilt = _median_filter
    scipy.signal = signal
    scipy.__version__ = "1.15.3"
    sys.modules["scipy"] = scipy
    sys.modules["scipy.signal"] = signal
    try:
        return importlib.import_module("mlx_whisper")
    finally:
        for name in list(sys.modules):
            if name == "scipy" or name.startswith("scipy."):
                sys.modules.pop(name, None)
        sys.modules.update(prior)


def reference_path(evidence_root: Path, sample: dict[str, Any]) -> Path:
    value = sample["reference"].get("conditioning_file")
    if not value:
        raise ValueError(f"Reference file is missing for {sample['sample_id']}")
    path = (evidence_root / "references" / value).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def speaker_embedding(model: Any, evidence_root: Path, path: Path) -> np.ndarray:
    prepared = prepared_reference_wav(evidence_root, path, sample_rate=24000)
    audio, sample_rate = sf.read(prepared, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)
    embedding = np.asarray(
        model.extract_speaker_embedding(mx.array(audio), sr=int(sample_rate))
    ).reshape(-1)
    return embedding / (np.linalg.norm(embedding) + 1e-12)


def audio_diagnostics(path: Path) -> dict[str, Any]:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = audio.mean(axis=1)
    onset_count = min(len(mono), max(1, int(sample_rate * 0.10)))
    tail_count = min(len(mono), max(1, int(sample_rate * 0.10)))
    onset = mono[:onset_count]
    tail = mono[-tail_count:]
    non_silent = np.flatnonzero(np.abs(mono) > 0.002)
    leading_silence = (
        float(non_silent[0]) / sample_rate if len(non_silent) else len(mono) / sample_rate
    )
    trailing_silence = (
        float(len(mono) - 1 - non_silent[-1]) / sample_rate
        if len(non_silent)
        else len(mono) / sample_rate
    )
    return {
        "duration_seconds": len(mono) / int(sample_rate),
        "sample_rate": int(sample_rate),
        "onset_peak": float(np.max(np.abs(onset))) if len(onset) else 0.0,
        "onset_rms": float(np.sqrt(np.mean(onset * onset))) if len(onset) else 0.0,
        "tail_peak": float(np.max(np.abs(tail))) if len(tail) else 0.0,
        "leading_silence_seconds": leading_silence,
        "trailing_silence_seconds": trailing_silence,
        "empty_or_nearly_silent": bool(
            not len(mono) or float(np.max(np.abs(mono))) < 0.005
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--group", required=True)
    parser.add_argument("--model", action="append")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    disable_optional_sklearn()
    evidence_root = Path(args.evidence_root).expanduser().resolve()
    manifest = json.loads(
        (evidence_root / "round1_internal_manifest.json").read_text(encoding="utf-8")
    )
    samples = [
        item
        for item in manifest["sample_specs"]
        if item["group"] == args.group
        and (not args.model or item["model_key"] in set(args.model))
        and (evidence_root / item["output_file"]).is_file()
        and (evidence_root / item["result_file"]).is_file()
    ]
    if not samples:
        raise RuntimeError(f"No generated samples found for group {args.group!r}.")

    output_dir = evidence_root / "objective"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{args.group}.json"
    existing = {}
    if output_path.is_file() and not args.force:
        existing_payload = json.loads(output_path.read_text(encoding="utf-8"))
        existing = existing_payload.get("measurements") or {}

    whisper = load_whisper()
    whisper_snapshot = exact_snapshot(WHISPER_REPO, WHISPER_REVISION)
    speaker_model, speaker_snapshot = load_model(
        "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
        "e7dd0585652209fa0d7783659aad4e8a324de11c",
    )
    reference_embeddings: dict[str, np.ndarray] = {}
    measurements: dict[str, Any] = dict(existing)

    for index, sample in enumerate(samples, start=1):
        receipt = json.loads(
            (evidence_root / sample["result_file"]).read_text(encoding="utf-8")
        )
        output = (evidence_root / sample["output_file"]).resolve()
        current_audio_sha = sha256_file(output)
        prior = measurements.get(sample["sample_id"])
        if (
            not args.force
            and prior
            and prior.get("audio_sha256") == current_audio_sha
            and prior.get("target_text_sha256") == sample["target_text_sha256"]
        ):
            continue

        transcript_result = whisper.transcribe(
            str(output),
            path_or_hf_repo=str(whisper_snapshot),
            language="en",
            word_timestamps=False,
            condition_on_previous_text=False,
            verbose=False,
        )
        transcript = str(transcript_result.get("text") or "").strip()
        ref = reference_path(evidence_root, sample)
        reference_hash = sha256_file(ref)
        if reference_hash not in reference_embeddings:
            reference_embeddings[reference_hash] = speaker_embedding(
                speaker_model, evidence_root, ref
            )
        output_embedding = speaker_embedding(speaker_model, evidence_root, output)
        measurements[sample["sample_id"]] = {
            "sample_id": sample["sample_id"],
            "blind_id": sample["blind_id"],
            "model_key": sample["model_key"],
            "identity_key": sample["identity_key"],
            "style": sample["style"],
            "audio_file": sample["output_file"],
            "audio_sha256": current_audio_sha,
            "target_text_sha256": sample["target_text_sha256"],
            "automatic_transcript": transcript,
            "automatic_transcript_sha256": sha256_text(transcript),
            "word_error_rate": word_error_rate(sample["target_text"], transcript),
            "speaker_reference_sha256": reference_hash,
            "speaker_cosine_to_expected_identity": float(
                np.dot(reference_embeddings[reference_hash], output_embedding)
            ),
            "audio_diagnostics": audio_diagnostics(output),
            "generation_receipt_sha256": sha256_file(
                evidence_root / sample["result_file"]
            ),
            "sample_fingerprint": receipt["sample_fingerprint"],
        }
        print(
            json.dumps(
                {
                    "index": index,
                    "count": len(samples),
                    "sample_id": sample["sample_id"],
                    "wer": measurements[sample["sample_id"]]["word_error_rate"],
                    "cosine": measurements[sample["sample_id"]][
                        "speaker_cosine_to_expected_identity"
                    ],
                }
            ),
            flush=True,
        )

    selected = [measurements[item["sample_id"]] for item in samples]
    wers = [float(item["word_error_rate"]) for item in selected]
    cosines = [float(item["speaker_cosine_to_expected_identity"]) for item in selected]
    payload = {
        "schema_version": 1,
        "round_id": manifest["round_id"],
        "group": args.group,
        "model_filter": args.model,
        "sample_count": len(selected),
        "perfect_transcript_count": sum(value == 0.0 for value in wers),
        "nonzero_wer_count": sum(value > 0.0 for value in wers),
        "max_word_error_rate": max(wers),
        "speaker_cosine_range": [min(cosines), max(cosines)],
        "whisper": {
            "repo": WHISPER_REPO,
            "revision": WHISPER_REVISION,
            "runtime": f"mlx-whisper=={WHISPER_VERSION}",
            "snapshot": str(whisper_snapshot),
        },
        "speaker_evaluator": {
            "repo": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
            "revision": "e7dd0585652209fa0d7783659aad4e8a324de11c",
            "snapshot": str(speaker_snapshot),
            "reference_group_count": len(reference_embeddings),
        },
        "measurements": measurements,
        "manual_blinded_review_required": True,
        "production_promotion_allowed": False,
    }
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output_path),
                "sample_count": payload["sample_count"],
                "perfect_transcript_count": payload["perfect_transcript_count"],
                "speaker_cosine_range": payload["speaker_cosine_range"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
