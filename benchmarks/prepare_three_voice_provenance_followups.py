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

from apply_three_voice_selected_refinement_review import load_json, sha256_file
from prepare_three_voice_final_salvage import (
    ASSET_ROOT as SALVAGE_ASSET_ROOT,
    RANGE_SERVER,
    SEPARATION_MODELS,
    audio_metrics,
    encode_mp3,
    extract_boundary,
    transcript_similarity,
)

APPLIED_ROUND_ID = "alexandria_three_voice_historical_provenance_review_applied_v1"
FOLLOWUP_ROUND_ID = "alexandria_three_voice_provenance_followups_v1"
REVIEW_ROUND_ID = "alexandria_three_voice_provenance_followups_review_v1"

BOUNDARY_CLIP_ID = "benny_hesitation_fatalistic_dread"
CLEANUP_CLIP_ID = "doctor_acf_dismissive_contempt"


class ProvenanceFollowupError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise ProvenanceFollowupError(
            result.stderr.strip() or result.stdout.strip() or f"Command failed: {command}"
        )


def transcribe(path: Path, whisper_model: Path) -> str:
    import mlx_whisper

    result = mlx_whisper.transcribe(
        str(path),
        path_or_hf_repo=str(whisper_model),
        language="en",
        condition_on_previous_text=False,
        word_timestamps=False,
        verbose=False,
    )
    return str(result.get("text") or "").strip()


def evaluate(path: Path, expected: str, whisper_model: Path, floor: float = 0.72) -> dict[str, Any]:
    observed = transcribe(path, whisper_model)
    metrics = audio_metrics(path)
    similarity = transcript_similarity(expected, observed)
    return {
        "audio_path": str(path),
        "audio_sha256": sha256_file(path),
        "verification_transcript": observed,
        "verification_similarity": round(similarity, 6),
        "metrics": metrics,
        "technical_pass": (
            similarity >= floor
            and metrics["clipping_sample_count"] == 0
            and metrics["peak"] <= 0.99
        ),
    }


