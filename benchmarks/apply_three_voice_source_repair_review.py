#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPAIR_ROUND_ID = "alexandria_three_voice_source_repairs_v1"
REVIEW_ROUND_ID = "alexandria_three_voice_source_repairs_review_v1"
APPLIED_ROUND_ID = "alexandria_three_voice_source_repair_review_applied_v1"
PRIOR_APPLIED_ROUND_ID = "alexandria_three_voice_source_atlas_applied_v1"


class RepairReviewError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    if not path.is_file():
        raise RepairReviewError(f"JSON file is missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RepairReviewError(f"Invalid JSON in {path}: {exc}") from exc


def index_rows(payload: Any, *, round_id: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("round_id") != round_id:
        raise RepairReviewError(f"{label} has an unexpected round_id.")
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise RepairReviewError(f"{label} has no rows.")
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        clip_id = row.get("clip_id")
        if not isinstance(clip_id, str) or not clip_id:
            raise RepairReviewError(f"Every {label} row requires clip_id.")
        if clip_id in indexed:
            raise RepairReviewError(f"Duplicate {label} row: {clip_id}")
        indexed[clip_id] = row
    return indexed


def issue_flags(review: dict[str, Any]) -> list[str]:
    notes = str(review.get("notes") or "").casefold()
    flags: list[str] = []
    if any(term in notes for term in ("music", "background", "sound effect", "artifact", "compressed")):
        flags.append("mixed_background_contamination")
    if any(term in notes for term in ("boundary", "cut off", "cuts off", "too early", "too late")):
        flags.append("boundary_problem")
    return flags


def classify(decision: str | None) -> str:
    mapping = {
        "approve_repaired": "approved_after_repair",
        "cleanup_still_bad": "source_separation_required",
        "boundary_still_wrong": "boundary_repair_required",
        "mine_nearby": "mine_nearby_required",
        "reject": "rejected_after_repair",
    }
    return mapping.get(decision, "invalid_or_missing_decision")


def build_applied(
    repair_payload: Any,
    review_payload: Any,
    prior_payload: Any,
    *,
    repair_path: Path,
    review_path: Path,
    prior_path: Path,
) -> dict[str, Any]:
    repairs = index_rows(repair_payload, round_id=REPAIR_ROUND_ID, label="repair manifest")
    reviews = index_rows(review_payload, round_id=REVIEW_ROUND_ID, label="repair review")
    if not isinstance(prior_payload, dict) or prior_payload.get("round_id") != PRIOR_APPLIED_ROUND_ID:
        raise RepairReviewError("Prior applied ledger has an unexpected round_id.")
    if set(repairs) != set(reviews):
        raise RepairReviewError(
            f"Repair/review clip mismatch: missing={sorted(set(repairs)-set(reviews))}, "
            f"unknown={sorted(set(reviews)-set(repairs))}"
        )

    disposition_rows: list[dict[str, Any]] = []
    approved_after_repair: list[dict[str, Any]] = []
    source_separation_queue: list[dict[str, Any]] = []
    boundary_queue: list[dict[str, Any]] = []
    mine_nearby_queue: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []

    for clip_id, repair in repairs.items():
        review = reviews[clip_id]
        stable_values = {
            "target": repair.get("target"),
            "repair_type": repair.get("repair_type"),
            "selected_transcript": repair.get("repaired_transcript") or repair.get("selected_transcript"),
        }
        for key, expected in stable_values.items():
            value = review.get(key)
            if value not in (None, "") and value != expected:
                raise RepairReviewError(f"Review changed stable field {key} for {clip_id}")
        repaired_audio = Path(str(repair.get("repaired_audio_path") or ""))
        if not repaired_audio.is_file():
            raise RepairReviewError(f"Repaired audio is missing for {clip_id}: {repaired_audio}")
        repaired_hash = sha256_file(repaired_audio)
        if repaired_hash != repair.get("repaired_audio_sha256"):
            raise RepairReviewError(f"Repaired audio hash mismatch for {clip_id}")
        disposition = classify(review.get("decision"))
        row = {
            "clip_id": clip_id,
            "target": repair.get("target"),
            "target_label": repair.get("target_label"),
            "repair_type": repair.get("repair_type"),
            "repair_reason": repair.get("repair_reason"),
            "source": repair.get("source"),
            "source_title": repair.get("source_title"),
            "source_kind": repair.get("source_kind"),
            "youtube_id": repair.get("youtube_id"),
            "source_audio": repair.get("source_audio"),
            "source_audio_sha256": repair.get("source_audio_sha256"),
            "selected_start_seconds": repair.get("selected_start_seconds"),
            "selected_end_seconds": repair.get("selected_end_seconds"),
            "selected_transcript": repair.get("repaired_transcript") or repair.get("selected_transcript"),
            "primary_emotion": repair.get("primary_emotion"),
            "secondary_emotion": repair.get("secondary_emotion"),
            "dramatic_function": repair.get("dramatic_function"),
            "coverage_gap": repair.get("coverage_gap"),
            "original_audio_path": repair.get("original_audio_path"),
            "original_audio_sha256": repair.get("original_audio_sha256"),
            "repaired_audio_path": str(repaired_audio),
            "repaired_audio_sha256": repaired_hash,
            "technical_pass": bool(repair.get("technical_pass")),
            "review_decision": review.get("decision"),
            "review_notes": review.get("notes") or None,
            "review_revision": review.get("revision"),
            "review_updated_at": review.get("updated_at"),
            "issue_flags": issue_flags(review),
            "disposition": disposition,
            "production_promotion_allowed": False,
        }
        disposition_rows.append(row)
        if disposition == "approved_after_repair":
            approved_after_repair.append({
                **row,
                "reference_status": "approved_source_reference_after_repair",
            })
        elif disposition == "source_separation_required":
            source_separation_queue.append(row)
        elif disposition == "boundary_repair_required":
            boundary_queue.append(row)
        elif disposition == "mine_nearby_required":
            mine_nearby_queue.append(row)
        elif disposition == "rejected_after_repair":
            rejected.append(row)
        else:
            invalid.append(row)

    prior_approved = list(prior_payload.get("approved_clean_references") or [])
    validated = []
    seen: set[str] = set()
    for row in prior_approved + approved_after_repair:
        clip_id = str(row.get("clip_id"))
        if clip_id in seen:
            raise RepairReviewError(f"Duplicate validated reference: {clip_id}")
        seen.add(clip_id)
        validated.append({**row, "production_promotion_allowed": False})

    counts = Counter(row["disposition"] for row in disposition_rows)
    by_target: dict[str, Counter[str]] = defaultdict(Counter)
    for row in disposition_rows:
        by_target[str(row.get("target"))][row["disposition"]] += 1
    return {
        "schema_version": 1,
        "round_id": APPLIED_ROUND_ID,
        "created_at": now_iso(),
        "repair_manifest": {"path": str(repair_path), "sha256": sha256_file(repair_path)},
        "repair_review": {
            "path": str(review_path),
            "sha256": sha256_file(review_path),
            "source_export_sha256": review_payload.get("source_export_sha256"),
            "exported_at": review_payload.get("exported_at"),
            "reported_summary": review_payload.get("summary"),
        },
        "prior_applied_ledger": {"path": str(prior_path), "sha256": sha256_file(prior_path)},
        "candidate_count": len(disposition_rows),
        "disposition_counts": dict(sorted(counts.items())),
        "target_disposition_counts": {
            target: dict(sorted(counter.items())) for target, counter in sorted(by_target.items())
        },
        "prior_approved_clean_count": len(prior_approved),
        "approved_after_repair_count": len(approved_after_repair),
        "validated_reference_count": len(validated),
        "validated_references": validated,
        "approved_after_repair": approved_after_repair,
        "source_separation_queue": source_separation_queue,
        "boundary_repair_queue": boundary_queue,
        "mine_nearby_queue": mine_nearby_queue,
        "rejected_after_repair": rejected,
        "invalid_or_missing_decisions": invalid,
        "all_dispositions": disposition_rows,
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }


def validate(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("round_id") != APPLIED_ROUND_ID:
        raise RepairReviewError("Applied repair review has an unexpected round_id.")
    rows = payload.get("all_dispositions")
    if not isinstance(rows, list) or len(rows) != payload.get("candidate_count"):
        raise RepairReviewError("Applied repair review rows/count mismatch.")
    ids = [row.get("clip_id") for row in rows]
    if len(ids) != len(set(ids)):
        raise RepairReviewError("Applied repair review has duplicate clip IDs.")
    for row in rows:
        audio = Path(str(row.get("repaired_audio_path") or ""))
        if not audio.is_file() or sha256_file(audio) != row.get("repaired_audio_sha256"):
            raise RepairReviewError(f"Audio validation failed for {row.get('clip_id')}")
        if row.get("production_promotion_allowed") is not False:
            raise RepairReviewError("Repair decisions may not auto-promote.")
    if payload.get("validated_reference_count") != len(payload.get("validated_references") or []):
        raise RepairReviewError("Validated reference count mismatch.")
    if payload.get("automatic_production_assignment") is not False:
        raise RepairReviewError("Automatic production assignment must remain disabled.")
    if payload.get("production_promotion_allowed") is not False:
        raise RepairReviewError("Production promotion must remain disabled.")
    return {
        "candidate_count": len(rows),
        "validated_reference_count": payload["validated_reference_count"],
        "approved_after_repair_count": payload["approved_after_repair_count"],
        "source_separation_count": len(payload.get("source_separation_queue") or []),
        "boundary_repair_count": len(payload.get("boundary_repair_queue") or []),
        "invalid_count": len(payload.get("invalid_or_missing_decisions") or []),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply the reviewed three-voice source repair pass.")
    sub = parser.add_subparsers(dest="command", required=True)
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--repairs", required=True)
    apply_parser.add_argument("--review", required=True)
    apply_parser.add_argument("--prior", required=True)
    apply_parser.add_argument("--output", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--applied", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "apply":
            repair_path = Path(args.repairs).expanduser().resolve()
            review_path = Path(args.review).expanduser().resolve()
            prior_path = Path(args.prior).expanduser().resolve()
            payload = build_applied(
                load_json(repair_path),
                load_json(review_path),
                load_json(prior_path),
                repair_path=repair_path,
                review_path=review_path,
                prior_path=prior_path,
            )
            output = Path(args.output).expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            result = {**validate(payload), "output": str(output)}
        else:
            result = validate(load_json(Path(args.applied).expanduser().resolve()))
    except RepairReviewError as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
