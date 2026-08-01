#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
ROUND_ID = "alexandria_original_sin_homeless_identity_transfer_round_v1"
REVIEW = ROOT / "benchmarks/original_sin_homeless_identity_transfer_round_v1_review.json"
ANSWER = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665/external_workflows/big_finish_overlap_reference_v1/"
    "homeless_identity_transfer_round_v1/private/answer-key.json"
)
OUTPUT = ROOT / "benchmarks/original_sin_homeless_identity_transfer_round_v1_decision.json"
SCORE_FIELDS = ("identity", "delivery", "naturalness", "intelligibility", "effects")
WINNER = "e883b934a1bdb7f3"
ALTERNATE = "dbb22db6e3fb92d3"


class DecisionError(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(value: Mapping[str, Any]) -> dict[str, Any]:
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
    results = review["results"]
    candidates = answer["candidates"]
    reviewed = {candidate_id: normalize(value) for candidate_id, value in results.items()}
    winner_row = candidates[WINNER]
    winner_review = reviewed[WINNER]
    alternate_row = candidates[ALTERNATE]
    alternate_review = reviewed[ALTERNATE]
    for candidate_id, row, review_row in (
        (WINNER, winner_row, winner_review),
        (ALTERNATE, alternate_row, alternate_review),
    ):
        if row.get("identity_source_kind") != "adaptation":
            raise DecisionError(f"Selected candidate no longer uses adaptation identity: {candidate_id}")
        if not (review_row["operator_pass"] and review_row["complete"] and review_row["all_scores_present"]):
            raise DecisionError(f"Selected candidate is incomplete: {candidate_id}")
    decision = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "closed_at_utc": utc_now(),
        "review_exported_at": review.get("exported_at"),
        "answer_candidate_count": len(candidates),
        "reviewed_candidate_count": len(results),
        "unreviewed_candidate_ids": sorted(set(candidates) - set(results)),
        "selected": {
            "candidate_id": WINNER,
            "backend": winner_row["backend"],
            "identity_source_kind": winner_row["identity_source_kind"],
            "approval_tier": "restricted_user_accepted",
            "scores": winner_review["scores"],
            "audio_path": winner_row["audio_path"],
            "audio_sha256": winner_row["audio"]["sha256"],
            "status": "clean_generated_identity_transfer_approved",
        },
        "approved_alternate": {
            "candidate_id": ALTERNATE,
            "backend": alternate_row["backend"],
            "identity_source_kind": alternate_row["identity_source_kind"],
            "approval_tier": "restricted_user_accepted",
            "scores": alternate_review["scores"],
            "status": "approved_evidence_not_default_route",
        },
        "source_audio_status": "context_only_not_directly_approved",
        "fail_closed_rules": {
            "unreviewed_candidates_fail": True,
            "generated_transfer_may_be_approved_without_approving_noisy_source_audio": True,
            "identity_or_effect_score_below_four_requires_restricted_tier": True,
        },
        "production_changes": False,
    }
    OUTPUT.write_text(json.dumps(decision, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "winner": WINNER, "alternate": ALTERNATE, "unreviewed": decision["unreviewed_candidate_ids"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
