#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

BENCHMARKS_ROOT = Path(__file__).resolve().parent
if str(BENCHMARKS_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS_ROOT))

from apply_three_voice_selected_refinement_review import (
    copy_reference_audio,
    gap_assessment,
    load_json,
    sha256_file,
    slug,
    validated_audio,
)

MANIFEST_ROUND_ID = "alexandria_three_voice_provenance_final_boundaries_v1"
REVIEW_ROUND_ID = "alexandria_three_voice_provenance_final_boundaries_review_v1"
PRIOR_BANK_ROUND_ID = "alexandria_three_voice_validated_reference_bank_v2"
HISTORICAL_BANK_ROUND_ID = "alexandria_transcript_guided_source_isolation_v1"
FINAL_BANK_ROUND_ID = "alexandria_three_voice_validated_reference_bank_v3"
APPLIED_ROUND_ID = "alexandria_three_voice_provenance_final_boundaries_review_applied_v1"

EXPECTED_CARDS = {
    "boundary:benny_hesitation_fatalistic_dread": "approve_final",
    "separation:doctor_acf_dismissive_contempt": "candidate_C",
}


class FinalBoundaryApplyError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def require_round(payload: Any, expected: str, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise FinalBoundaryApplyError(f"{label} must be a JSON object.")
    if payload.get("round_id") != expected:
        raise FinalBoundaryApplyError(
            f"Unexpected {label} round_id: {payload.get('round_id')!r}; expected {expected!r}."
        )
    return payload


def rows_by_id(rows: Any, *, key: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise FinalBoundaryApplyError(f"{label} must contain a non-empty list.")
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise FinalBoundaryApplyError(f"Every {label} row must be an object.")
        value = row.get(key)
        if not isinstance(value, str) or not value.strip():
            raise FinalBoundaryApplyError(f"Every {label} row requires {key}.")
        if value in indexed:
            raise FinalBoundaryApplyError(f"Duplicate {label} {key}: {value}")
        indexed[value] = row
    return indexed


def unwrap_review(payload: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(payload, dict):
        raise FinalBoundaryApplyError("Normalized review must be a JSON object.")
    review = payload.get("review", payload)
    metadata = {
        "source_upload_name": payload.get("source_upload_name"),
        "source_upload_sha256": payload.get("source_upload_sha256"),
    }
    return require_round(review, REVIEW_ROUND_ID, "final-boundary review"), metadata


def verify_review(review: dict[str, Any], manifest_rows: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    rows = rows_by_id(review.get("rows"), key="card_id", label="final-boundary review")
    if set(rows) != set(EXPECTED_CARDS) or set(rows) != set(manifest_rows):
        raise FinalBoundaryApplyError(
            f"Card mismatch: review={sorted(rows)}, manifest={sorted(manifest_rows)}"
        )
    summary = review.get("summary")
    expected_summary = {
        "card_count": 2,
        "complete_count": 2,
        "separation_selected_count": 1,
        "separation_none_count": 0,
        "boundary_approved_count": 1,
        "boundary_wrong_count": 0,
    }
    if summary != expected_summary:
        raise FinalBoundaryApplyError(f"Review summary mismatch: {summary!r} != {expected_summary!r}")
    for card_id, expected_decision in EXPECTED_CARDS.items():
        reviewed = rows[card_id]
        manifest = manifest_rows[card_id]
        if reviewed.get("decision") != expected_decision:
            raise FinalBoundaryApplyError(
                f"Unexpected decision for {card_id}: {reviewed.get('decision')!r}; expected {expected_decision!r}."
            )
        for field in ("card_type", "clip_id", "target", "target_label", "selected_transcript", "primary_emotion"):
            if reviewed.get(field) != manifest.get(field):
                raise FinalBoundaryApplyError(
                    f"Review changed stable field {field} for {card_id}: "
                    f"{reviewed.get(field)!r} != {manifest.get(field)!r}"
                )
    return rows


def candidate_for_label(row: dict[str, Any], label: str) -> dict[str, Any]:
    for candidate in row.get("candidates") or []:
        if candidate.get("candidate_label") == label:
            return candidate
    raise FinalBoundaryApplyError(f"Candidate {label} is missing for {row.get('card_id')}")


def copy_prior_reference(row: dict[str, Any], output_root: Path) -> dict[str, Any]:
    clip_id = str(row.get("clip_id") or "")
    target = str(row.get("target") or "")
    source = validated_audio(
        row.get("audio_path"), row.get("audio_sha256"), f"prior:{clip_id}", require_bank_format=True
    )
    destination, digest = copy_reference_audio(source, output_root, target, clip_id)
    return {
        **row,
        "source_reference_audio_sha256": sha256_file(source),
        "audio_path": str(destination),
        "audio_sha256": digest,
        "production_promotion_allowed": False,
    }


def build_final_reference(
    manifest: dict[str, Any],
    review: dict[str, Any],
    historical: dict[str, Any],
    source_audio: Path,
    output_root: Path,
    *,
    reference_status: str,
    source_kind: str,
    provenance: str,
    selected_start_seconds: float,
    selected_end_seconds: float,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clip_id = manifest["clip_id"]
    target = manifest["target"]
    source_audio = validated_audio(
        source_audio,
        sha256_file(source_audio),
        f"selected:{clip_id}",
        require_bank_format=True,
    )
    destination, digest = copy_reference_audio(source_audio, output_root, target, clip_id)
    return {
        "clip_id": clip_id,
        "target": target,
        "target_label": manifest["target_label"],
        "source_title": manifest["source_title"],
        "source_kind": source_kind,
        "youtube_id": None,
        "selected_transcript": manifest["selected_transcript"],
        "selected_start_seconds": round(float(selected_start_seconds), 3),
        "selected_end_seconds": round(float(selected_end_seconds), 3),
        "primary_emotion": manifest["primary_emotion"],
        "secondary_emotion": historical.get("secondary_emotion"),
        "dramatic_function": manifest["dramatic_function"],
        "intensity_1_to_5": historical.get("intensity_1_to_5"),
        "coverage_family": slug(str(manifest.get("primary_emotion") or "")),
        "speaker_certainty": "explicitly_human_approved_final_gate",
        "speaker_role": historical.get("speaker_role"),
        "source_audio_sha256": historical.get("source_sha256"),
        "source_reference_audio_sha256": sha256_file(source_audio),
        "audio_path": str(destination),
        "audio_sha256": digest,
        "reference_status": reference_status,
        "provenance": provenance,
        "review_decision": review["decision"],
        "review_revision": review.get("revision"),
        "review_updated_at": review.get("updated_at"),
        **(extra or {}),
        "production_promotion_allowed": False,
    }


def validate_final_bank(payload: Any) -> dict[str, Any]:
    bank = require_round(payload, FINAL_BANK_ROUND_ID, "final validated bank")
    rows = bank.get("references")
    if not isinstance(rows, list) or len(rows) != int(bank.get("reference_count") or 0):
        raise FinalBoundaryApplyError("Final bank reference count is inconsistent.")
    seen: set[str] = set()
    counts: Counter[str] = Counter()
    for row in rows:
        clip_id = str(row.get("clip_id") or "")
        if not clip_id or clip_id in seen:
            raise FinalBoundaryApplyError(f"Invalid or duplicate clip_id: {clip_id!r}")
        seen.add(clip_id)
        counts[str(row.get("target"))] += 1
        validated_audio(
            row.get("audio_path"), row.get("audio_sha256"), f"bank:{clip_id}", require_bank_format=True
        )
        if row.get("production_promotion_allowed") is not False:
            raise FinalBoundaryApplyError(f"Reference may not auto-promote: {clip_id}")
    expected_counts = {"benny": 10, "doctor": 5, "narrator": 16}
    if dict(sorted(counts.items())) != expected_counts:
        raise FinalBoundaryApplyError(f"Unexpected final counts: {dict(counts)}")
    if bank.get("reference_counts_by_target") != expected_counts:
        raise FinalBoundaryApplyError("reference_counts_by_target is inconsistent.")
    if bank.get("reference_count") != 31:
        raise FinalBoundaryApplyError("Final bank must contain 31 references.")
    if bank.get("automatic_production_assignment") is not False:
        raise FinalBoundaryApplyError("Automatic production assignment must remain disabled.")
    if bank.get("production_promotion_allowed") is not False:
        raise FinalBoundaryApplyError("Production promotion must remain disabled.")
    open_gaps = {
        target: [row["function"] for row in detail["requirements"] if row["status"] == "open_gap"]
        for target, detail in bank.get("coverage_assessment", {}).items()
    }
    return {
        "reference_count": len(rows),
        "reference_counts_by_target": expected_counts,
        "remaining_follow_up_count": len(bank.get("follow_up_queue") or []),
        "open_gaps": open_gaps,
    }


def build(
    *,
    prior_bank: dict[str, Any],
    manifest: dict[str, Any],
    answer_key: list[dict[str, Any]],
    normalized_review: dict[str, Any],
    historical_bank: dict[str, Any],
    output_root: Path,
    source_paths: dict[str, Path],
) -> tuple[dict[str, Any], dict[str, Any]]:
    prior = require_round(prior_bank, PRIOR_BANK_ROUND_ID, "prior validated bank")
    final_manifest = require_round(manifest, MANIFEST_ROUND_ID, "final-boundary manifest")
    historical = require_round(historical_bank, HISTORICAL_BANK_ROUND_ID, "historical source bank")
    manifest_rows = rows_by_id(final_manifest.get("rows"), key="card_id", label="final-boundary manifest")
    answers = rows_by_id(answer_key, key="card_id", label="final-boundary answer key")
    if set(answers) != set(manifest_rows):
        raise FinalBoundaryApplyError("Manifest/answer-key card mismatch.")
    review, upload_metadata = unwrap_review(normalized_review)
    reviews = verify_review(review, manifest_rows)
    historical_rows = rows_by_id(
        historical.get("accepted_candidates"), key="clip_id", label="historical source bank"
    )

    prior_rows = prior.get("references")
    if not isinstance(prior_rows, list) or len(prior_rows) != 29:
        raise FinalBoundaryApplyError("Prior bank must contain exactly 29 references.")
    prior_ids = {str(row.get("clip_id")) for row in prior_rows}
    added_ids = {row["clip_id"] for row in manifest_rows.values()}
    if prior_ids & added_ids:
        raise FinalBoundaryApplyError(f"Final clips already exist in prior bank: {sorted(prior_ids & added_ids)}")

    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    references = [copy_prior_reference(row, output_root) for row in prior_rows]

    benny_card = manifest_rows["boundary:benny_hesitation_fatalistic_dread"]
    benny_review = reviews[benny_card["card_id"]]
    benny_source = Path(str((benny_card.get("final") or {}).get("audio_path") or ""))
    benny_historical = historical_rows[benny_card["clip_id"]]
    benny_reference = build_final_reference(
        benny_card,
        benny_review,
        benny_historical,
        benny_source,
        output_root,
        reference_status="approved_final_boundary_human_validated",
        source_kind="transcript_guided_source_human_validated_boundary_final",
        provenance="historical_provenance_final_boundary_review",
        selected_start_seconds=float(benny_historical["audio_start_seconds"]) + float(benny_card["relative_start_seconds"]),
        selected_end_seconds=float(benny_historical["audio_end_seconds"]),
        extra={
            "boundary_policy": benny_card.get("end_policy"),
            "technical_verification": benny_card.get("final"),
        },
    )
    references.append(benny_reference)

    doctor_card = manifest_rows["separation:doctor_acf_dismissive_contempt"]
    doctor_review = reviews[doctor_card["card_id"]]
    doctor_candidate = candidate_for_label(answers[doctor_card["card_id"]], "C")
    doctor_source = Path(str(doctor_candidate.get("audio_path") or ""))
    doctor_historical = historical_rows[doctor_card["clip_id"]]
    doctor_start = float(doctor_historical["audio_start_seconds"])
    doctor_reference = build_final_reference(
        doctor_card,
        doctor_review,
        doctor_historical,
        doctor_source,
        output_root,
        reference_status="approved_source_separation_final_boundary_human_validated",
        source_kind="transcript_guided_source_human_validated_cleanup_final",
        provenance="historical_provenance_final_boundary_review",
        selected_start_seconds=doctor_start,
        selected_end_seconds=doctor_start + float(doctor_candidate["trim_end_seconds"]),
        extra={
            "separation_model_key": doctor_candidate.get("model_key"),
            "separation_model_filename": doctor_candidate.get("model_filename"),
            "tail_policy": doctor_card.get("tail_policy"),
            "technical_verification": {
                key: doctor_candidate.get(key)
                for key in ("verification_transcript", "verification_similarity", "metrics", "technical_pass")
            },
        },
    )
    references.append(doctor_reference)

    references.sort(key=lambda row: (str(row.get("target")), str(row.get("clip_id"))))
    counts = Counter(str(row.get("target")) for row in references)
    coverage = gap_assessment(references)
    follow_up_queue = [
        row
        for row in prior.get("follow_up_queue") or []
        if row.get("clip_id") not in added_ids
    ]
    decisions = [
        {
            "card_id": card_id,
            "clip_id": manifest_rows[card_id]["clip_id"],
            "target": manifest_rows[card_id]["target"],
            "decision": reviews[card_id]["decision"],
            "included": True,
            "review_updated_at": reviews[card_id].get("updated_at"),
        }
        for card_id in sorted(EXPECTED_CARDS)
    ]

    bank = {
        "schema_version": 3,
        "round_id": FINAL_BANK_ROUND_ID,
        "created_at": now_iso(),
        "reference_count": len(references),
        "reference_counts_by_target": dict(sorted(counts.items())),
        "core_reference_count": prior.get("core_reference_count", 19),
        "newly_human_validated_reference_count": int(prior.get("newly_human_validated_reference_count") or 0) + 2,
        "pending_historical_candidate_count": 0,
        "coverage_assessment": coverage,
        "references": references,
        "follow_up_queue": follow_up_queue,
        "rejected_sources": list(prior.get("rejected_sources") or []),
        "final_boundary_decisions": decisions,
        "source_artifacts": {
            name: {"path": str(path), "sha256": sha256_file(path)}
            for name, path in source_paths.items()
        },
        "source_upload": upload_metadata,
        "bank_status": "validated_research_reference_bank",
        "ready_for_targeted_generation_benchmark": True,
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    bank_path = output_root / "three-voice-validated-reference-bank.json"
    bank_path.write_text(json.dumps(bank, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    validation = validate_final_bank(bank)

    open_gaps = validation["open_gaps"]
    report = {
        "schema_version": 1,
        "round_id": FINAL_BANK_ROUND_ID,
        "reference_count": len(references),
        "reference_counts_by_target": dict(sorted(counts.items())),
        "remaining_follow_up_count": len(follow_up_queue),
        "open_gaps": open_gaps,
        "recommendation": (
            "Use the 31-reference bank for bounded generation benchmarking. Do not continue cleanup of "
            "the role-contaminated Benny reassurance clip. Mine only the explicitly named open gaps."
        ),
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    report_path = output_root / "coverage-report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    applied = {
        "schema_version": 1,
        "round_id": APPLIED_ROUND_ID,
        "created_at": now_iso(),
        "review_export": {
            "path": str(source_paths["normalized_review"]),
            "sha256": sha256_file(source_paths["normalized_review"]),
            **upload_metadata,
            "exported_at": review.get("exported_at"),
            "reported_summary": review.get("summary"),
        },
        "final_boundary_manifest": {
            "path": str(source_paths["final_boundary_manifest"]),
            "sha256": sha256_file(source_paths["final_boundary_manifest"]),
        },
        "answer_key": {
            "path": str(source_paths["answer_key"]),
            "sha256": sha256_file(source_paths["answer_key"]),
        },
        "prior_bank": {
            "path": str(source_paths["prior_bank"]),
            "sha256": sha256_file(source_paths["prior_bank"]),
            "reference_count": 29,
        },
        "final_bank": {
            "path": str(bank_path),
            "sha256": sha256_file(bank_path),
            "reference_count": 31,
        },
        "added_reference_count": 2,
        "added_reference_counts_by_target": {"benny": 1, "doctor": 1},
        "decisions": decisions,
        "remaining_follow_up_count": len(follow_up_queue),
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    applied_path = output_root / "applied-final-boundary-review-ledger.json"
    applied_path.write_text(json.dumps(applied, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return bank, applied


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply the final two three-voice provenance decisions.")
    sub = parser.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--prior-bank", required=True)
    build_parser.add_argument("--manifest", required=True)
    build_parser.add_argument("--answer-key", required=True)
    build_parser.add_argument("--review", required=True)
    build_parser.add_argument("--historical-bank", required=True)
    build_parser.add_argument("--output-root", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--bank", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "build":
            source_paths = {
                "prior_bank": Path(args.prior_bank).expanduser().resolve(),
                "final_boundary_manifest": Path(args.manifest).expanduser().resolve(),
                "answer_key": Path(args.answer_key).expanduser().resolve(),
                "normalized_review": Path(args.review).expanduser().resolve(),
                "historical_bank": Path(args.historical_bank).expanduser().resolve(),
            }
            bank, applied = build(
                prior_bank=load_json(source_paths["prior_bank"]),
                manifest=load_json(source_paths["final_boundary_manifest"]),
                answer_key=load_json(source_paths["answer_key"]),
                normalized_review=load_json(source_paths["normalized_review"]),
                historical_bank=load_json(source_paths["historical_bank"]),
                output_root=Path(args.output_root).expanduser().resolve(),
                source_paths=source_paths,
            )
            result = {
                **validate_final_bank(bank),
                "bank": str(Path(args.output_root).expanduser().resolve() / "three-voice-validated-reference-bank.json"),
                "applied_ledger": str(Path(args.output_root).expanduser().resolve() / "applied-final-boundary-review-ledger.json"),
                "added_reference_count": applied["added_reference_count"],
            }
        else:
            result = validate_final_bank(load_json(Path(args.bank).expanduser().resolve()))
    except (FinalBoundaryApplyError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
