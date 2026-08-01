#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
ROUND_ID = "alexandria_original_sin_overlap_final_character_round_v5"
REVIEW = ROOT / "benchmarks/original_sin_overlap_final_character_round_v5_review.json"
ANSWER = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665/external_workflows/big_finish_overlap_reference_v1/"
    "overlap_final_character_round_v5/private/answer-key.json"
)
OUTPUT = ROOT / "benchmarks/original_sin_overlap_final_character_round_v5_decision.json"
FIELDS = ("identity", "delivery", "naturalness", "intelligibility", "effects")
DOCTOR_WINNER = "3b81e79b4db7b9e7"
SHYTHE_WINNER = "a4eb313f21abbc67"
DANTALION_WINNER = "22f71b41cbee4305"
DANTALION_ALTERNATE = "3e27fa9d49bbf575"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalized(value: Mapping[str, Any]) -> dict[str, Any]:
    scores = {}
    for field in FIELDS:
        raw = value.get(field)
        try:
            score = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            score = None
        scores[field] = score if score in {1, 2, 3, 4, 5} else None
    return {
        **dict(value),
        "scores": scores,
        "complete": value.get("completeness") == "complete",
        "operator_pass": value.get("decision") == "pass",
        "all_scores_present": all(score is not None for score in scores.values()),
    }


def main() -> int:
    review = read_json(REVIEW)
    answer = read_json(ANSWER)
    if review.get("round_id") != ROUND_ID or answer.get("round_id") != ROUND_ID:
        raise RuntimeError("Review or answer key belongs to another round.")
    results = review.get("results")
    candidates = answer.get("candidates")
    if not isinstance(results, Mapping) or not isinstance(candidates, Mapping):
        raise RuntimeError("Invalid review or answer key.")
    if set(results) != set(candidates):
        raise RuntimeError("Review candidate IDs do not match the answer key.")
    reviewed = {candidate_id: normalized(value) for candidate_id, value in results.items()}

    doctor = reviewed[DOCTOR_WINNER]
    doctor_row = candidates[DOCTOR_WINNER]
    if not (
        doctor["operator_pass"]
        and doctor["complete"]
        and doctor["all_scores_present"]
        and min(doctor["scores"].values()) >= 4
    ):
        raise RuntimeError("Doctor winner is not strict-complete.")

    operator_selected = {}
    for mode_id, candidate_id in {
        "shythe_crisis_broadcast": SHYTHE_WINNER,
        "dantalion_weary_memory": DANTALION_WINNER,
    }.items():
        review_row = reviewed[candidate_id]
        source = candidates[candidate_id]
        if source.get("mode_id") != mode_id:
            raise RuntimeError(f"Selected mode changed: {candidate_id}")
        if not review_row["operator_pass"] or not review_row["complete"]:
            raise RuntimeError(f"Explicit pass changed: {candidate_id}")
        if (review_row["scores"]["identity"] or 0) < 4 or (review_row["scores"]["delivery"] or 0) < 4:
            raise RuntimeError(f"Selected explicit pass lost identity or delivery support: {candidate_id}")
        operator_selected[mode_id] = {
            "candidate_id": candidate_id,
            "backend": source["backend"],
            "approval_tier": "operator_approved_scores_incomplete",
            "scores": review_row["scores"],
            "missing_scores": [field for field, score in review_row["scores"].items() if score is None],
            "audio_path": source["audio_path"],
            "audio_sha256": source["audio"]["sha256"],
            "identity_audio_sha256": source.get("identity_audio_sha256"),
            "performance_audio_sha256": source.get("performance_audio_sha256"),
            "text": source["text"],
            "instruct": source["instruct"],
        }

    alternate_review = reviewed[DANTALION_ALTERNATE]
    alternate_row = candidates[DANTALION_ALTERNATE]
    if not alternate_review["operator_pass"] or not alternate_review["complete"]:
        raise RuntimeError("Dantalion alternate explicit pass changed.")

    decision = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "closed_at_utc": utc_now(),
        "review_exported_at": review.get("exported_at"),
        "selected": {
            "doctor_sudden_realization_final": {
                "candidate_id": DOCTOR_WINNER,
                "backend": doctor_row["backend"],
                "approval_tier": "strict",
                "scores": doctor["scores"],
                "audio_path": doctor_row["audio_path"],
                "audio_sha256": doctor_row["audio"]["sha256"],
                "identity_audio_sha256": doctor_row.get("identity_audio_sha256"),
                "performance_audio_sha256": doctor_row.get("performance_audio_sha256"),
                "text": doctor_row["text"],
                "instruct": doctor_row["instruct"],
            },
            **operator_selected,
        },
        "approved_alternates": {
            "dantalion_weary_memory": [{
                "candidate_id": DANTALION_ALTERNATE,
                "backend": alternate_row["backend"],
                "approval_tier": "operator_approved_scores_incomplete",
                "scores": alternate_review["scores"],
                "status": "approved_evidence_not_default_route",
            }],
            "doctor_sudden_realization_final": [
                {"candidate_id": "f7b6a9349295c223", "status": "operator_pass_evidence_only"},
                {"candidate_id": "77480f9eb974d653", "status": "operator_pass_evidence_only"},
            ],
        },
        "acceptance_rule": "explicit_operator_pass_is_actionable_even_when_optional_numeric_scores_are_omitted",
        "production_changes": False,
    }
    OUTPUT.write_text(json.dumps(decision, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "selected_modes": sorted(decision["selected"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
