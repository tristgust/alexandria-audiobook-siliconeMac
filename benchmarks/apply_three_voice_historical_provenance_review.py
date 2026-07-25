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
    FinalBankError,
    copy_reference_audio,
    gap_assessment,
    load_json,
    rows_by_id,
    sha256_file,
    slug,
    validated_audio,
)

REVIEW_ROUND_ID = "alexandria_three_voice_historical_provenance_review_v1"
CORE_BANK_ROUND_ID = "alexandria_three_voice_validated_core_reference_bank_v1"
HISTORICAL_BANK_ROUND_ID = "alexandria_transcript_guided_source_isolation_v1"
FINAL_BANK_ROUND_ID = "alexandria_three_voice_validated_reference_bank_v2"
APPLIED_ROUND_ID = "alexandria_three_voice_historical_provenance_review_applied_v1"

ALLOWED_DECISIONS = {
    "approve_usable",
    "correct_speaker_unusable",
    "wrong_or_uncertain_speaker",
    "wrong_boundary",
    "locked_rejected_wrong_speaker",
}

EXPECTED_DECISION_COUNTS = {
    "approve_usable": 10,
    "correct_speaker_unusable": 2,
    "wrong_or_uncertain_speaker": 0,
    "wrong_boundary": 1,
    "locked_rejected_wrong_speaker": 1,
}

BOUNDARY_REPAIR_SPECS = {
    "benny_hesitation_fatalistic_dread": {
        "suggested_start_seconds": 1272.44,
        "suggested_end_seconds": 1277.14,
        "word_timed_start_seconds": 1272.46,
        "word_timed_end_seconds": 1277.10,
        "reason": (
            "The reviewed cut begins about 120 ms before the first word and ends about "
            "180 ms after the final word. Recut with a 20 ms entrance pad and 40 ms exit pad, "
            "then require one final listen before inclusion."
        ),
    }
}


