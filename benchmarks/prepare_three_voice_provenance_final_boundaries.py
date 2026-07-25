#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf

BENCHMARKS_ROOT = Path(__file__).resolve().parent
if str(BENCHMARKS_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS_ROOT))

from prepare_three_voice_provenance_followups import (
    ProvenanceFollowupError,
    encode_mp3,
    evaluate,
    load_json,
    sha256_file,
)

PREVIOUS_MANIFEST_ROUND_ID = "alexandria_three_voice_provenance_followups_v1"
SOURCE_REVIEW_ROUND_ID = "alexandria_three_voice_provenance_followups_review_v1"
FINAL_MANIFEST_ROUND_ID = "alexandria_three_voice_provenance_final_boundaries_v1"
FINAL_REVIEW_ROUND_ID = "alexandria_three_voice_provenance_final_boundaries_review_v1"

BOUNDARY_CARD_ID = "boundary:benny_hesitation_fatalistic_dread"
SEPARATION_CARD_ID = "separation:doctor_acf_dismissive_contempt"
BENNY_END_POLICY = "preserve_full_hash_verified_source_tail_without_fade"
DOCTOR_END_SECONDS = 4.34
DOCTOR_FADE_SECONDS = 0.008

SALVAGE_ASSET_ROOT = Path(__file__).with_name("three_voice_final_salvage_assets")
RANGE_SERVER = Path(__file__).with_name("range_http_server.py")


