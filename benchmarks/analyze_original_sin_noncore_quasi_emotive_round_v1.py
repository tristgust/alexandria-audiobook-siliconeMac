#!/usr/bin/env python3
"""Unblind the Qwen-only non-core Voice diagnostic round."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping


ROUND_ID = "alexandria_original_sin_noncore_quasi_emotive_round_v1"
DECISION_ID = "alexandria_original_sin_noncore_quasi_emotive_round_v1_closed"
DEFAULT_PROJECT = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665"
)
DEFAULT_ANSWER = (
    DEFAULT_PROJECT
    / "external_workflows"
    / "big_finish_overlap_reference_v1"
    / "noncore_quasi_emotive_round_v1"
    / "private"
    / "answer-key.json"
)
DEFAULT_REVIEW = Path(
    "benchmarks/original_sin_noncore_quasi_emotive_round_v1_review.json"
)
DEFAULT_OUTPUT = Path(
    "benchmarks/original_sin_noncore_quasi_emotive_round_v1_decision.json"
)

BLOCKING_NOTE_RULES = {
    "completeness": ("cuts off", "cut off", "last word"),
    "identity": (
        "different person",
        "nothing like",
        "wrong accent",
        "does not sound like him",
    ),
    "identity_effects": (
        "does not sound like it has the effects",
        "where is the modulation effect",
    ),
}
INCONCLUSIVE_NOTE_RULES = (
    "can't really say",
    "cannot really say",
    "i think this sounds like",
)
WEAK_DELIVERY_NOTE_RULES = (
    "weak",
    "not exactly intense",
    "sell the emotion better",
)


class DiagnosticDecisionError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DiagnosticDecisionError(f"{label} could not be read: {exc}") from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def score(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= 5 else None


def note_classification(note: str) -> tuple[list[str], bool, bool]:
    lowered = note.casefold()
    blockers = [
        category
        for category, phrases in BLOCKING_NOTE_RULES.items()
        if any(phrase in lowered for phrase in phrases)
    ]
    inconclusive = any(phrase in lowered for phrase in INCONCLUSIVE_NOTE_RULES)
    weak_delivery = any(phrase in lowered for phrase in WEAK_DELIVERY_NOTE_RULES)
    return blockers, inconclusive, weak_delivery


def reusable(row: Mapping[str, Any]) -> bool:
    review = row["review"]
    if review.get("decision") != "pass" or row["blocking_reasons"]:
        return False
    identity = score(review.get("identity"))
    naturalness = score(review.get("naturalness"))
    intelligibility = score(review.get("intelligibility"))
    return (
        identity is not None
        and identity >= 4
        and naturalness is not None
        and naturalness >= 4
        and intelligibility == 5
    )


def preference(row: Mapping[str, Any]) -> tuple[int, int, int, int]:
    review = row["review"]
    return (
        score(review.get("delivery")) or 0,
        score(review.get("identity")) or 0,
        int(row["candidate_kind"] == "specialist_reference"),
        score(review.get("naturalness")) or 0,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--answer-key", type=Path, default=DEFAULT_ANSWER)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    answer = read_json(args.answer_key.expanduser().resolve(), "Round answer key")
    review = read_json(args.review.expanduser().resolve(), "User review")
    if answer.get("round_id") != ROUND_ID or review.get("round_id") != ROUND_ID:
        raise DiagnosticDecisionError("Round IDs do not match the v1 diagnostic round.")
    candidates = answer.get("candidates")
    results = review.get("results")
    if not isinstance(candidates, Mapping) or not isinstance(results, Mapping):
        raise DiagnosticDecisionError("Answer key or review candidates are invalid.")
    if set(candidates) != set(results):
        raise DiagnosticDecisionError(
            "The review must contain every expected candidate exactly once."
        )

    rows: list[dict[str, Any]] = []
    by_mode: dict[str, list[dict[str, Any]]] = {}
    for candidate_id, raw in candidates.items():
        user = dict(results[candidate_id])
        note = str(user.get("notes") or "").strip()
        blockers, inconclusive, weak_delivery = note_classification(note)
        row = {
            "candidate_id": candidate_id,
            "mode_id": raw.get("mode_id"),
            "candidate_kind": raw.get("candidate_kind"),
            "reference_chunk_id": raw.get("reference_chunk_id"),
            "seed": raw.get("seed"),
            "audio_path": raw.get("audio_path"),
            "audio_sha256": (raw.get("audio") or {}).get("sha256"),
            "review": user,
            "blocking_reasons": blockers,
            "identity_inconclusive_without_reference": inconclusive,
            "delivery_underpowered": weak_delivery,
            "reusable_qwen_diagnostic": False,
        }
        rows.append(row)
        by_mode.setdefault(str(row["mode_id"]), []).append(row)

    reuse: dict[str, str] = {}
    for mode_id, mode_rows in by_mode.items():
        viable = [row for row in mode_rows if reusable(row)]
        if not viable:
            continue
        chosen = max(viable, key=preference)
        chosen["reusable_qwen_diagnostic"] = True
        reuse[mode_id] = str(chosen["candidate_id"])

    decision = {
        "schema_version": 1,
        "decision_id": DECISION_ID,
        "round_id": ROUND_ID,
        "decided_at_utc": utc_now(),
        "status": "diagnostic_closed_cross_model_acceptance_required",
        "candidate_count": len(rows),
        "mode_count": len(by_mode),
        "reusable_qwen_candidate_count": len(reuse),
        "reusable_qwen_candidates": reuse,
        "candidates": rows,
        "required_follow_up": {
            "round_id": "alexandria_original_sin_noncore_multimodel_round_v2",
            "models": [
                "qwen3_instruction_controlled",
                "voxcpm2_controllable_clone",
                "fish_s2_pro_free_zero_shot",
                "indextts2_matched_control",
            ],
            "reference_audio_visible": True,
            "complete_line_score_required": True,
            "identity_effects_score_required": True,
            "production_routing_changed": False,
        },
        "production_winners_selected": False,
        "production_routing_changed": False,
        "project_audio_changed": False,
        "voice_config_changed": False,
    }
    write_json(args.output, decision)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "candidate_count": len(rows),
                "mode_count": len(by_mode),
                "reusable_qwen_candidate_count": len(reuse),
                "reusable_qwen_candidates": reuse,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
