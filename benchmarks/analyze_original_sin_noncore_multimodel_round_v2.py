#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
ROUND_ID = "alexandria_original_sin_noncore_multimodel_round_v2"
DEFAULT_REVIEW = ROOT / "benchmarks/original_sin_noncore_multimodel_round_v2_review.json"
DEFAULT_ANSWER = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665/external_workflows/big_finish_overlap_reference_v1/"
    "noncore_multimodel_round_v2/private/answer-key.json"
)
DEFAULT_OUTPUT = ROOT / "benchmarks/original_sin_noncore_multimodel_round_v2_decision.json"
SCORE_FIELDS = ("identity", "delivery", "naturalness", "intelligibility", "effects")


WINNERS = {
    "beltempest_interrogative_impatience": "54dda4f4b484a80d",
    "beltempest_military_volatility": "a858239e27a41a7f",
    "beltempest_weary_resignation": "d97515443eb86bb5",
    "beltempest_urgent_command": "30ebab19d5246127",
    "tobias_cultivated_menace": "7c5151013479ad79",
    "tobias_polished_probe": "99a52e4cd83a60e6",
    "zebulon_nervous_analysis": "3b7685534f6ec30f",
    # The higher-delivery Index candidate omitted the explicit completeness
    # decision, so the complete Vox candidate wins fail-closed.
    "zebulon_intense_questioning": "be97083cd4387e62",
    "hater_wounded_fury": "dd7940ac7b340e93",
    "karvellis_amplified_command": "2aac880c2e906ade",
    "lubineki_rough_jovial": "8e1465508de01b21",
    "powerless_panicked_urgency": "8a3712555e31617a",
    "rashid_tired_authority": "b1c47d8d83b5b526",
    "under_sergeant_military_menace": "7021a9e54fb3ca99",
}

RESTRICTED_WINNERS = frozenset(
    {
        "powerless_panicked_urgency",
        "under_sergeant_military_menace",
    }
)

ALTERNATES = {
    "beltempest_weary_resignation": ["50ef57315f67355f", "e4f06c8a60962b60"],
    "tobias_polished_probe": ["093f31282e4d8c8d"],
    "zebulon_nervous_analysis": ["535660811db3450d"],
    "karvellis_amplified_command": ["ebbf477e03d16ba6"],
    "rashid_tired_authority": ["14e5b5cf176f8efc"],
}


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
    result = dict(value)
    scores: dict[str, int | None] = {}
    for field in SCORE_FIELDS:
        raw = value.get(field)
        try:
            score = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            score = None
        scores[field] = score if score in {1, 2, 3, 4, 5} else None
    result["scores"] = scores
    result["all_scores_present"] = all(score is not None for score in scores.values())
    result["complete"] = value.get("completeness") == "complete"
    result["operator_pass"] = value.get("decision") == "pass"
    result["promotion_eligible"] = bool(
        result["operator_pass"]
        and result["complete"]
        and result["all_scores_present"]
    )
    return result


def main() -> int:
    review = read_json(DEFAULT_REVIEW, "Multimodel review")
    answer = read_json(DEFAULT_ANSWER, "Multimodel answer key")
    if review.get("round_id") != ROUND_ID or answer.get("round_id") != ROUND_ID:
        raise DecisionError("Review or answer key belongs to another round.")
    results = review.get("results")
    candidates = answer.get("candidates")
    modes = answer.get("modes")
    if not isinstance(results, Mapping) or not isinstance(candidates, Mapping):
        raise DecisionError("Review or answer key has an invalid candidate mapping.")
    if not isinstance(modes, list):
        raise DecisionError("Answer key has no mode list.")
    if set(results) != set(candidates):
        raise DecisionError("Review candidate IDs do not match the blind answer key.")

    reviewed = {candidate_id: normalized_review(value) for candidate_id, value in results.items()}
    selected: dict[str, dict[str, Any]] = {}
    for mode_id, candidate_id in WINNERS.items():
        row = candidates.get(candidate_id)
        review_row = reviewed.get(candidate_id)
        if not isinstance(row, Mapping) or row.get("mode_id") != mode_id:
            raise DecisionError(f"Winner mapping is invalid for {mode_id}.")
        if not review_row["promotion_eligible"]:
            raise DecisionError(f"Selected winner is not promotion-eligible: {candidate_id}.")
        tier = (
            "restricted_user_accepted"
            if mode_id in RESTRICTED_WINNERS
            else "strict"
        )
        if tier == "restricted_user_accepted" and review_row["scores"]["identity"] != 3:
            raise DecisionError(f"Restricted winner identity contract changed: {mode_id}.")
        if tier == "strict" and review_row["scores"]["identity"] < 4:
            raise DecisionError(f"Strict winner has insufficient identity: {mode_id}.")
        selected[mode_id] = {
            "mode_id": mode_id,
            "candidate_id": candidate_id,
            "backend": row["backend"],
            "approval_tier": tier,
            "scores": review_row["scores"],
            "notes": review_row.get("notes"),
            "text": row["text"],
            "instruct": row["instruct"],
            "audio_path": row["audio_path"],
            "audio_sha256": row["audio"]["sha256"],
            "identity_audio_sha256": row.get("identity_audio_sha256"),
            "performance_audio_sha256": row.get("performance_audio_sha256"),
            "effect_processing": row.get("effect_processing"),
            "seed": row.get("seed"),
        }

    alternates: dict[str, list[dict[str, Any]]] = {}
    for mode_id, candidate_ids in ALTERNATES.items():
        rows = []
        for candidate_id in candidate_ids:
            row = candidates[candidate_id]
            review_row = reviewed[candidate_id]
            if row.get("mode_id") != mode_id or not review_row["promotion_eligible"]:
                raise DecisionError(f"Alternate mapping is invalid: {candidate_id}.")
            rows.append(
                {
                    "candidate_id": candidate_id,
                    "backend": row["backend"],
                    "scores": review_row["scores"],
                    "status": "approved_evidence_not_default_route",
                }
            )
        alternates[mode_id] = rows

    unsupported = {
        mode["mode_id"]: {
            "mode_id": mode["mode_id"],
            "status": "no_approved_candidate",
        }
        for mode in modes
        if mode["mode_id"] not in selected
    }
    decision = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "closed_at_utc": utc_now(),
        "review_exported_at": review.get("exported_at"),
        "candidate_count": len(candidates),
        "selected_mode_count": len(selected),
        "strict_winner_count": sum(
            row["approval_tier"] == "strict" for row in selected.values()
        ),
        "restricted_winner_count": sum(
            row["approval_tier"] == "restricted_user_accepted"
            for row in selected.values()
        ),
        "unsupported_mode_count": len(unsupported),
        "selected": selected,
        "approved_alternates": alternates,
        "unsupported_modes": unsupported,
        "fail_closed_rules": {
            "written_notes_override_pass": True,
            "all_scores_required": True,
            "explicit_complete_line_required": True,
            "identity_below_four_requires_restricted_tier": True,
            "unselected_passes_are_evidence_only": True,
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
                "selected_mode_count": len(selected),
                "restricted_winner_count": decision["restricted_winner_count"],
                "unsupported_modes": sorted(unsupported),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