class FinalBoundaryError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def rows_by_id(rows: Any, *, key: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        raise FinalBoundaryError(f"{label} must be a list.")
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise FinalBoundaryError(f"Every {label} row must be an object.")
        value = row.get(key)
        if not isinstance(value, str) or not value:
            raise FinalBoundaryError(f"Every {label} row requires {key}.")
        if value in indexed:
            raise FinalBoundaryError(f"Duplicate {label} {key}: {value}")
        indexed[value] = row
    return indexed


def unwrap_review(payload: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(payload, dict):
        raise FinalBoundaryError("Normalized review export must be an object.")
    review = payload.get("review")
    upload = payload.get("source_upload")
    if not isinstance(review, dict) or not isinstance(upload, dict):
        raise FinalBoundaryError("Normalized review export requires review and source_upload.")
    if not re.fullmatch(r"[0-9a-f]{64}", str(upload.get("sha256") or "")):
        raise FinalBoundaryError("Normalized review export has an invalid upload hash.")
    return review, upload


def validate_partial_review(payload: Any, previous_manifest: dict[str, Any]) -> dict[str, Any]:
    review, upload = unwrap_review(payload)
    if review.get("round_id") != SOURCE_REVIEW_ROUND_ID:
        raise FinalBoundaryError(f"Unexpected review round_id: {review.get('round_id')}")
    if previous_manifest.get("round_id") != PREVIOUS_MANIFEST_ROUND_ID:
        raise FinalBoundaryError(f"Unexpected previous manifest round_id: {previous_manifest.get('round_id')}")

    review_rows = rows_by_id(review.get("rows"), key="card_id", label="review")
    manifest_rows = rows_by_id(previous_manifest.get("rows"), key="card_id", label="previous manifest")
    expected = {BOUNDARY_CARD_ID, SEPARATION_CARD_ID}
    if set(review_rows) != expected or set(manifest_rows) != expected:
        raise FinalBoundaryError(
            f"Card set mismatch: review={sorted(review_rows)}, manifest={sorted(manifest_rows)}"
        )

    for card_id in sorted(expected):
        reviewed = review_rows[card_id]
        source = manifest_rows[card_id]
        for key in ("card_type", "clip_id", "target", "target_label", "selected_transcript", "primary_emotion"):
            if reviewed.get(key) != source.get(key):
                raise FinalBoundaryError(
                    f"Review changed stable field {key} for {card_id}: "
                    f"{reviewed.get(key)!r} != {source.get(key)!r}"
                )

    boundary = review_rows[BOUNDARY_CARD_ID]
    if boundary.get("decision") != "still_wrong":
        raise FinalBoundaryError("Benny boundary must be explicitly marked still_wrong.")
    if "inevitable" not in str(boundary.get("notes") or "").casefold():
        raise FinalBoundaryError("Benny boundary note must identify the final word problem.")

    doctor = review_rows[SEPARATION_CARD_ID]
    if doctor.get("decision") not in (None, ""):
        raise FinalBoundaryError("Incomplete Doctor card must not be interpreted as a candidate selection.")
    doctor_note = str(doctor.get("notes") or "").casefold()
    if not doctor_note or not any(token in doctor_note for token in ("end", "talking", "someone else")):
        raise FinalBoundaryError("Doctor card requires a tail-contamination note.")

    summary = review.get("summary")
    expected_summary = {
        "card_count": 2,
        "complete_count": 1,
        "separation_selected_count": 0,
        "separation_none_count": 0,
        "boundary_approved_count": 0,
        "boundary_wrong_count": 1,
    }
    if summary != expected_summary:
        raise FinalBoundaryError(f"Unexpected review summary: {summary!r}")

    return {
        "review": review,
        "upload": upload,
        "review_rows": review_rows,
        "manifest_rows": manifest_rows,
    }


def validate_audio(path: Path, expected_hash: str | None = None) -> Path:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FinalBoundaryError(f"Audio is missing: {path}")
    if expected_hash is not None and sha256_file(path) != expected_hash:
        raise FinalBoundaryError(f"Audio hash mismatch: {path}")
    return path


def normalize_no_tail_fade(source: Path, output: Path, *, start_seconds: float) -> None:
    audio, sample_rate = sf.read(source, dtype="float32", always_2d=True)
    if audio.size == 0:
        raise FinalBoundaryError(f"Audio is empty: {source}")
    mono = audio.mean(axis=1, dtype=np.float32)
    start_frame = max(0, int(round(start_seconds * sample_rate)))
    mono = mono[start_frame:]
    if mono.size == 0:
        raise FinalBoundaryError("Benny final boundary produced empty audio.")
    peak = float(np.max(np.abs(mono)))
    if peak > 0.86:
        mono *= 0.86 / peak
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, mono, sample_rate, subtype="PCM_16")


def trim_doctor_candidate(source: Path, output: Path) -> None:
    audio, sample_rate = sf.read(source, dtype="float32", always_2d=True)
    if audio.size == 0:
        raise FinalBoundaryError(f"Audio is empty: {source}")
    mono = audio.mean(axis=1, dtype=np.float32)
    end_frame = min(len(mono), int(round(DOCTOR_END_SECONDS * sample_rate)))
    mono = mono[:end_frame]
    fade_frames = min(len(mono), max(1, int(round(DOCTOR_FADE_SECONDS * sample_rate))))
    mono[-fade_frames:] *= np.linspace(1.0, 0.0, fade_frames, dtype=np.float32)
    peak = float(np.max(np.abs(mono)))
    if peak > 0.86:
        mono *= 0.86 / peak
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, mono, 24000, subtype="PCM_16")


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    previous_manifest_path = Path(args.previous_manifest).expanduser().resolve()
    normalized_review_path = Path(args.review).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    whisper_model = Path(args.whisper_model).expanduser().resolve()
    if not whisper_model.is_dir():
        raise FinalBoundaryError(f"Whisper model is missing: {whisper_model}")

    previous_manifest = load_json(previous_manifest_path)
    normalized_review = load_json(normalized_review_path)
    validated = validate_partial_review(normalized_review, previous_manifest)
    source_rows = validated["manifest_rows"]
    review_rows = validated["review_rows"]

    if output_root.exists() and args.force:
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    boundary_source = source_rows[BOUNDARY_CARD_ID]
    original_benny = validate_audio(
        Path(str(boundary_source.get("previous_audio_path") or "")),
        str(boundary_source.get("previous_audio_sha256") or ""),
    )
    failed_benny = validate_audio(
        Path(str(boundary_source.get("final", {}).get("audio_path") or "")),
        str(boundary_source.get("final", {}).get("audio_sha256") or ""),
    )
    original_duration = float(sf.info(original_benny).duration)
    start_seconds = 0.10
    final_benny = output_root / "boundary" / "benny_hesitation_fatalistic_dread.wav"
    normalize_no_tail_fade(original_benny, final_benny, start_seconds=start_seconds)
    benny_evaluation = evaluate(
        final_benny,
        boundary_source["selected_transcript"],
        whisper_model,
        floor=0.92,
    )

    doctor_source = source_rows[SEPARATION_CARD_ID]
    original_doctor = validate_audio(
        Path(str(doctor_source.get("original", {}).get("audio_path") or "")),
        str(doctor_source.get("original", {}).get("audio_sha256") or ""),
    )
    trimmed_original = output_root / "separation" / "doctor_acf_dismissive_contempt" / "original.wav"
    trim_doctor_candidate(original_doctor, trimmed_original)
    doctor_candidates: list[dict[str, Any]] = []
    for ordinal, candidate in enumerate(doctor_source.get("candidates") or [], start=1):
        source = validate_audio(
            Path(str(candidate.get("audio_path") or "")),
            str(candidate.get("audio_sha256") or ""),
        )
        destination = trimmed_original.parent / f"candidate_{ordinal}.wav"
        trim_doctor_candidate(source, destination)
        doctor_candidates.append(
            {
                "candidate_label": candidate["candidate_label"],
                "model_key": candidate["model_key"],
                "model_filename": candidate["model_filename"],
                "source_audio_sha256": sha256_file(source),
                "trim_end_seconds": DOCTOR_END_SECONDS,
                "fade_out_seconds": DOCTOR_FADE_SECONDS,
                **evaluate(destination, doctor_source["selected_transcript"], whisper_model, floor=0.75),
            }
        )

    rows = [
        {
            "card_id": BOUNDARY_CARD_ID,
            "card_type": "boundary_final",
            "clip_id": boundary_source["clip_id"],
            "target": boundary_source["target"],
            "target_label": boundary_source["target_label"],
            "source_title": boundary_source["source_title"],
            "selected_transcript": boundary_source["selected_transcript"],
            "primary_emotion": boundary_source["primary_emotion"],
            "dramatic_function": boundary_source["dramatic_function"],
            "review_notes": review_rows[BOUNDARY_CARD_ID].get("notes"),
            "boundary_reason": (
                "The previous recut audibly shortened the release of 'inevitable'. This final cut "
                "starts 100 ms into the hash-verified source clip, preserves its complete remaining "
                "tail, and applies no fade-out."
            ),
            "source_recovery_method": "hash_verified_reviewed_clip_preserve_full_tail",
            "source_duration_seconds": round(original_duration, 6),
            "relative_start_seconds": start_seconds,
            "end_policy": BENNY_END_POLICY,
            "previous_audio_path": str(failed_benny),
            "previous_audio_sha256": sha256_file(failed_benny),
            "final": benny_evaluation,
            "automatic_production_assignment": False,
            "production_promotion_allowed": False,
        },
        {
            "card_id": SEPARATION_CARD_ID,
            "card_type": "source_separation",
            "clip_id": doctor_source["clip_id"],
            "target": doctor_source["target"],
            "target_label": doctor_source["target_label"],
            "source_title": doctor_source["source_title"],
            "selected_transcript": doctor_source["selected_transcript"],
            "primary_emotion": doctor_source["primary_emotion"],
            "dramatic_function": doctor_source["dramatic_function"],
            "review_notes": review_rows[SEPARATION_CARD_ID].get("notes"),
            "tail_policy": {
                "target_word_end_seconds": 4.30,
                "trim_end_seconds": DOCTOR_END_SECONDS,
                "fade_out_seconds": DOCTOR_FADE_SECONDS,
                "reason": "Remove the following speaker while retaining a short release after 'it'.",
            },
            "original": evaluate(
                trimmed_original,
                doctor_source["selected_transcript"],
                whisper_model,
                floor=0.70,
            ),
            "candidates": doctor_candidates,
            "automatic_production_assignment": False,
            "production_promotion_allowed": False,
        },
    ]

    payload = {
        "schema_version": 1,
        "round_id": FINAL_MANIFEST_ROUND_ID,
        "created_at": now_iso(),
        "source_previous_manifest": str(previous_manifest_path),
        "source_previous_manifest_sha256": sha256_file(previous_manifest_path),
        "source_review_export": str(normalized_review_path),
        "source_review_export_sha256": sha256_file(normalized_review_path),
        "source_upload_sha256": validated["upload"]["sha256"],
        "card_count": 2,
        "card_type_counts": dict(sorted(Counter(row["card_type"] for row in rows).items())),
        "rows": rows,
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    path = output_root / "final-boundary-manifest.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "card_count": 2,
        "card_type_counts": payload["card_type_counts"],
        "manifest": str(path),
    }


