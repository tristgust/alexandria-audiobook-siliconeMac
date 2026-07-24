#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf

from prepare_narrator_indextts2_reference_bank import sha256_file, text_similarity
from prepare_same_speaker_performance_validation import (
    ROUND_ID,
    SameSpeakerError,
    acoustic_metrics,
    fingerprint,
    normalize_text,
)

ALPHAS = (0.15, 0.35, 0.55)
SPECS = (
    {
        "target_key": "doctor",
        "target_label": "Doctor",
        "mode": "protective_authority",
        "mode_label": "Protective authority",
        "target_text": "Stay behind me. Whatever happens, do not let go of my hand.",
        "reference_text": "I'm the Doctor, and I take care of my friends.",
        "source_clips": ("sample_0208.wav",),
    },
    {
        "target_key": "doctor",
        "target_label": "Doctor",
        "mode": "dark_warning",
        "mode_label": "Dark warning",
        "target_text": "You will leave them alone. That is not a request.",
        "reference_text": "You're not a man, not a human being. I'm a complex space-time event. I am Lord President of Gallifrey, the traveller from beyond time.",
        "source_clips": ("sample_0204.wav", "sample_0205.wav", "sample_0206.wav", "sample_0207.wav"),
    },
)


def concatenate(clips: list[Path], output: Path) -> None:
    pieces: list[np.ndarray] = []
    sample_rate = 24000
    silence = np.zeros(int(sample_rate * 0.12), dtype=np.float32)
    for index, path in enumerate(clips):
        audio, rate = sf.read(path, dtype="float32", always_2d=True)
        mono = np.mean(audio, axis=1, dtype=np.float32)
        if rate != sample_rate:
            raise SameSpeakerError(f"Unexpected Doctor sample rate {rate}: {path}")
        if index:
            pieces.append(silence)
        pieces.append(mono)
    merged = np.concatenate(pieces)
    peak = float(np.max(np.abs(merged)))
    if peak > 0:
        merged *= min(1.0, 0.70 / peak)
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, merged, sample_rate, subtype="PCM_16")


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    doctor_bank = Path(args.doctor_bank_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    canonical = doctor_bank / "banks" / "doctor_core_identity.wav"
    if not canonical.is_file():
        raise SameSpeakerError(f"Doctor identity bank is missing: {canonical}")
    reference_root = output_root / "references"
    rows = []
    for spec in SPECS:
        sources = [doctor_bank / "clips" / name for name in spec["source_clips"]]
        for source in sources:
            if not source.is_file():
                raise SameSpeakerError(f"Doctor source clip is missing: {source}")
        reference = reference_root / f"doctor-{spec['mode']}.wav"
        concatenate(sources, reference)
        rows.append(
            {
                **spec,
                "source_audio": [str(path) for path in sources],
                "source_audio_sha256": [sha256_file(path) for path in sources],
                "reference_audio": str(reference),
                "reference_audio_sha256": sha256_file(reference),
                "speaker_audio": str(reference),
                "speaker_audio_sha256": sha256_file(reference),
                "canonical_identity_audio": str(canonical),
                "canonical_identity_sha256": sha256_file(canonical),
            }
        )
    matrix = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "alphas": list(ALPHAS),
        "reference_count": len(rows),
        "rows": rows,
        "production_promotion_allowed": False,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / "matrix.json"
    path.write_text(json.dumps(matrix, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"reference_count": len(rows), "matrix": str(path)}


def analyze(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    whisper_model = Path(args.whisper_model).expanduser().resolve()
    if not whisper_model.is_dir():
        raise SameSpeakerError(f"Whisper model is missing: {whisper_model}")
    summary = json.loads((output_root / "generation-summary.json").read_text(encoding="utf-8"))
    import mlx_whisper

    analyzed = []
    for row in summary["samples"]:
        result = mlx_whisper.transcribe(
            row["audio_path"],
            path_or_hf_repo=str(whisper_model),
            language="en",
            word_timestamps=False,
            condition_on_previous_text=False,
            verbose=False,
        )
        transcript = str(result.get("text") or "").strip()
        similarity = text_similarity(row["target_text"], transcript)
        expected = normalize_text(row["target_text"]).split()
        actual = normalize_text(transcript).split()
        final_word = bool(expected and actual and expected[-1] == actual[-1])
        technical_pass = (
            similarity >= 0.92
            and final_word
            and row["style_reference_cosine"] >= 0.75
            and row["canonical_identity_cosine"] >= 0.66
            and row["acoustic_match"] >= 0.50
            and not row["acoustic_metrics"]["pitch_trajectory_anomaly"]
            and float(row["acoustic_metrics"]["clipping_fraction"]) < 0.001
        )
        score = (
            row["technical_score_without_asr"]
            + similarity * 3.0
            + (0.75 if technical_pass else -0.75)
        )
        analyzed.append(
            {
                **row,
                "automatic_transcript": transcript,
                "text_similarity": round(similarity, 6),
                "final_word_matches": final_word,
                "technical_pass": technical_pass,
                "selection_score": round(score, 6),
            }
        )
    winners = []
    excluded = []
    for spec in SPECS:
        candidates = [row for row in analyzed if row["mode"] == spec["mode"]]
        passing = [row for row in candidates if row["technical_pass"]]
        if passing:
            winners.append(max(passing, key=lambda row: row["selection_score"]))
        else:
            excluded.append(
                {
                    "mode": spec["mode"],
                    "reason": "no_candidate_passed_automatic_gate",
                    "best": max(candidates, key=lambda row: row["selection_score"]),
                }
            )
    (output_root / "answer-key.json").write_text(
        json.dumps(winners, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_root / "analysis.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "round_id": ROUND_ID,
                "sample_count": len(analyzed),
                "technical_pass_count": sum(row["technical_pass"] for row in analyzed),
                "winner_count": len(winners),
                "excluded": excluded,
                "samples": analyzed,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return {"winner_count": len(winners), "excluded_count": len(excluded)}


def validate(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    winners = json.loads((output_root / "answer-key.json").read_text(encoding="utf-8"))
    missing = []
    bad_hash = []
    for row in winners:
        path = Path(row["audio_path"])
        if not path.is_file():
            missing.append(row["sample_id"])
        elif sha256_file(path) != row["audio_sha256"]:
            bad_hash.append(row["sample_id"])
    if missing or bad_hash:
        raise SameSpeakerError(f"Doctor salvage validation failed: missing={missing}, bad_hash={bad_hash}")
    return {
        "winner_count": len(winners),
        "missing_count": len(missing),
        "bad_hash_count": len(bad_hash),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a compact Doctor same-speaker salvage matrix.")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--doctor-bank-root", required=True)
    prepare_parser.add_argument("--output-root", required=True)
    analyze_parser = sub.add_parser("analyze")
    analyze_parser.add_argument("--output-root", required=True)
    analyze_parser.add_argument("--whisper-model", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--output-root", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "prepare":
            result = prepare(args)
        elif args.command == "analyze":
            result = analyze(args)
        else:
            result = validate(args)
    except SameSpeakerError as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
