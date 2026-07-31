#!/usr/bin/env python3
"""Close the final Powerless and Chris blind-review gates.

This analyzer is evidence-only. It does not change Alexandria project Voice,
reference-bank, or chunk-audio state.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


POWERLESS_ROUND_ID = "alexandria_original_sin_powerless_final_source_v7"
CHRIS_ROUND_ID = "alexandria_original_sin_chris_urgent_clean_identity_v4"
POWERLESS_CANDIDATE_COUNT = 1
CHRIS_CANDIDATE_COUNT = 4


class FinalGateError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FinalGateError(f"Expected JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def score(result: dict[str, Any], key: str) -> int:
    try:
        value = int(result[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise FinalGateError(f"Missing or invalid score {key!r}") from exc
    if value not in range(1, 6):
        raise FinalGateError(f"Score outside 1-5: {key}={value}")
    return value


def analyze_powerless(answer: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    if answer.get("round_id") != POWERLESS_ROUND_ID or review.get("round_id") != POWERLESS_ROUND_ID:
        raise FinalGateError("Powerless round_id mismatch")
    candidates = answer.get("candidates")
    results = review.get("results")
    if not isinstance(candidates, dict) or not isinstance(results, dict):
        raise FinalGateError("Powerless candidates/results must be objects")
    if len(candidates) != POWERLESS_CANDIDATE_COUNT or set(candidates) != set(results):
        raise FinalGateError("Powerless review must account for its one candidate")
    candidate_id, source = next(iter(candidates.items()))
    human = results[candidate_id]
    scores = {
        key: score(human, key)
        for key in ("boundaries", "isolation", "naturalness", "usefulness")
    }
    notes = str(human.get("notes") or "").strip()
    eligible = (
        human.get("decision") == "pass"
        and min(scores.values()) >= 4
        and not notes
        and source.get("objective_eligible") is True
    )
    return {
        "round_id": POWERLESS_ROUND_ID,
        "review_exported_at": review.get("exported_at"),
        "candidate_count": 1,
        "chunk_id": int(source["chunk_id"]),
        "character": str(source["character"]),
        "outcome": (
            "exact-line substitution eligible"
            if eligible
            else "exact-line substitution unsupported"
        ),
        "selected_candidate_id": candidate_id if eligible else None,
        "selected_treatment": source.get("treatment") if eligible else None,
        "candidate": {
            "candidate_id": candidate_id,
            "human_decision": human.get("decision"),
            "human_scores": scores,
            "notes": notes,
            "promotion_eligible": eligible,
        },
    }


def analyze_chris(answer: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    if answer.get("round_id") != CHRIS_ROUND_ID or review.get("round_id") != CHRIS_ROUND_ID:
        raise FinalGateError("Chris round_id mismatch")
    candidates = answer.get("candidates")
    results = review.get("results")
    if not isinstance(candidates, dict) or not isinstance(results, dict):
        raise FinalGateError("Chris candidates/results must be objects")
    if len(candidates) != CHRIS_CANDIDATE_COUNT or set(candidates) != set(results):
        raise FinalGateError("Chris review must account for all four candidates")

    rows = []
    for candidate_id, source in candidates.items():
        human = results[candidate_id]
        scores = {
            key: score(human, key)
            for key in ("identity", "delivery", "naturalness", "artifacts")
        }
        notes = str(human.get("notes") or "").strip()
        eligible = (
            human.get("decision") == "pass"
            and scores["identity"] >= 4
            and scores["delivery"] >= 4
            and scores["naturalness"] >= 4
            and scores["artifacts"] <= 1
            and not notes
            and source.get("fallback_used") is not True
        )
        rows.append(
            {
                "candidate_id": candidate_id,
                "route_key": str(source["route_key"]),
                "actual_backend": str(source["actual_backend"]),
                "human_decision": human.get("decision"),
                "human_scores": scores,
                "notes": notes,
                "promotion_eligible": eligible,
                "selected": False,
            }
        )
    eligible = [row for row in rows if row["promotion_eligible"]]
    winner = None
    if eligible:
        winner = max(
            eligible,
            key=lambda row: (
                row["human_scores"]["identity"]
                + row["human_scores"]["delivery"]
                + row["human_scores"]["naturalness"],
                row["human_scores"]["delivery"],
                row["route_key"] == "qwen_clean_identity",
            ),
        )
        winner["selected"] = True
    first = next(iter(candidates.values()))
    return {
        "round_id": CHRIS_ROUND_ID,
        "review_exported_at": review.get("exported_at"),
        "candidate_count": len(rows),
        "character": str(first["character"]),
        "mode": str(first["mode"]),
        "outcome": (
            "approved expressive generation route"
            if winner
            else "urgent authority unsupported"
        ),
        "selected_candidate_id": winner["candidate_id"] if winner else None,
        "selected_route_key": winner["route_key"] if winner else None,
        "selected_actual_backend": winner["actual_backend"] if winner else None,
        "candidates": rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    direct = report["powerless_round"]
    chris = report["chris_round"]
    return "\n".join(
        [
            "# Original Sin final gate decisions",
            "",
            "No Alexandria project Voice, reference bank, or chunk audio was changed.",
            "",
            "## Powerless Friendless",
            "",
            f"- Outcome: {direct['outcome']}",
            f"- Chunk: `{direct['chunk_id']}`",
            f"- Candidate: `{direct['selected_candidate_id']}`",
            f"- Treatment: `{direct['selected_treatment']}`",
            "",
            "## Chris Cwej urgent authority",
            "",
            f"- Outcome: {chris['outcome']}",
            f"- Candidate: `{chris['selected_candidate_id']}`",
            f"- Route: `{chris['selected_route_key']}`",
            f"- Backend: `{chris['selected_actual_backend']}`",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--powerless-answer-key", type=Path, required=True)
    parser.add_argument("--powerless-review", type=Path, required=True)
    parser.add_argument("--chris-answer-key", type=Path, required=True)
    parser.add_argument("--chris-review", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    args = parser.parse_args()
    report = {
        "schema_version": 1,
        "powerless_round": analyze_powerless(
            read_json(args.powerless_answer_key),
            read_json(args.powerless_review),
        ),
        "chris_round": analyze_chris(
            read_json(args.chris_answer_key),
            read_json(args.chris_review),
        ),
        "production_changes": False,
        "project_voice_config_changed": False,
        "project_chunks_changed": False,
    }
    write_json(args.output_json, report)
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.write_text(render_markdown(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
