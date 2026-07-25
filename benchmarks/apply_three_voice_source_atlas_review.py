#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SOURCE_ROUND_ID = "alexandria_three_voice_source_atlas_v1"
REVIEW_ROUND_ID = "alexandria_three_voice_source_atlas_review_v1"
APPLIED_ROUND_ID = "alexandria_three_voice_source_atlas_applied_v1"

APPROVED = "approve"
REJECTED = "reject"
MINE_NEARBY = "mine_nearby"
CLEAN = "clean"
USABLE_WITH_CLEANUP = "usable_with_cleanup"
CORRECT = "correct"
BOUNDARY_REPAIRS = {"too_early", "too_late", "ends_too_early", "ends_too_late"}


class ReviewApplyError(RuntimeError):
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
        raise ReviewApplyError(f"JSON file is missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReviewApplyError(f"Invalid JSON in {path}: {exc}") from exc


def rows_by_id(payload: Any, *, round_id: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ReviewApplyError(f"{label} must be a JSON object.")
    if payload.get("round_id") != round_id:
        raise ReviewApplyError(
            f"Unexpected {label} round_id: {payload.get('round_id')!r}; expected {round_id!r}."
        )
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ReviewApplyError(f"{label} must contain a non-empty rows list.")
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        clip_id = row.get("clip_id")
        if not isinstance(clip_id, str) or not clip_id.strip():
            raise ReviewApplyError(f"Every {label} row requires clip_id.")
        clip_id = clip_id.strip()
        if clip_id in indexed:
            raise ReviewApplyError(f"Duplicate {label} clip_id: {clip_id}")
        indexed[clip_id] = row
    return indexed


def stable_label(review: dict[str, Any], source: dict[str, Any], key: str, fallback_key: str | None = None) -> Any:
    value = review.get(key)
    if value not in (None, ""):
        return value
    if fallback_key:
        value = source.get(fallback_key)
        if value not in (None, ""):
            return value
    return source.get(key)


def shared_record(source: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    audio_path = Path(str(source.get("audio_path") or ""))
    if not audio_path.is_file():
        raise ReviewApplyError(f"Source audio is missing for {source.get('clip_id')}: {audio_path}")
    actual_hash = sha256_file(audio_path)
    if actual_hash != source.get("audio_sha256"):
        raise ReviewApplyError(f"Source audio hash mismatch for {source.get('clip_id')}")
    try:
        intensity = int(stable_label(review, source, "intensity_1_to_5", "intensity"))
    except (TypeError, ValueError):
        intensity = 0
    return {
        "clip_id": source["clip_id"],
        "target": source.get("target"),
        "target_label": source.get("target_label"),
        "source": source.get("source"),
        "source_title": source.get("source_title"),
        "source_kind": source.get("source_kind"),
        "youtube_id": source.get("youtube_id"),
        "source_audio": source.get("source_audio"),
        "source_audio_sha256": source.get("source_audio_sha256"),
        "audio_path": str(audio_path),
        "audio_sha256": actual_hash,
        "selected_start_seconds": source.get("selected_start_seconds"),
        "selected_end_seconds": source.get("selected_end_seconds"),
        "selected_duration_seconds": source.get("selected_duration_seconds"),
        "selected_transcript": review.get("selected_transcript") or source.get("expected_text"),
        "primary_emotion": stable_label(review, source, "primary_emotion"),
        "secondary_emotion": stable_label(review, source, "secondary_emotion"),
        "dramatic_function": stable_label(review, source, "dramatic_function"),
        "intensity_1_to_5": intensity or None,
        "coverage_gap": source.get("coverage_gap"),
        "speaker_certainty": source.get("speaker_certainty"),
        "speaker_role_decision": review.get("speaker_role_decision"),
        "boundary_decision": review.get("boundary_decision"),
        "audio_cleanliness_decision": review.get("audio_cleanliness_decision"),
        "reference_decision": review.get("reference_decision"),
        "review_notes": review.get("notes") or None,
        "review_revision": review.get("revision"),
        "review_updated_at": review.get("updated_at"),
        "production_promotion_allowed": False,
    }


def classify(record: dict[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    reference = record.get("reference_decision")
    speaker = record.get("speaker_role_decision")
    boundary = record.get("boundary_decision")
    cleanliness = record.get("audio_cleanliness_decision")

    if reference == REJECTED:
        return "rejected_by_reviewer", ["reviewer_rejected"]
    if reference == MINE_NEARBY:
        return "mine_nearby_requested", ["reviewer_requested_nearby_source"]
    if boundary in BOUNDARY_REPAIRS:
        return "boundary_repair_required", [f"boundary_{boundary}"]
    if reference == APPROVED:
        if speaker != CORRECT:
            reasons.append("speaker_role_not_confirmed")
        if boundary != CORRECT:
            reasons.append("boundary_not_confirmed")
        if cleanliness == CLEAN and not reasons:
            return "approved_clean", []
        if cleanliness == USABLE_WITH_CLEANUP and not reasons:
            return "cleanup_required", ["reviewer_approved_after_cleanup"]
        if cleanliness not in {CLEAN, USABLE_WITH_CLEANUP}:
            reasons.append("cleanliness_not_resolved")
        return "blocked_approval", reasons

    if speaker not in (None, "", CORRECT):
        reasons.append("speaker_role_rejected_or_uncertain")
    if boundary not in (None, "", CORRECT):
        reasons.append(f"boundary_{boundary}")
    if cleanliness == USABLE_WITH_CLEANUP:
        reasons.append("cleanup_selected_without_reference_decision")
    if not reasons:
        reasons.append("reference_decision_missing")
    return "incomplete_review", reasons


def build_applied(atlas_payload: Any, review_payload: Any, *, atlas_path: Path, review_path: Path) -> dict[str, Any]:
    atlas = rows_by_id(atlas_payload, round_id=SOURCE_ROUND_ID, label="source atlas")
    review = rows_by_id(review_payload, round_id=REVIEW_ROUND_ID, label="review export")
    if set(atlas) != set(review):
        missing = sorted(set(atlas) - set(review))
        unknown = sorted(set(review) - set(atlas))
        raise ReviewApplyError(f"Review/atlas clip mismatch: missing={missing}, unknown={unknown}")

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    disposition_rows: list[dict[str, Any]] = []
    for clip_id in atlas:
        source = atlas[clip_id]
        reviewed = review[clip_id]
        for key in ("target", "youtube_id"):
            review_value = reviewed.get(key)
            source_value = source.get(key)
            if review_value not in (None, "") and review_value != source_value:
                raise ReviewApplyError(
                    f"Review changed stable field {key} for {clip_id}: {review_value!r} != {source_value!r}"
                )
        record = shared_record(source, reviewed)
        disposition, reasons = classify(record)
        record["disposition"] = disposition
        record["disposition_reasons"] = reasons
        buckets[disposition].append(record)
        disposition_rows.append(record)

    counts = Counter(row["disposition"] for row in disposition_rows)
    target_status: dict[str, Counter[str]] = defaultdict(Counter)
    target_coverage: dict[str, set[str]] = defaultdict(set)
    for row in disposition_rows:
        target_status[str(row.get("target"))][row["disposition"]] += 1
        if row["disposition"] == "approved_clean" and row.get("coverage_gap"):
            target_coverage[str(row.get("target"))].add(str(row["coverage_gap"]))

    review_summary = review_payload.get("summary") if isinstance(review_payload, dict) else {}
    return {
        "schema_version": 1,
        "round_id": APPLIED_ROUND_ID,
        "created_at": now_iso(),
        "source_atlas": {
            "path": str(atlas_path),
            "round_id": SOURCE_ROUND_ID,
            "sha256": sha256_file(atlas_path),
        },
        "review_export": {
            "path": str(review_path),
            "round_id": REVIEW_ROUND_ID,
            "sha256": sha256_file(review_path),
            "exported_at": review_payload.get("exported_at") if isinstance(review_payload, dict) else None,
            "reported_summary": review_summary,
        },
        "candidate_count": len(disposition_rows),
        "disposition_counts": dict(sorted(counts.items())),
        "target_disposition_counts": {
            target: dict(sorted(counter.items())) for target, counter in sorted(target_status.items())
        },
        "approved_clean_coverage": {
            target: sorted(families) for target, families in sorted(target_coverage.items())
        },
        "approved_clean_references": buckets.get("approved_clean", []),
        "cleanup_queue": buckets.get("cleanup_required", []),
        "boundary_repair_queue": buckets.get("boundary_repair_required", []),
        "mine_nearby_queue": buckets.get("mine_nearby_requested", []),
        "incomplete_review_queue": buckets.get("incomplete_review", []),
        "blocked_approvals": buckets.get("blocked_approval", []),
        "rejected_candidates": buckets.get("rejected_by_reviewer", []),
        "all_dispositions": disposition_rows,
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }


def validate_applied(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ReviewApplyError("Applied review must be a JSON object.")
    if payload.get("round_id") != APPLIED_ROUND_ID:
        raise ReviewApplyError("Applied review has an unexpected round_id.")
    rows = payload.get("all_dispositions")
    if not isinstance(rows, list) or not rows:
        raise ReviewApplyError("Applied review has no disposition rows.")
    ids = [row.get("clip_id") for row in rows]
    if len(ids) != len(set(ids)):
        raise ReviewApplyError("Applied review contains duplicate clip IDs.")
    if len(rows) != int(payload.get("candidate_count") or 0):
        raise ReviewApplyError("Applied review candidate_count does not match rows.")
    allowed = {
        "approved_clean",
        "cleanup_required",
        "boundary_repair_required",
        "mine_nearby_requested",
        "incomplete_review",
        "blocked_approval",
        "rejected_by_reviewer",
    }
    for row in rows:
        if row.get("disposition") not in allowed:
            raise ReviewApplyError(f"Invalid disposition: {row}")
        if row.get("production_promotion_allowed") is not False:
            raise ReviewApplyError("No review row may auto-promote to production.")
        audio = Path(str(row.get("audio_path") or ""))
        if not audio.is_file() or sha256_file(audio) != row.get("audio_sha256"):
            raise ReviewApplyError(f"Audio validation failed for {row.get('clip_id')}")
    if payload.get("automatic_production_assignment") is not False:
        raise ReviewApplyError("Automatic production assignment must remain disabled.")
    if payload.get("production_promotion_allowed") is not False:
        raise ReviewApplyError("Production promotion must remain disabled.")
    counts = Counter(row["disposition"] for row in rows)
    if dict(sorted(counts.items())) != payload.get("disposition_counts"):
        raise ReviewApplyError("Disposition counts do not match rows.")
    return {
        "candidate_count": len(rows),
        "approved_clean_count": counts["approved_clean"],
        "cleanup_required_count": counts["cleanup_required"],
        "boundary_repair_count": counts["boundary_repair_required"],
        "incomplete_review_count": counts["incomplete_review"],
        "rejected_count": counts["rejected_by_reviewer"],
        "blocked_approval_count": counts["blocked_approval"],
        "mine_nearby_count": counts["mine_nearby_requested"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply a reviewed three-voice source atlas without production promotion.")
    sub = parser.add_subparsers(dest="command", required=True)
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--atlas", required=True)
    apply_parser.add_argument("--review", required=True)
    apply_parser.add_argument("--output", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--applied", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "apply":
            atlas_path = Path(args.atlas).expanduser().resolve()
            review_path = Path(args.review).expanduser().resolve()
            payload = build_applied(
                load_json(atlas_path),
                load_json(review_path),
                atlas_path=atlas_path,
                review_path=review_path,
            )
            output = Path(args.output).expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            result = {**validate_applied(payload), "output": str(output)}
        else:
            result = validate_applied(load_json(Path(args.applied).expanduser().resolve()))
    except ReviewApplyError as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
