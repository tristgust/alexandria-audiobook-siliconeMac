#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
ROUND_ID = "alexandria_original_sin_overlap_identity_salvage_round_v6"
DEFAULT_REVIEW = ROOT / "benchmarks/original_sin_overlap_identity_salvage_round_v6_review.json"
DEFAULT_ANSWER = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665/external_workflows/big_finish_overlap_reference_v1/"
    "overlap_identity_salvage_round_v6/private/answer-key.json"
)
DEFAULT_OUTPUT = ROOT / "benchmarks/original_sin_overlap_identity_salvage_round_v6_decision.json"
SCORE_FIELDS = ("identity", "cleanliness", "naturalness", "intelligibility", "contamination")


class DecisionError(RuntimeError):
    pass


def read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DecisionError(f"{label} could not be read: {exc}") from exc


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalized_review(value: Mapping[str, Any]) -> dict[str, Any]:
    scores: dict[str, int | None] = {}
    for field in SCORE_FIELDS:
        raw = value.get(field)
        try:
            score = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            score = None
        scores[field] = score if score in {1, 2, 3, 4, 5} else None
    return {
        **dict(value),
        "scores": scores,
        "all_scores_present": all(score is not None for score in scores.values()),
        "complete": value.get("completeness") == "complete",
        "operator_pass": value.get("decision") == "pass",
    }


def main() -> int:
    review = read_json(DEFAULT_REVIEW, "Identity-salvage review")
    answer = read_json(DEFAULT_ANSWER, "Identity-salvage answer key")
    if review.get("round_id") != ROUND_ID or answer.get("round_id") != ROUND_ID:
        raise DecisionError("Review or answer key belongs to another round.")
    results = review.get("results")
    candidates = answer.get("candidates")
    if not isinstance(results, Mapping) or not isinstance(candidates, Mapping):
        raise DecisionError("Review or answer key has an invalid candidate mapping.")
    if set(results) - set(candidates):
        raise DecisionError("Review includes a candidate outside the answer key.")

    reviewed = {
        candidate_id: normalized_review(value)
        for candidate_id, value in results.items()
        if isinstance(value, Mapping)
    }
    doc_id = "89773ee3454a2cbf"
    doc_row = candidates[doc_id]
    doc_review = reviewed[doc_id]
    if not (
        doc_review["operator_pass"]
        and doc_review["complete"]
        and doc_review["all_scores_present"]
        and min(doc_review["scores"].values()) == 5
    ):
        raise DecisionError("Doc Dantalion winner contract changed.")

    shythe_id = "5ad130953556d32b"
    shythe_review = reviewed[shythe_id]
    if not shythe_review["operator_pass"] or not shythe_review["complete"]:
        raise DecisionError("Expected Shythe provisional pass changed.")
    if shythe_review["all_scores_present"]:
        raise DecisionError("Shythe review unexpectedly contains all required scores.")

    selected = {
        "DOC DANTALION": {
            "candidate_id": doc_id,
            "character": doc_row["character"],
            "book_speaker": doc_row["book_speaker"],
            "treatment": doc_row["treatment"],
            "scores": doc_review["scores"],
            "audio_path": doc_row["audio_path"],
            "audio_sha256": doc_row["audio"]["sha256"],
            "transcript": doc_row["transcript"],
            "status": "identity_source_approved_pending_generated_mode_review",
        }
    }
    pending_completion = {
        "SHYTHE SHAHID": {
            "candidate_id": shythe_id,
            "character": candidates[shythe_id]["character"],
            "known_scores": shythe_review["scores"],
            "missing_scores": [
                field for field, score in shythe_review["scores"].items() if score is None
            ],
            "status": "operator_pass_fail_closed_missing_required_scores",
        }
    }
    unsupported = {
        "HOMELESS FORSAKEN": {
            "status": "no_approved_identity_candidate",
            "reason": "Both objectively retained candidates were marked incomplete and failed.",
        }
    }
    decision = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "closed_at_utc": utc_now(),
        "review_exported_at": review.get("exported_at"),
        "reviewed_candidate_count": len(results),
        "approved_identity_count": len(selected),
        "pending_completion_review_count": len(pending_completion),
        "unsupported_identity_count": len(unsupported),
        "selected": selected,
        "pending_completion_review": pending_completion,
        "unsupported": unsupported,
        "fail_closed_rules": {
            "all_scores_required": True,
            "explicit_complete_line_required": True,
            "written_notes_override_pass": True,
            "identity_source_approval_does_not_complete_generated_mode_coverage": True,
        },
        "production_changes": False,
    }
    DEFAULT_OUTPUT.write_text(
        json.dumps(decision, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(DEFAULT_OUTPUT),
                "approved": sorted(selected),
                "pending_completion": sorted(pending_completion),
                "unsupported": sorted(unsupported),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