class ProvenanceApplyError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def require_round(payload: Any, expected: str, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProvenanceApplyError(f"{label} must be a JSON object.")
    if payload.get("round_id") != expected:
        raise ProvenanceApplyError(
            f"Unexpected {label} round_id: {payload.get('round_id')!r}; expected {expected!r}."
        )
    return payload


def unwrap_review(payload: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ProvenanceApplyError("Normalized review must be a JSON object.")
    if "review" in payload:
        review = payload.get("review")
        metadata = {
            "source_upload_name": payload.get("source_upload_name"),
            "source_upload_sha256": payload.get("source_upload_sha256"),
        }
    else:
        review = payload
        metadata = {"source_upload_name": None, "source_upload_sha256": None}
    return require_round(review, REVIEW_ROUND_ID, "provenance review"), metadata


def verify_summary(review: dict[str, Any], rows: dict[str, dict[str, Any]]) -> Counter[str]:
    summary = review.get("summary")
    if not isinstance(summary, dict):
        raise ProvenanceApplyError("Review summary is missing.")
    counts = Counter(str(row.get("decision")) for row in rows.values())
    for decision, expected in EXPECTED_DECISION_COUNTS.items():
        if counts[decision] != expected:
            raise ProvenanceApplyError(
                f"Unexpected {decision} count: {counts[decision]}; expected {expected}."
            )
    expected_summary = {
        "candidate_count": len(rows),
        "actionable_count": len(rows) - counts["locked_rejected_wrong_speaker"],
        "warning_count": counts["locked_rejected_wrong_speaker"],
        "complete_count": len(rows) - counts["locked_rejected_wrong_speaker"],
        "approved_usable_count": counts["approve_usable"],
        "correct_speaker_unusable_count": counts["correct_speaker_unusable"],
        "wrong_or_uncertain_speaker_count": counts["wrong_or_uncertain_speaker"],
        "wrong_boundary_count": counts["wrong_boundary"],
        "locked_wrong_speaker_count": counts["locked_rejected_wrong_speaker"],
    }
    for key, expected in expected_summary.items():
        if summary.get(key) != expected:
            raise ProvenanceApplyError(
                f"Review summary mismatch for {key}: {summary.get(key)!r} != {expected!r}."
            )
    return counts


def verify_review_against_source(
    review_rows: dict[str, dict[str, Any]],
    historical_rows: dict[str, dict[str, Any]],
) -> None:
    if set(review_rows) != set(historical_rows):
        raise ProvenanceApplyError(
            "Review/source candidate mismatch: "
            f"review={sorted(review_rows)}, source={sorted(historical_rows)}"
        )
    for clip_id, reviewed in review_rows.items():
        source = historical_rows[clip_id]
        decision = reviewed.get("decision")
        if decision not in ALLOWED_DECISIONS:
            raise ProvenanceApplyError(f"Invalid decision for {clip_id}: {decision!r}")
        stable_pairs = {
            "target": source.get("target"),
            "target_label": source.get("target_label"),
            "selected_transcript": source.get("transcript"),
            "selected_start_seconds": source.get("audio_start_seconds"),
            "selected_end_seconds": source.get("audio_end_seconds"),
            "assistant_speaker_role": source.get("speaker_role"),
            "assistant_primary_emotion": source.get("primary_emotion"),
            "assistant_secondary_emotion": source.get("secondary_emotion"),
            "assistant_dramatic_function": source.get("dramatic_function"),
            "assistant_intensity_1_to_5": source.get("intensity_1_to_5"),
        }
        for field, expected in stable_pairs.items():
            observed = reviewed.get(field)
            if isinstance(expected, float):
                if observed is None or abs(float(observed) - expected) > 0.0005:
                    raise ProvenanceApplyError(
                        f"Review changed {field} for {clip_id}: {observed!r} != {expected!r}"
                    )
            elif observed != expected:
                raise ProvenanceApplyError(
                    f"Review changed {field} for {clip_id}: {observed!r} != {expected!r}"
                )
        if decision == "locked_rejected_wrong_speaker":
            if reviewed.get("locked") is not True or reviewed.get("warning_only") is not True:
                raise ProvenanceApplyError(f"Locked wrong-speaker evidence is malformed: {clip_id}")
            if reviewed.get("known_disposition") != "rejected_wrong_speaker":
                raise ProvenanceApplyError(f"Wrong known disposition for {clip_id}")
        else:
            if reviewed.get("locked") is not False or reviewed.get("warning_only") is not False:
                raise ProvenanceApplyError(f"Actionable review row is unexpectedly locked: {clip_id}")
            if not reviewed.get("updated_at") or int(reviewed.get("revision") or 0) < 1:
                raise ProvenanceApplyError(f"Actionable decision lacks durable timestamp/revision: {clip_id}")


def canonical_approved_historical_reference(
    source: dict[str, Any],
    reviewed: dict[str, Any],
    *,
    source_audio: Path,
    destination: Path,
    destination_hash: str,
) -> dict[str, Any]:
    return {
        "clip_id": source["clip_id"],
        "target": source.get("target"),
        "target_label": source.get("target_label"),
        "source_title": reviewed.get("source_title") or source.get("source_key"),
        "source_kind": "transcript_guided_source_human_validated",
        "youtube_id": None,
        "selected_transcript": source.get("transcript"),
        "primary_emotion": source.get("primary_emotion"),
        "secondary_emotion": source.get("secondary_emotion"),
        "dramatic_function": source.get("dramatic_function"),
        "intensity_1_to_5": source.get("intensity_1_to_5"),
        "coverage_family": slug(str(source.get("primary_emotion") or "")),
        "speaker_certainty": "explicitly_human_approved",
        "speaker_role": source.get("speaker_role"),
        "source_audio_sha256": source.get("source_sha256"),
        "source_reference_audio_sha256": sha256_file(source_audio),
        "audio_path": str(destination),
        "audio_sha256": destination_hash,
        "reference_status": "approved_source_reference_human_validated",
        "provenance": "historical_provenance_review",
        "review_decision": reviewed.get("decision"),
        "review_notes": reviewed.get("notes"),
        "review_revision": reviewed.get("revision"),
        "review_updated_at": reviewed.get("updated_at"),
        "production_promotion_allowed": False,
    }


def copy_core_reference(
    row: dict[str, Any],
    output_root: Path,
) -> dict[str, Any]:
    clip_id = str(row.get("clip_id") or "")
    target = str(row.get("target") or "")
    source = validated_audio(
        row.get("audio_path"), row.get("audio_sha256"), f"core:{clip_id}", require_bank_format=True
    )
    destination, digest = copy_reference_audio(source, output_root, target, clip_id)
    return {
        **row,
        "source_reference_audio_sha256": sha256_file(source),
        "audio_path": str(destination),
        "audio_sha256": digest,
        "production_promotion_allowed": False,
    }


def build_follow_up(
    source: dict[str, Any],
    reviewed: dict[str, Any],
) -> dict[str, Any]:
    clip_id = source["clip_id"]
    common = {
        "clip_id": clip_id,
        "target": source.get("target"),
        "target_label": source.get("target_label"),
        "source_title": reviewed.get("source_title") or source.get("source_key"),
        "selected_transcript": source.get("transcript"),
        "selected_start_seconds": source.get("audio_start_seconds"),
        "selected_end_seconds": source.get("audio_end_seconds"),
        "primary_emotion": source.get("primary_emotion"),
        "dramatic_function": source.get("dramatic_function"),
        "source_audio": source.get("source_path"),
        "source_audio_sha256": source.get("source_sha256"),
        "candidate_audio": source.get("audio_path"),
        "candidate_audio_sha256": source.get("audio_sha256"),
        "review_decision": reviewed.get("decision"),
        "review_notes": reviewed.get("notes"),
        "review_updated_at": reviewed.get("updated_at"),
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    if reviewed.get("decision") == "wrong_boundary":
        spec = BOUNDARY_REPAIR_SPECS.get(clip_id)
        if spec is None:
            raise ProvenanceApplyError(f"No bounded repair specification exists for {clip_id}")
        return {
            **common,
            "follow_up_type": "boundary_recut",
            "disposition": "one_recut_then_human_confirmation",
            **spec,
        }
    notes = str(reviewed.get("notes") or "").casefold()
    if clip_id == "benny_hesitation_protective_reassurance" or "someone elses voice" in notes:
        return {
            **common,
            "follow_up_type": "replacement_source_required",
            "disposition": "exclude_role_contaminated_clip",
            "reason": (
                "The clip contains Benny performing another person's voice. Signal cleanup cannot "
                "restore a stable Benny performance, so do not attempt destructive cleanup."
            ),
        }
    return {
        **common,
        "follow_up_type": "source_cleanup",
        "disposition": "one_bounded_cleanup_attempt_then_stop",
        "reason": (
            "The reviewer confirmed the Seventh Doctor speaker but rejected the present audio as "
            "insufficiently clean. Permit one blinded source-separation comparison; abandon the clip "
            "if no candidate is clean and faithful."
        ),
    }


def apply_review(
    *,
    core_bank: dict[str, Any],
    historical_bank: dict[str, Any],
    normalized_review: dict[str, Any],
    output_root: Path,
    source_paths: dict[str, Path],
) -> tuple[dict[str, Any], dict[str, Any]]:
    core = require_round(core_bank, CORE_BANK_ROUND_ID, "validated core bank")
    historical = require_round(historical_bank, HISTORICAL_BANK_ROUND_ID, "historical source bank")
    review, upload_metadata = unwrap_review(normalized_review)

    core_rows = core.get("references")
    if not isinstance(core_rows, list) or len(core_rows) != int(core.get("reference_count") or 0):
        raise ProvenanceApplyError("Validated core bank references are missing or incomplete.")
    historical_rows = rows_by_id(
        historical.get("accepted_candidates"), key="clip_id", label="historical source bank"
    )
    review_rows = rows_by_id(review.get("rows"), key="clip_id", label="provenance review")
    verify_review_against_source(review_rows, historical_rows)
    decision_counts = verify_summary(review, review_rows)

    pending_ids = {
        str(row.get("clip_id")) for row in core.get("pending_historical_candidates") or []
    }
    if pending_ids != set(historical_rows):
        raise ProvenanceApplyError(
            f"Core pending-candidate set is inconsistent: {sorted(pending_ids)} != {sorted(historical_rows)}"
        )

    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    references = [copy_core_reference(row, output_root) for row in core_rows]
    approved_rows: list[dict[str, Any]] = []
    follow_up_queue: list[dict[str, Any]] = []
    rejected_sources = list(core.get("rejected_sources") or [])
    dispositions: list[dict[str, Any]] = []

    for clip_id in sorted(review_rows):
        reviewed = review_rows[clip_id]
        source = historical_rows[clip_id]
        decision = reviewed["decision"]
        if decision == "approve_usable":
            source_audio = validated_audio(
                source.get("audio_path"), source.get("audio_sha256"), f"historical:{clip_id}"
            )
            destination, digest = copy_reference_audio(
                source_audio, output_root, str(source.get("target") or ""), clip_id
            )
            canonical = canonical_approved_historical_reference(
                source,
                reviewed,
                source_audio=source_audio,
                destination=destination,
                destination_hash=digest,
            )
            references.append(canonical)
            approved_rows.append(canonical)
            dispositions.append({"clip_id": clip_id, "decision": decision, "included": True})
        elif decision in {"wrong_boundary", "correct_speaker_unusable"}:
            follow_up = build_follow_up(source, reviewed)
            follow_up_queue.append(follow_up)
            dispositions.append(
                {
                    "clip_id": clip_id,
                    "decision": decision,
                    "included": False,
                    "follow_up_type": follow_up["follow_up_type"],
                }
            )
        else:
            rejection = {
                "clip_id": clip_id,
                "target": source.get("target"),
                "source_title": reviewed.get("source_title") or source.get("source_key"),
                "rejection_reason": (
                    "human_rejected_wrong_speaker"
                    if decision == "locked_rejected_wrong_speaker"
                    else "human_rejected_wrong_or_uncertain_speaker"
                ),
                "review_notes": reviewed.get("notes") or reviewed.get("warning_reason"),
                "review_updated_at": reviewed.get("updated_at"),
            }
            rejected_sources.append(rejection)
            dispositions.append({"clip_id": clip_id, "decision": decision, "included": False})

    references.sort(key=lambda row: (str(row.get("target")), str(row.get("clip_id"))))
    ids = [row["clip_id"] for row in references]
    if len(ids) != len(set(ids)):
        raise ProvenanceApplyError("Final bank contains duplicate clip IDs.")
    counts = Counter(str(row.get("target")) for row in references)
    expected_counts = {"narrator": 16, "benny": 9, "doctor": 4}
    if dict(counts) != expected_counts:
        raise ProvenanceApplyError(f"Unexpected final target counts: {dict(counts)} != {expected_counts}")
    coverage = gap_assessment(references)

    bank = {
        "schema_version": 2,
        "round_id": FINAL_BANK_ROUND_ID,
        "created_at": now_iso(),
        "reference_count": len(references),
        "reference_counts_by_target": dict(sorted(counts.items())),
        "core_reference_count": len(core_rows),
        "newly_human_validated_reference_count": len(approved_rows),
        "pending_historical_candidate_count": 0,
        "coverage_assessment": coverage,
        "references": references,
        "follow_up_queue": follow_up_queue,
        "rejected_sources": rejected_sources,
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

    applied = {
        "schema_version": 1,
        "round_id": APPLIED_ROUND_ID,
        "created_at": now_iso(),
        "review_export": source_paths["normalized_review"].as_posix(),
        "review_export_sha256": sha256_file(source_paths["normalized_review"]),
        "source_upload": upload_metadata,
        "decision_counts": dict(sorted(decision_counts.items())),
        "approved_reference_count": len(approved_rows),
        "follow_up_count": len(follow_up_queue),
        "rejected_count": sum(
            row["decision"] in {"wrong_or_uncertain_speaker", "locked_rejected_wrong_speaker"}
            for row in dispositions
        ),
        "dispositions": dispositions,
        "approved_references": approved_rows,
        "follow_up_queue": follow_up_queue,
        "final_bank": str(bank_path),
        "final_bank_sha256": sha256_file(bank_path),
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    applied_path = output_root / "applied-provenance-review-ledger.json"
    applied_path.write_text(json.dumps(applied, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return bank, applied


def validate_bank(payload: Any) -> dict[str, Any]:
    bank = require_round(payload, FINAL_BANK_ROUND_ID, "validated reference bank")
    rows = bank.get("references")
    if not isinstance(rows, list) or len(rows) != int(bank.get("reference_count") or 0):
        raise ProvenanceApplyError("Final bank reference count mismatch.")
    failures: list[str] = []
    counts: Counter[str] = Counter()
    seen: set[str] = set()
    for row in rows:
        clip_id = str(row.get("clip_id") or "")
        if not clip_id or clip_id in seen:
            failures.append(f"clip_id:{clip_id}")
            continue
        seen.add(clip_id)
        counts[str(row.get("target"))] += 1
        try:
            validated_audio(
                row.get("audio_path"), row.get("audio_sha256"), f"final:{clip_id}", require_bank_format=True
            )
        except FinalBankError as exc:
            failures.append(str(exc))
        for field in (
            "target",
            "target_label",
            "selected_transcript",
            "primary_emotion",
            "dramatic_function",
            "reference_status",
            "provenance",
        ):
            if row.get(field) in (None, ""):
                failures.append(f"field:{clip_id}:{field}")
        if row.get("production_promotion_allowed") is not False:
            failures.append(f"promotion:{clip_id}")
    expected_counts = {"benny": 9, "doctor": 4, "narrator": 16}
    if dict(sorted(counts.items())) != expected_counts:
        failures.append(f"counts:{dict(counts)}")
    if bank.get("pending_historical_candidate_count") != 0:
        failures.append("pending_historical_candidate_count")
    if len(bank.get("follow_up_queue") or []) != 3:
        failures.append("follow_up_queue")
    if bank.get("automatic_production_assignment") is not False:
        failures.append("automatic_production_assignment")
    if bank.get("production_promotion_allowed") is not False:
        failures.append("production_promotion_allowed")
    if failures:
        raise ProvenanceApplyError(f"Validated bank failed: {failures}")
    open_gaps = {
        target: [row["function"] for row in detail["requirements"] if row["status"] == "open_gap"]
        for target, detail in bank.get("coverage_assessment", {}).items()
    }
    return {
        "reference_count": len(rows),
        "reference_counts_by_target": dict(sorted(counts.items())),
        "newly_human_validated_reference_count": bank.get("newly_human_validated_reference_count"),
        "follow_up_count": len(bank.get("follow_up_queue") or []),
        "open_gaps": open_gaps,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply the strict Benny/Doctor historical provenance review to the validated core bank."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--core-bank", required=True)
    apply_parser.add_argument("--historical-bank", required=True)
    apply_parser.add_argument("--normalized-review", required=True)
    apply_parser.add_argument("--output-root", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--bank", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "apply":
            source_paths = {
                "validated_core_bank": Path(args.core_bank).expanduser().resolve(),
                "historical_source_bank": Path(args.historical_bank).expanduser().resolve(),
                "normalized_review": Path(args.normalized_review).expanduser().resolve(),
            }
            bank, _applied = apply_review(
                core_bank=load_json(source_paths["validated_core_bank"]),
                historical_bank=load_json(source_paths["historical_source_bank"]),
                normalized_review=load_json(source_paths["normalized_review"]),
                output_root=Path(args.output_root).expanduser().resolve(),
                source_paths=source_paths,
            )
            result = {
                **validate_bank(bank),
                "output": str(
                    Path(args.output_root).expanduser().resolve()
                    / "three-voice-validated-reference-bank.json"
                ),
            }
        else:
            result = validate_bank(load_json(Path(args.bank).expanduser().resolve()))
    except (ProvenanceApplyError, FinalBankError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