def validate_manifest(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("round_id") != FINAL_MANIFEST_ROUND_ID:
        raise FinalBoundaryError("Final boundary manifest has an unexpected round_id.")
    rows = payload.get("rows")
    if not isinstance(rows, list) or len(rows) != 2:
        raise FinalBoundaryError("Final boundary manifest must contain two cards.")
    indexed = rows_by_id(rows, key="card_id", label="final boundary manifest")
    if set(indexed) != {BOUNDARY_CARD_ID, SEPARATION_CARD_ID}:
        raise FinalBoundaryError("Final boundary manifest card set is invalid.")
    failures: list[str] = []
    for row in rows:
        if row.get("production_promotion_allowed") is not False:
            failures.append(f"promotion:{row.get('card_id')}")
        if row["card_type"] == "boundary_final":
            paths = [row.get("previous_audio_path"), row.get("final", {}).get("audio_path")]
            if row.get("end_policy") != BENNY_END_POLICY:
                failures.append("benny_end_policy")
        else:
            paths = [row.get("original", {}).get("audio_path")] + [
                item.get("audio_path") for item in row.get("candidates") or []
            ]
            if len(row.get("candidates") or []) != 3:
                failures.append("doctor_candidate_count")
            if float(row.get("tail_policy", {}).get("trim_end_seconds") or 0) != DOCTOR_END_SECONDS:
                failures.append("doctor_trim_end")
        for value in paths:
            path = Path(str(value or ""))
            if not path.is_file():
                failures.append(f"missing:{row.get('card_id')}:{path}")
    if payload.get("automatic_production_assignment") is not False:
        failures.append("automatic_production_assignment")
    if payload.get("production_promotion_allowed") is not False:
        failures.append("production_promotion_allowed")
    if failures:
        raise FinalBoundaryError(f"Final boundary manifest validation failed: {failures}")
    return {
        "card_count": len(rows),
        "card_type_counts": dict(sorted(Counter(row["card_type"] for row in rows).items())),
        "failure_count": 0,
    }


def package(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = Path(args.manifest).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    payload = load_json(manifest_path)
    validate_manifest(payload)
    review = output_root / "review"
    if review.exists():
        shutil.rmtree(review)
    (review / "audio").mkdir(parents=True)
    for name in ("index.html", "styles.css", "app.js"):
        shutil.copy2(SALVAGE_ASSET_ROOT / name, review / name)
    shutil.copy2(RANGE_SERVER, review / "serve_review.py")

    index_path = review / "index.html"
    index = index_path.read_text(encoding="utf-8")
    index = index.replace("Final Three-Voice Salvage Gate", "Final Two-Clip Boundary Gate")
    index = index.replace(
        "Four cards test real vocal-source separation. Five cards verify the final Narrator boundaries. Nothing here enters production automatically.",
        "One card verifies Benny's complete final word. One card compares the three Doctor cleanup candidates after removing the contaminating tail. Nothing enters production automatically.",
    )
    index_path.write_text(index, encoding="utf-8")

    app_path = review / "app.js"
    app = app_path.read_text(encoding="utf-8")
    app = app.replace("alexandria:three-voice-final-salvage:", "alexandria:three-voice-final-boundaries:")
    app = app.replace(
        "alexandria_three_voice_final_salvage_review.json",
        "alexandria_three_voice_provenance_final_boundaries_review.json",
    )
    app_path.write_text(app, encoding="utf-8")

    public_rows: list[dict[str, Any]] = []
    answer_rows: list[dict[str, Any]] = []
    for ordinal, row in enumerate(payload["rows"], start=1):
        common = {
            "card_id": row["card_id"],
            "ordinal": ordinal,
            "card_type": row["card_type"],
            "clip_id": row["clip_id"],
            "target": row["target"],
            "target_label": row["target_label"],
            "source_title": row["source_title"],
            "selected_transcript": row["selected_transcript"],
            "primary_emotion": row["primary_emotion"],
            "dramatic_function": row["dramatic_function"],
            "review_notes": row.get("review_notes"),
        }
        if row["card_type"] == "boundary_final":
            previous_mp3 = review / "audio" / "benny_previous.mp3"
            final_mp3 = review / "audio" / "benny_final.mp3"
            encode_mp3(Path(row["previous_audio_path"]), previous_mp3)
            encode_mp3(Path(row["final"]["audio_path"]), final_mp3)
            public_rows.append(
                {
                    **common,
                    "boundary_reason": row["boundary_reason"],
                    "previous_audio": f"audio/{previous_mp3.name}",
                    "final_audio": f"audio/{final_mp3.name}",
                    "technical_pass": row["final"]["technical_pass"],
                    "verification_similarity": row["final"]["verification_similarity"],
                }
            )
        else:
            original_mp3 = review / "audio" / "doctor_original_trimmed.mp3"
            encode_mp3(Path(row["original"]["audio_path"]), original_mp3)
            candidates: list[dict[str, Any]] = []
            for candidate in row["candidates"]:
                destination = review / "audio" / f"doctor_{candidate['candidate_label']}.mp3"
                encode_mp3(Path(candidate["audio_path"]), destination)
                candidates.append(
                    {
                        "candidate_label": candidate["candidate_label"],
                        "audio": f"audio/{destination.name}",
                        "technical_pass": candidate["technical_pass"],
                        "verification_similarity": candidate["verification_similarity"],
                    }
                )
            public_rows.append(
                {
                    **common,
                    "original_audio": f"audio/{original_mp3.name}",
                    "candidates": candidates,
                }
            )
        answer_rows.append(row)

    public = {
        "schema_version": 1,
        "round_id": FINAL_REVIEW_ROUND_ID,
        "title": "Final Two-Clip Boundary Gate",
        "card_count": 2,
        "card_type_counts": payload["card_type_counts"],
        "rows": public_rows,
    }
    (review / "data.js").write_text(
        "window.THREE_VOICE_FINAL_SALVAGE_DATA = " + json.dumps(public, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "round_id": FINAL_REVIEW_ROUND_ID,
        "card_count": 2,
        "card_type_counts": payload["card_type_counts"],
        "model_names_blinded": True,
        "maximum_simultaneous_audio_elements": 4,
        "range_server_included": True,
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    (review / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (output_root / "answer-key.json").write_text(
        json.dumps(answer_rows, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output_root / "START_HERE.txt").write_text(
        f'cd "{review}"\npython3 serve_review.py --bind 127.0.0.1 --port 8794\n\nThen open http://127.0.0.1:8794/\n',
        encoding="utf-8",
    )
    return {"card_count": 2, "review": str(review / "index.html")}


def validate_package(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    review = output_root / "review"
    prefix = "window.THREE_VOICE_FINAL_SALVAGE_DATA = "
    text = (review / "data.js").read_text(encoding="utf-8").strip()
    if not text.startswith(prefix):
        raise FinalBoundaryError("Review data.js prefix is invalid.")
    data = json.loads(text[len(prefix):].rstrip(";"))
    failures: list[str] = []
    if data.get("round_id") != FINAL_REVIEW_ROUND_ID or data.get("card_count") != 2:
        failures.append("review_contract")
    for row in data.get("rows") or []:
        if row["card_type"] == "boundary_final":
            paths = [row["previous_audio"], row["final_audio"]]
        else:
            paths = [row["original_audio"]] + [item["audio"] for item in row["candidates"]]
        for relative in paths:
            path = review / relative
            if not path.is_file():
                failures.append(f"missing:{row['card_id']}:{relative}")
    if failures:
        raise FinalBoundaryError(f"Final review package validation failed: {failures}")
    return {"card_count": len(data["rows"]), "failure_count": 0, "review": str(review / "index.html")}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare the final two surgical endpoint checks.")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--previous-manifest", required=True)
    prepare_parser.add_argument("--review", required=True)
    prepare_parser.add_argument("--output-root", required=True)
    prepare_parser.add_argument("--whisper-model", required=True)
    prepare_parser.add_argument("--force", action="store_true")
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--manifest", required=True)
    package_parser = sub.add_parser("package")
    package_parser.add_argument("--manifest", required=True)
    package_parser.add_argument("--output-root", required=True)
    package_validate_parser = sub.add_parser("validate-package")
    package_validate_parser.add_argument("--output-root", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "prepare":
            result = prepare(args)
        elif args.command == "validate":
            result = validate_manifest(load_json(Path(args.manifest).expanduser().resolve()))
        elif args.command == "package":
            result = package(args)
        else:
            result = validate_package(args)
    except (
        FinalBoundaryError,
        ProvenanceFollowupError,
        subprocess.CalledProcessError,
        json.JSONDecodeError,
        OSError,
    ) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
