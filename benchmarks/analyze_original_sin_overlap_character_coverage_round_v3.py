#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
ROUND_ID = "alexandria_original_sin_overlap_character_coverage_round_v3"
DEFAULT_REVIEW = ROOT / "benchmarks/original_sin_overlap_character_coverage_round_v3_review.json"
DEFAULT_ANSWER = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665/external_workflows/big_finish_overlap_reference_v1/"
    "overlap_character_coverage_round_v3/private/answer-key.json"
)
DEFAULT_OUTPUT = ROOT / "benchmarks/original_sin_overlap_character_coverage_round_v3_decision.json"
SCORE_FIELDS = ("identity", "delivery", "naturalness", "intelligibility", "effects")


WINNERS = {
    "doctor_wry_deflection": "e7768afee3731638",
    "doctor_hushed_vulnerability": "1ddea3084591ac68",
    "bernice_quiet_defiance": "a3fc7c2761afc1f7",
    "bernice_bittersweet_nostalgia": "fc952419bf3a5e0d",
    "roz_survivor_reflection": "750d0f92682e3235",
    "roz_defeated_grief": "f93b7cf54a65b398",
    "chris_exposed_vulnerability": "010970948cf76dcf",
    "powerless_wounded_accusation": "06b2057e89690313",
    "hater_grave_statecraft": "f3e6595da0425191",
    "evan_broadcast_authority": "70e52c2fedc3b843",
    "securitybot_identity_repair": "70388c05e8412f90",
    "tobias_robot_cold_control": "8bbd24d9c531dd0b",
}

RESTRICTED_WINNERS = frozenset(
    {
        "roz_survivor_reflection",
        "hater_grave_statecraft",
        "securitybot_identity_repair",
    }
)

ALTERNATES = {
    "doctor_hushed_vulnerability": ["a63f80300f94338c"],
    "chris_exposed_vulnerability": ["e6599dedd0bc8546"],
}

REPAIR_REQUIRED = {
    "doctor_urgent_discovery": "No candidate preserved the Doctor identity above 3/5.",
    "doctor_weary_moral_gravity": "No candidate combined natural delivery and the Doctor identity.",
    "roz_dry_banter": "No candidate was human-approved despite one identity-strong Fish output.",
    "computer_formal_timestamp": (
        "Nominal passes scored effects 1/5, and the operator explicitly said the "
        "required post-processing was absent."
    ),
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


def candidate_record(
    *,
    mode_id: str,
    candidate_id: str,
    row: Mapping[str, Any],
    review_row: Mapping[str, Any],
    tier: str,
) -> dict[str, Any]:
    return {
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


def main() -> int:
    review = read_json(DEFAULT_REVIEW, "Coverage review")
    answer = read_json(DEFAULT_ANSWER, "Coverage answer key")
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

    reviewed = {
        candidate_id: normalized_review(value)
        for candidate_id, value in results.items()
        if isinstance(value, Mapping)
    }
    selected: dict[str, dict[str, Any]] = {}
    for mode_id, candidate_id in WINNERS.items():
        row = candidates.get(candidate_id)
        review_row = reviewed.get(candidate_id)
        if not isinstance(row, Mapping) or row.get("mode_id") != mode_id:
            raise DecisionError(f"Winner mapping is invalid for {mode_id}.")
        if not isinstance(review_row, Mapping) or not review_row["promotion_eligible"]:
            raise DecisionError(f"Selected winner is not review-complete: {candidate_id}.")
        tier = (
            "restricted_user_accepted"
            if mode_id in RESTRICTED_WINNERS
            else "strict"
        )
        scores = review_row["scores"]
        if tier == "strict" and min(scores.values()) < 4:
            raise DecisionError(f"Strict winner has a score below four: {mode_id}.")
        if tier == "restricted_user_accepted" and min(scores.values()) < 3:
            raise DecisionError(f"Restricted winner has a score below three: {mode_id}.")
        selected[mode_id] = candidate_record(
            mode_id=mode_id,
            candidate_id=candidate_id,
            row=row,
            review_row=review_row,
            tier=tier,
        )

    alternates: dict[str, list[dict[str, Any]]] = {}
    for mode_id, candidate_ids in ALTERNATES.items():
        alternate_rows = []
        for candidate_id in candidate_ids:
            row = candidates[candidate_id]
            review_row = reviewed[candidate_id]
            if row.get("mode_id") != mode_id or not review_row["promotion_eligible"]:
                raise DecisionError(f"Alternate mapping is invalid: {candidate_id}.")
            status = "approved_evidence_not_default_route"
            if candidate_id == "a63f80300f94338c":
                status = "approved_evidence_not_default_due_operator_overgeneralization_concern"
            alternate_rows.append(
                {
                    "candidate_id": candidate_id,
                    "backend": row["backend"],
                    "scores": review_row["scores"],
                    "notes": review_row.get("notes"),
                    "status": status,
                }
            )
        alternates[mode_id] = alternate_rows

    blocked_nominal_passes = {}
    for candidate_id in ("da6c367d964ea6c9", "56da202533b9f6d6"):
        row = candidates[candidate_id]
        review_row = reviewed[candidate_id]
        if not review_row["operator_pass"]:
            raise DecisionError(f"Expected nominal Computer pass changed: {candidate_id}.")
        blocked_nominal_passes[candidate_id] = {
            "mode_id": row["mode_id"],
            "backend": row["backend"],
            "scores": review_row["scores"],
            "notes": review_row.get("notes"),
            "status": "blocked_missing_required_character_processing",
        }

    unsupported = {
        mode["mode_id"]: {
            "mode_id": mode["mode_id"],
            "status": "repair_required",
            "reason": REPAIR_REQUIRED[mode["mode_id"]],
        }
        for mode in modes
        if mode["mode_id"] in REPAIR_REQUIRED
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
        "repair_required_mode_count": len(unsupported),
        "selected": selected,
        "approved_alternates": alternates,
        "blocked_nominal_passes": blocked_nominal_passes,
        "repair_required_modes": unsupported,
        "speaker_split_validation": {
            "status": "approved_pending_production_remap",
            "selected_mode_id": "tobias_robot_cold_control",
            "selected_candidate_id": WINNERS["tobias_robot_cold_control"],
            "securitybot_chunk_ids": [491, 493, 495, 497, 501, 503, 618, 622, 634],
            "tobias_robot_chunk_ids": [1341, 3669, 3674, 3676, 3680, 3682, 3684],
        },
        "fail_closed_rules": {
            "written_notes_override_pass": True,
            "all_scores_required": True,
            "explicit_complete_line_required": True,
            "strict_scores_must_all_be_at_least_four": True,
            "restricted_scores_must_all_be_at_least_three": True,
            "required_character_effects_can_block_a_nominal_pass": True,
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
                "strict_winner_count": decision["strict_winner_count"],
                "restricted_winner_count": decision["restricted_winner_count"],
                "repair_required_modes": sorted(unsupported),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