def normalize_candidate(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.wav")
    run([
        "ffmpeg", "-v", "error", "-y", "-i", str(source),
        "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(temporary),
    ])
    audio, sample_rate = sf.read(temporary, dtype="float32", always_2d=True)
    temporary.unlink(missing_ok=True)
    mono = audio.mean(axis=1, dtype=np.float32)
    if mono.size == 0:
        raise ProvenanceFollowupError(f"Separated audio is empty: {source}")
    peak = float(np.max(np.abs(mono)))
    if peak > 0.86:
        mono *= 0.86 / peak
    fade = min(int(sample_rate * 0.04), mono.size // 8)
    if fade > 0:
        mono[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)
        mono[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    sf.write(output, mono, sample_rate, subtype="PCM_16")


def find_vocal_output(root: Path) -> Path:
    candidates = sorted(
        path for path in root.rglob("*.wav")
        if "vocal" in path.name.casefold() or "vocals" in path.name.casefold()
    )
    if len(candidates) != 1:
        raise ProvenanceFollowupError(
            f"Expected exactly one vocal output in {root}; found {[str(path) for path in candidates]}"
        )
    return candidates[0]


def blind_order(clip_id: str) -> list[str]:
    return sorted(
        SEPARATION_MODELS,
        key=lambda key: hashlib.sha256(f"{FOLLOWUP_ROUND_ID}:{clip_id}:{key}".encode()).hexdigest(),
    )


def queue_index(applied: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = applied.get("follow_up_queue")
    if not isinstance(rows, list):
        raise ProvenanceFollowupError("Applied provenance ledger has no follow-up queue.")
    index = {str(row.get("clip_id")): row for row in rows}
    if set(index) != {
        BOUNDARY_CLIP_ID,
        "benny_hesitation_protective_reassurance",
        CLEANUP_CLIP_ID,
    }:
        raise ProvenanceFollowupError(f"Unexpected follow-up queue: {sorted(index)}")
    if index["benny_hesitation_protective_reassurance"].get("follow_up_type") != "replacement_source_required":
        raise ProvenanceFollowupError("Role-contaminated Benny clip must not enter salvage generation.")
    return index


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    applied_path = Path(args.applied).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    separator_command = Path(args.separator_command).expanduser().resolve()
    model_dir = Path(args.model_dir).expanduser().resolve()
    whisper_model = Path(args.whisper_model).expanduser().resolve()
    if not separator_command.is_file():
        raise ProvenanceFollowupError(f"Separator command is missing: {separator_command}")
    if not model_dir.is_dir():
        raise ProvenanceFollowupError(f"Separator model directory is missing: {model_dir}")
    if not whisper_model.is_dir():
        raise ProvenanceFollowupError(f"Whisper model is missing: {whisper_model}")

    applied = load_json(applied_path)
    if applied.get("round_id") != APPLIED_ROUND_ID:
        raise ProvenanceFollowupError(f"Unexpected applied round_id: {applied.get('round_id')}")
    queue = queue_index(applied)
    if output_root.exists() and args.force:
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    model_receipts = {}
    for key, model in SEPARATION_MODELS.items():
        path = model_dir / model["filename"]
        if not path.is_file():
            raise ProvenanceFollowupError(f"Separation model is missing: {path}")
        model_receipts[key] = {
            **model,
            "path": str(path),
            "sha256": sha256_file(path),
        }

    rows: list[dict[str, Any]] = []

    boundary = queue[BOUNDARY_CLIP_ID]
    source = Path(str(boundary.get("source_audio") or ""))
    previous = Path(str(boundary.get("candidate_audio") or ""))
    if not previous.is_file():
        raise ProvenanceFollowupError("Prior Benny boundary candidate is missing.")
    recut = output_root / "boundary" / f"{BOUNDARY_CLIP_ID}.wav"
    if source.is_file():
        extract_boundary(
            source,
            float(boundary["suggested_start_seconds"]),
            float(boundary["suggested_end_seconds"]),
            recut,
        )
        boundary_source_method = "absolute_source_recut"
    else:
        relative_start = float(boundary["suggested_start_seconds"]) - float(boundary["selected_start_seconds"])
        relative_end = float(boundary["suggested_end_seconds"]) - float(boundary["selected_start_seconds"])
        extract_boundary(previous, relative_start, relative_end, recut)
        boundary_source_method = "hash_verified_candidate_relative_trim"
    rows.append({
        "card_id": f"boundary:{BOUNDARY_CLIP_ID}",
        "card_type": "boundary_final",
        "clip_id": BOUNDARY_CLIP_ID,
        "target": boundary["target"],
        "target_label": boundary["target_label"],
        "source_title": boundary["source_title"],
        "selected_transcript": boundary["selected_transcript"],
        "primary_emotion": boundary["primary_emotion"],
        "dramatic_function": boundary["dramatic_function"],
        "review_notes": boundary.get("review_notes"),
        "boundary_reason": boundary["reason"],
        "absolute_start_seconds": boundary["suggested_start_seconds"],
        "absolute_end_seconds": boundary["suggested_end_seconds"],
        "source_recovery_method": boundary_source_method,
        "previous_audio_path": str(previous),
        "previous_audio_sha256": sha256_file(previous),
        "final": evaluate(recut, boundary["selected_transcript"], whisper_model, floor=0.88),
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    })

    cleanup = queue[CLEANUP_CLIP_ID]
    cleanup_source = Path(str(cleanup.get("source_audio") or ""))
    cleanup_candidate = Path(str(cleanup.get("candidate_audio") or ""))
    if not cleanup_candidate.is_file():
        raise ProvenanceFollowupError(f"Doctor reviewed candidate is missing: {cleanup_candidate}")
    original = output_root / "separation" / CLEANUP_CLIP_ID / "original.wav"
    original.parent.mkdir(parents=True, exist_ok=True)
    if cleanup_source.is_file():
        run([
            "ffmpeg", "-v", "error", "-y",
            "-ss", f"{float(cleanup['selected_start_seconds']):.3f}",
            "-to", f"{float(cleanup['selected_end_seconds']):.3f}",
            "-i", str(cleanup_source),
            "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(original),
        ])
        cleanup_source_method = "absolute_source_extraction"
    else:
        run([
            "ffmpeg", "-v", "error", "-y", "-i", str(cleanup_candidate),
            "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(original),
        ])
        cleanup_source_method = "hash_verified_reviewed_clip"
    candidates = []
    for ordinal, model_key in enumerate(blind_order(CLEANUP_CLIP_ID), start=1):
        model = SEPARATION_MODELS[model_key]
        working = output_root / "working" / CLEANUP_CLIP_ID / model_key
        if working.exists():
            shutil.rmtree(working)
        working.mkdir(parents=True)
        run([
            str(separator_command),
            "-m", model["filename"],
            "--model_file_dir", str(model_dir),
            "--output_dir", str(working),
            "--output_format", "WAV",
            "--single_stem", "Vocals",
            "--normalization", "0.9",
            str(original),
        ])
        separated = find_vocal_output(working)
        destination = original.parent / f"candidate_{ordinal}.wav"
        normalize_candidate(separated, destination)
        candidates.append({
            "candidate_label": chr(ord("A") + ordinal - 1),
            "model_key": model_key,
            "model_filename": model["filename"],
            **evaluate(destination, cleanup["selected_transcript"], whisper_model, floor=0.75),
        })
    rows.append({
        "card_id": f"separation:{CLEANUP_CLIP_ID}",
        "card_type": "source_separation",
        "clip_id": CLEANUP_CLIP_ID,
        "target": cleanup["target"],
        "target_label": cleanup["target_label"],
        "source_title": cleanup["source_title"],
        "selected_transcript": cleanup["selected_transcript"],
        "primary_emotion": cleanup["primary_emotion"],
        "dramatic_function": cleanup["dramatic_function"],
        "review_notes": cleanup.get("review_notes") or cleanup.get("reason"),
        "source_recovery_method": cleanup_source_method,
        "original": evaluate(original, cleanup["selected_transcript"], whisper_model, floor=0.70),
        "candidates": candidates,
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    })

    payload = {
        "schema_version": 1,
        "round_id": FOLLOWUP_ROUND_ID,
        "created_at": now_iso(),
        "source_applied_ledger": str(applied_path),
        "source_applied_ledger_sha256": sha256_file(applied_path),
        "card_count": len(rows),
        "card_type_counts": dict(sorted(Counter(row["card_type"] for row in rows).items())),
        "separation_models": model_receipts,
        "rows": rows,
        "abandoned_without_generation": [
            {
                "clip_id": "benny_hesitation_protective_reassurance",
                "reason": "role_contaminated_performance",
            }
        ],
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    manifest = output_root / "followup-manifest.json"
    manifest.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "card_count": len(rows),
        "card_type_counts": payload["card_type_counts"],
        "manifest": str(manifest),
    }


def validate_manifest(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("round_id") != FOLLOWUP_ROUND_ID:
        raise ProvenanceFollowupError("Follow-up manifest has an unexpected round_id.")
    rows = payload.get("rows")
    if not isinstance(rows, list) or len(rows) != 2:
        raise ProvenanceFollowupError("Follow-up manifest must contain exactly two cards.")
    failures = []
    seen = set()
    for row in rows:
        card_id = row.get("card_id")
        if card_id in seen:
            failures.append(f"duplicate:{card_id}")
        seen.add(card_id)
        if row.get("production_promotion_allowed") is not False:
            failures.append(f"promotion:{card_id}")
        if row.get("card_type") == "source_separation":
            if len(row.get("candidates") or []) != 3:
                failures.append(f"candidate_count:{card_id}")
            paths = [row.get("original", {}).get("audio_path")] + [
                item.get("audio_path") for item in row.get("candidates") or []
            ]
        else:
            paths = [row.get("previous_audio_path"), row.get("final", {}).get("audio_path")]
        for value in paths:
            path = Path(str(value or ""))
            if not path.is_file():
                failures.append(f"missing:{card_id}:{path}")
    if payload.get("automatic_production_assignment") is not False:
        failures.append("automatic_production_assignment")
    if payload.get("production_promotion_allowed") is not False:
        failures.append("production_promotion_allowed")
    if failures:
        raise ProvenanceFollowupError(f"Follow-up manifest validation failed: {failures}")
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

    index = (review / "index.html").read_text(encoding="utf-8")
    index = index.replace("Final Three-Voice Salvage Gate", "Benny / Doctor Provenance Salvage")
    index = index.replace(
        "Four cards test real vocal-source separation. Five cards verify the final Narrator boundaries.",
        "One card verifies the corrected Benny boundary. One card tests the final Doctor cleanup attempt.",
    )
    (review / "index.html").write_text(index, encoding="utf-8")
    app = (review / "app.js").read_text(encoding="utf-8")
    app = app.replace("alexandria:three-voice-final-salvage:", "alexandria:three-voice-provenance-followups:")
    app = app.replace(
        "alexandria_three_voice_final_salvage_review.json",
        "alexandria_three_voice_provenance_followups_review.json",
    )
    (review / "app.js").write_text(app, encoding="utf-8")

    public_rows = []
    answer_rows = []
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
        if row["card_type"] == "source_separation":
            original_mp3 = review / "audio" / f"{row['clip_id']}_original.mp3"
            encode_mp3(Path(row["original"]["audio_path"]), original_mp3)
            public_candidates = []
            for candidate in row["candidates"]:
                candidate_mp3 = review / "audio" / f"{row['clip_id']}_{candidate['candidate_label']}.mp3"
                encode_mp3(Path(candidate["audio_path"]), candidate_mp3)
                public_candidates.append({
                    "candidate_label": candidate["candidate_label"],
                    "audio": f"audio/{candidate_mp3.name}",
                    "technical_pass": candidate["technical_pass"],
                    "verification_similarity": candidate["verification_similarity"],
                })
            public_rows.append({
                **common,
                "original_audio": f"audio/{original_mp3.name}",
                "candidates": public_candidates,
            })
        else:
            previous_mp3 = review / "audio" / f"{row['clip_id']}_previous.mp3"
            final_mp3 = review / "audio" / f"{row['clip_id']}_final.mp3"
            encode_mp3(Path(row["previous_audio_path"]), previous_mp3)
            encode_mp3(Path(row["final"]["audio_path"]), final_mp3)
            public_rows.append({
                **common,
                "boundary_reason": row["boundary_reason"],
                "previous_audio": f"audio/{previous_mp3.name}",
                "final_audio": f"audio/{final_mp3.name}",
                "technical_pass": row["final"]["technical_pass"],
                "verification_similarity": row["final"]["verification_similarity"],
            })
        answer_rows.append(row)

    public = {
        "schema_version": 1,
        "round_id": REVIEW_ROUND_ID,
        "title": "Benny / Doctor Provenance Salvage",
        "card_count": len(public_rows),
        "card_type_counts": dict(sorted(Counter(row["card_type"] for row in public_rows).items())),
        "rows": public_rows,
    }
    (review / "data.js").write_text(
        "window.THREE_VOICE_FINAL_SALVAGE_DATA = "
        + json.dumps(public, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )
    review_manifest = {
        "schema_version": 1,
        "round_id": REVIEW_ROUND_ID,
        "card_count": len(public_rows),
        "card_type_counts": public["card_type_counts"],
        "model_names_blinded": True,
        "maximum_simultaneous_audio_elements": 4,
        "range_server_included": True,
        "abandoned_role_contaminated_clip_count": 1,
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    (review / "manifest.json").write_text(
        json.dumps(review_manifest, indent=2) + "\n", encoding="utf-8"
    )
    (output_root / "answer-key.json").write_text(
        json.dumps(answer_rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_root / "START_HERE.txt").write_text(
        f'cd "{review}"\npython3 serve_review.py --bind 127.0.0.1 --port 8793\n\n'
        "Then open http://127.0.0.1:8793/\n",
        encoding="utf-8",
    )
    return {"card_count": len(public_rows), "review": str(review / "index.html")}


def validate_package(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.output_root).expanduser().resolve()
    review = root / "review"
    prefix = "window.THREE_VOICE_FINAL_SALVAGE_DATA = "
    text = (review / "data.js").read_text(encoding="utf-8").strip()
    if not text.startswith(prefix):
        raise ProvenanceFollowupError("Review data.js prefix is invalid.")
    data = json.loads(text[len(prefix):].rstrip(";"))
    failures = []
    if data.get("round_id") != REVIEW_ROUND_ID or len(data.get("rows") or []) != 2:
        failures.append("review_contract")
    for row in data.get("rows") or []:
        if row["card_type"] == "source_separation":
            paths = [row["original_audio"]] + [item["audio"] for item in row["candidates"]]
            if len(row["candidates"]) != 3:
                failures.append(f"candidates:{row['card_id']}")
        else:
            paths = [row["previous_audio"], row["final_audio"]]
        for relative in paths:
            path = review / relative
            if not path.is_file():
                failures.append(f"missing:{row['card_id']}:{relative}")
    if failures:
        raise ProvenanceFollowupError(f"Review package validation failed: {failures}")
    return {"card_count": len(data["rows"]), "failure_count": 0, "review": str(review / "index.html")}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare the final two bounded provenance follow-ups for Benny and the Seventh Doctor."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--applied", required=True)
    prepare_parser.add_argument("--output-root", required=True)
    prepare_parser.add_argument("--separator-command", required=True)
    prepare_parser.add_argument("--model-dir", required=True)
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
    except (ProvenanceFollowupError, subprocess.CalledProcessError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
