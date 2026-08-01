#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
ROUND_ID = "alexandria_original_sin_overlap_character_repairs_round_v4"
REVIEW = ROOT / "benchmarks/original_sin_overlap_character_repairs_round_v4_review.json"
ANSWER = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665/external_workflows/big_finish_overlap_reference_v1/"
    "overlap_character_repairs_round_v4/private/answer-key.json"
)
OUTPUT = ROOT / "benchmarks/original_sin_overlap_character_repairs_round_v4_decision.json"
SCORE_FIELDS = ("identity", "delivery", "naturalness", "intelligibility", "effects")

WINNERS = {
    "doctor_weary_moral_gravity_repair": "e152df4f61b9f377",
    "roz_dry_banter_repair": "3b5872d15032a247",
    "computer_processing_repair": "23f4ff9b9f37f040",
}

RESTRICTED_WINNERS = {"roz_dry_banter_repair"}

ALTERNATES = {
    "computer_processing_repair": [
        "13ea660a57900658",
        "86c47449c5aefb80",
        "1e7ae0144cba4061",
    ]
}

PENDING_COMPLETION = {
    "dantalion_dry_sardonic": "0dff7471f2e22ead",
}

REPAIR_REQUIRED = {
    "doctor_urgent_discovery_repair": "All reviewed candidates failed operator acceptance.",
    "dantalion_sharp_irritation": "All reviewed candidates failed operator acceptance.",
}


class DecisionError(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_review(value: Mapping[str, Any]) -> dict[str, Any]:
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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> int:
    review = read_json(REVIEW)
    answer = read_json(ANSWER)
    if review.get("round_id") != ROUND_ID or answer.get("round_id") != ROUND_ID:
        raise DecisionError("Review or answer belongs to another round.")
    results = review.get("results")
    candidates = answer.get("candidates")
    modes = answer.get("modes")
    if not isinstance(results, Mapping) or not isinstance(candidates, Mapping):
        raise DecisionError("Invalid review or answer candidate mapping.")
    reviewed = {
        candidate_id: normalize_review(value)
        for candidate_id, value in results.items()
        if isinstance(value, Mapping)
    }

    selected: dict[str, dict[str, Any]] = {}
    for mode_id, candidate_id in WINNERS.items():
        row = candidates[candidate_id]
        review_row = reviewed[candidate_id]
        if row["mode_id"] != mode_id:
            raise DecisionError(f"Winner belongs to another mode: {candidate_id}")
        if not (review_row["operator_pass"] and review_row["complete"] and review_row["all_scores_present"]):
            raise DecisionError(f"Winner is not review-complete: {candidate_id}")
        tier = "restricted_user_accepted" if mode_id in RESTRICTED_WINNERS else "strict"
        scores = review_row["scores"]
        if tier == "strict" and min(scores.values()) < 4:
            raise DecisionError(f"Strict winner scored below four: {candidate_id}")
        if tier == "restricted_user_accepted" and min(scores.values()) < 3:
            raise DecisionError(f"Restricted winner scored below three: {candidate_id}")
        selected[mode_id] = {
            "mode_id": mode_id,
            "candidate_id": candidate_id,
            "backend": row["backend"],
            "approval_tier": tier,
            "scores": scores,
            "notes": review_row.get("notes"),
            "audio_path": row["audio_path"],
            "audio_sha256": row["audio"]["sha256"],
            "text": row["text"],
            "instruct": row["instruct"],
            "effect_processing": row.get("effect_processing"),
            "source_candidate_id": row.get("source_candidate_id"),
        }

    alternates: dict[str, list[dict[str, Any]]] = {}
    for mode_id, candidate_ids in ALTERNATES.items():
        rows = []
        for candidate_id in candidate_ids:
            row = candidates[candidate_id]
            review_row = reviewed[candidate_id]
            if not (review_row["operator_pass"] and review_row["complete"] and review_row["all_scores_present"]):
                raise DecisionError(f"Alternate is not review-complete: {candidate_id}")
            rows.append(
                {
                    "candidate_id": candidate_id,
                    "backend": row["backend"],
                    "scores": review_row["scores"],
                    "effect_processing": row.get("effect_processing"),
                    "status": "approved_evidence_not_default_route",
                }
            )
        alternates[mode_id] = rows

    pending_completion = {}
    for mode_id, candidate_id in PENDING_COMPLETION.items():
        row = candidates[candidate_id]
        review_row = reviewed[candidate_id]
        if not review_row["operator_pass"] or not review_row["complete"]:
            raise DecisionError(f"Expected provisional pass changed: {candidate_id}")
        if review_row["all_scores_present"]:
            raise DecisionError(f"Expected missing scores changed: {candidate_id}")
        pending_completion[mode_id] = {
            "candidate_id": candidate_id,
            "backend": row["backend"],
            "missing_scores": [
                field for field, score in review_row["scores"].items() if score is None
            ],
            "status": "operator_pass_fail_closed_missing_required_scores",
        }

    repair_required = {
        mode_id: {"mode_id": mode_id, "status": "repair_required", "reason": reason}
        for mode_id, reason in REPAIR_REQUIRED.items()
    }
    decision = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "closed_at_utc": utc_now(),
        "review_exported_at": review.get("exported_at"),
        "answer_candidate_count": len(candidates),
        "reviewed_candidate_count": len(results),
        "unreviewed_candidate_ids": sorted(set(candidates) - set(results)),
        "selected_mode_count": len(selected),
        "strict_winner_count": sum(row["approval_tier"] == "strict" for row in selected.values()),
        "restricted_winner_count": sum(row["approval_tier"] == "restricted_user_accepted" for row in selected.values()),
        "selected": selected,
        "approved_alternates": alternates,
        "pending_completion_review": pending_completion,
        "repair_required_modes": repair_required,
        "fail_closed_rules": {
            "unreviewed_candidates_fail": True,
            "all_scores_required": True,
            "explicit_complete_line_required": True,
            "written_notes_override_pass": True,
        },
        "production_changes": False,
    }
    OUTPUT.write_text(json.dumps(decision, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "selected": sorted(selected), "pending_completion": sorted(pending_completion), "repair_required": sorted(repair_required)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
