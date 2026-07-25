#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SALVAGE_ROUND_ID = "alexandria_three_voice_final_salvage_v1"
REVIEW_ROUND_ID = "alexandria_three_voice_final_salvage_review_v1"
APPLIED_ROUND_ID = "alexandria_three_voice_final_salvage_applied_v1"
PRIOR_ROUND_ID = "alexandria_three_voice_source_repair_review_applied_v1"


class FinalSalvageApplyError(RuntimeError):
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
        raise FinalSalvageApplyError(f"JSON file is missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FinalSalvageApplyError(f"Invalid JSON in {path}: {exc}") from exc


def rows_by_id(payload: Any, *, round_id: str, id_key: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise FinalSalvageApplyError(f"{label} must be a JSON object.")
    if payload.get("round_id") != round_id:
        raise FinalSalvageApplyError(
            f"Unexpected {label} round_id: {payload.get('round_id')!r}; expected {round_id!r}."
        )
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise FinalSalvageApplyError(f"{label} must contain a non-empty rows list.")
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = row.get(id_key)
        if not isinstance(value, str) or not value.strip():
            raise FinalSalvageApplyError(f"Every {label} row requires {id_key}.")
        value = value.strip()
        if value in indexed:
            raise FinalSalvageApplyError(f"Duplicate {label} {id_key}: {value}")
        indexed[value] = row
    return indexed


def answer_rows(answer_payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(answer_payload, list) or not answer_payload:
        raise FinalSalvageApplyError("Answer key must contain a non-empty list.")
    indexed: dict[str, dict[str, Any]] = {}
    for row in answer_payload:
        card_id = row.get("card_id")
        if not isinstance(card_id, str) or not card_id.strip():
            raise FinalSalvageApplyError("Every answer-key row requires card_id.")
        if card_id in indexed:
            raise FinalSalvageApplyError(f"Duplicate answer-key card_id: {card_id}")
        indexed[card_id] = row
    return indexed


def candidate_for_decision(answer: dict[str, Any], decision: str) -> dict[str, Any]:
    if not decision.startswith("candidate_"):
        raise FinalSalvageApplyError(f"Not a candidate decision: {decision}")
    label = decision.removeprefix("candidate_")
    for candidate in answer.get("candidates") or []:
        if candidate.get("candidate_label") == label:
            return candidate
    raise FinalSalvageApplyError(f"Candidate {label} does not exist for {answer.get('card_id')}")


def requires_refinement(review: dict[str, Any]) -> bool:
    notes = str(review.get("notes") or "").lower()
    markers = (
        "if refined",
        "still hear",
        "sound effect",
        "page turn",
        "background",
        "artifact",
    )
    return any(marker in notes for marker in markers)


def validated_reference_from_boundary(answer: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    final = answer.get("final") or {}
    audio = Path(str(final.get("audio_path") or ""))
    if not audio.is_file() or sha256_file(audio) != final.get("audio_sha256"):
        raise FinalSalvageApplyError(f"Final boundary audio failed validation for {answer.get('clip_id')}")
    return {
        "clip_id": answer["clip_id"],
        "target": answer["target"],
        "target_label": answer["target_label"],
        "source_title": answer["source_title"],
        "audio_path": str(audio),
        "audio_sha256": final["audio_sha256"],
        "selected_start_seconds": answer.get("absolute_start_seconds"),
        "selected_end_seconds": answer.get("absolute_end_seconds"),
        "selected_transcript": answer["selected_transcript"],
        "primary_emotion": answer["primary_emotion"],
        "dramatic_function": answer["dramatic_function"],
        "reference_status": "approved_final_boundary",
        "review_decision": review.get("decision"),
        "review_notes": review.get("notes") or None,
        "review_updated_at": review.get("updated_at"),
        "production_promotion_allowed": False,
    }


def validated_reference_from_candidate(answer: dict[str, Any], review: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    audio = Path(str(candidate.get("audio_path") or ""))
    if not audio.is_file() or sha256_file(audio) != candidate.get("audio_sha256"):
        raise FinalSalvageApplyError(f"Selected separation audio failed validation for {answer.get('clip_id')}")
    return {
        "clip_id": answer["clip_id"],
        "target": answer["target"],
        "target_label": answer["target_label"],
        "source_title": answer["source_title"],
        "audio_path": str(audio),
        "audio_sha256": candidate["audio_sha256"],
        "selected_transcript": answer["selected_transcript"],
        "primary_emotion": answer["primary_emotion"],
        "dramatic_function": answer["dramatic_function"],
        "reference_status": "approved_source_separation",
        "separation_model_key": candidate.get("model_key"),
        "separation_model_filename": candidate.get("model_filename"),
        "review_decision": review.get("decision"),
        "review_notes": review.get("notes") or None,
        "review_updated_at": review.get("updated_at"),
        "production_promotion_allowed": False,
    }


def normalize_prior_reference(row: dict[str, Any]) -> dict[str, Any]:
    path_value = row.get("audio_path") or row.get("repaired_audio_path")
    hash_value = row.get("audio_sha256") or row.get("repaired_audio_sha256")
    audio = Path(str(path_value or ""))
    if not audio.is_file() or sha256_file(audio) != hash_value:
        raise FinalSalvageApplyError(f"Prior reference audio failed validation for {row.get('clip_id')}")
    return {
        **row,
        "audio_path": str(audio),
        "audio_sha256": hash_value,
        "production_promotion_allowed": False,
    }


def build_applied(
    salvage_payload: Any,
    answer_payload: Any,
    review_payload: Any,
    prior_payload: Any,
    *,
    salvage_path: Path,
    answer_path: Path,
    review_path: Path,
    prior_path: Path,
) -> dict[str, Any]:
    salvage = rows_by_id(salvage_payload, round_id=SALVAGE_ROUND_ID, id_key="card_id", label="salvage manifest")
    answers = answer_rows(answer_payload)
    reviews = rows_by_id(review_payload, round_id=REVIEW_ROUND_ID, id_key="card_id", label="salvage review")
    if prior_payload.get("round_id") != PRIOR_ROUND_ID:
        raise FinalSalvageApplyError(f"Unexpected prior ledger round_id: {prior_payload.get('round_id')}")
    if set(salvage) != set(answers) or set(salvage) != set(reviews):
        raise FinalSalvageApplyError(
            f"Card mismatch: manifest={sorted(salvage)}, answers={sorted(answers)}, reviews={sorted(reviews)}"
        )

    raw_prior_references = list(prior_payload.get("validated_references") or [])
    if len(raw_prior_references) != int(prior_payload.get("validated_reference_count") or 0):
        raise FinalSalvageApplyError("Prior validated-reference count is inconsistent.")
    prior_references = [normalize_prior_reference(row) for row in raw_prior_references]
    validated_references = list(prior_references)
    refinement_queue: list[dict[str, Any]] = []
    rejected_sources: list[dict[str, Any]] = []
    dispositions: list[dict[str, Any]] = []

    for card_id in salvage:
        manifest_row = salvage[card_id]
        answer = answers[card_id]
        review = reviews[card_id]
        for key in ("card_type", "clip_id", "target", "selected_transcript"):
            review_value = review.get(key)
            manifest_value = manifest_row.get(key)
            if review_value not in (None, "") and review_value != manifest_value:
                raise FinalSalvageApplyError(
                    f"Review changed stable field {key} for {card_id}: {review_value!r} != {manifest_value!r}"
                )
        decision = str(review.get("decision") or "")
        if manifest_row["card_type"] == "boundary_final":
            if decision == "approve_final":
                reference = validated_reference_from_boundary(answer, review)
                validated_references.append(reference)
                disposition = "approved_final_boundary"
            elif decision == "boundary_wrong":
                disposition = "boundary_rejected"
            else:
                raise FinalSalvageApplyError(f"Invalid boundary decision for {card_id}: {decision}")
            dispositions.append({
                "card_id": card_id,
                "clip_id": manifest_row["clip_id"],
                "target": manifest_row["target"],
                "card_type": manifest_row["card_type"],
                "decision": decision,
                "disposition": disposition,
                "notes": review.get("notes") or None,
            })
            continue

        if decision == "none":
            rejected_sources.append({
                "clip_id": manifest_row["clip_id"],
                "target": manifest_row["target"],
                "source_title": manifest_row["source_title"],
                "rejection_reason": "no_separation_candidate_usable",
                "review_notes": review.get("notes") or None,
                "review_updated_at": review.get("updated_at"),
            })
            disposition = "rejected_no_usable_separation"
        elif decision.startswith("candidate_"):
            candidate = candidate_for_decision(answer, decision)
            selected = validated_reference_from_candidate(answer, review, candidate)
            if requires_refinement(review):
                refinement_queue.append({
                    **selected,
                    "reference_status": "selected_for_refinement",
                    "refinement_reason": review.get("notes") or "reviewer_requested_refinement",
                })
                disposition = "selected_for_refinement"
            else:
                validated_references.append(selected)
                disposition = "approved_source_separation"
        else:
            raise FinalSalvageApplyError(f"Invalid source-separation decision for {card_id}: {decision}")
        dispositions.append({
            "card_id": card_id,
            "clip_id": manifest_row["clip_id"],
            "target": manifest_row["target"],
            "card_type": manifest_row["card_type"],
            "decision": decision,
            "disposition": disposition,
            "notes": review.get("notes") or None,
        })

    ids = [row.get("clip_id") for row in validated_references]
    if len(ids) != len(set(ids)):
        raise FinalSalvageApplyError("Validated references contain duplicate clip IDs.")
    counts = Counter(row["disposition"] for row in dispositions)
    target_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in dispositions:
        target_counts[str(row["target"])][row["disposition"]] += 1
    validated_by_target = Counter(str(row.get("target")) for row in validated_references)
    return {
        "schema_version": 1,
        "round_id": APPLIED_ROUND_ID,
        "created_at": now_iso(),
        "salvage_manifest": {"path": str(salvage_path), "sha256": sha256_file(salvage_path)},
        "answer_key": {"path": str(answer_path), "sha256": sha256_file(answer_path)},
        "review_export": {
            "path": str(review_path),
            "sha256": sha256_file(review_path),
            "source_export_sha256": review_payload.get("source_export_sha256"),
            "exported_at": review_payload.get("exported_at"),
            "reported_summary": review_payload.get("summary"),
        },
        "prior_validated_ledger": {"path": str(prior_path), "sha256": sha256_file(prior_path)},
        "card_count": len(dispositions),
        "disposition_counts": dict(sorted(counts.items())),
        "target_disposition_counts": {
            target: dict(sorted(counter.items())) for target, counter in sorted(target_counts.items())
        },
        "prior_validated_reference_count": len(prior_references),
        "validated_reference_count": len(validated_references),
        "validated_reference_counts_by_target": dict(sorted(validated_by_target.items())),
        "validated_references": validated_references,
        "refinement_queue": refinement_queue,
        "rejected_sources": rejected_sources,
        "dispositions": dispositions,
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }


def validate_applied(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("round_id") != APPLIED_ROUND_ID:
        raise FinalSalvageApplyError("Applied salvage ledger has an unexpected round_id.")
    references = payload.get("validated_references")
    refinements = payload.get("refinement_queue")
    rejected = payload.get("rejected_sources")
    dispositions = payload.get("dispositions")
    if not all(isinstance(value, list) for value in (references, refinements, rejected, dispositions)):
        raise FinalSalvageApplyError("Applied salvage ledger lists are missing.")
    reference_ids = [row.get("clip_id") for row in references]
    if len(reference_ids) != len(set(reference_ids)):
        raise FinalSalvageApplyError("Applied salvage ledger has duplicate validated references.")
    for row in references + refinements:
        audio = Path(str(row.get("audio_path") or ""))
        if not audio.is_file() or sha256_file(audio) != row.get("audio_sha256"):
            raise FinalSalvageApplyError(f"Audio validation failed for {row.get('clip_id')}")
        if row.get("production_promotion_allowed") is not False:
            raise FinalSalvageApplyError("No source reference may auto-promote to production.")
    if len(references) != int(payload.get("validated_reference_count") or 0):
        raise FinalSalvageApplyError("Validated-reference count does not match.")
    counts = Counter(str(row.get("disposition")) for row in dispositions)
    if dict(sorted(counts.items())) != payload.get("disposition_counts"):
        raise FinalSalvageApplyError("Disposition counts do not match.")
    if payload.get("automatic_production_assignment") is not False:
        raise FinalSalvageApplyError("Automatic production assignment must remain disabled.")
    if payload.get("production_promotion_allowed") is not False:
        raise FinalSalvageApplyError("Production promotion must remain disabled.")
    return {
        "card_count": len(dispositions),
        "validated_reference_count": len(references),
        "refinement_count": len(refinements),
        "rejected_source_count": len(rejected),
        "disposition_counts": dict(sorted(counts.items())),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply the final three-voice salvage review without production promotion.")
    sub = parser.add_subparsers(dest="command", required=True)
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--salvage", required=True)
    apply_parser.add_argument("--answer-key", required=True)
    apply_parser.add_argument("--review", required=True)
    apply_parser.add_argument("--prior-ledger", required=True)
    apply_parser.add_argument("--output", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--applied", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "apply":
            salvage_path = Path(args.salvage).expanduser().resolve()
            answer_path = Path(args.answer_key).expanduser().resolve()
            review_path = Path(args.review).expanduser().resolve()
            prior_path = Path(args.prior_ledger).expanduser().resolve()
            payload = build_applied(
                load_json(salvage_path),
                load_json(answer_path),
                load_json(review_path),
                load_json(prior_path),
                salvage_path=salvage_path,
                answer_path=answer_path,
                review_path=review_path,
                prior_path=prior_path,
            )
            output = Path(args.output).expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            result = {**validate_applied(payload), "output": str(output)}
        else:
            result = validate_applied(load_json(Path(args.applied).expanduser().resolve()))
    except FinalSalvageApplyError as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
