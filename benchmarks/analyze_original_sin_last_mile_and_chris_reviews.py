#!/usr/bin/env python3
"""Unblind the direct last-mile and Chris urgent-performance reviews."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


DIRECT_ROUND_ID = "alexandria_original_sin_direct_substitution_last_mile_v6"
CHRIS_ROUND_ID = "alexandria_original_sin_chris_urgent_performance_v3"
DIRECT_CHUNKS = (1317, 2954)


class ReviewError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReviewError(f"Expected object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def score(row: dict[str, Any], key: str) -> int:
    try:
        value = int(row[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ReviewError(f"Missing {key}: {row.get(key)!r}") from exc
    if value not in range(1, 6):
        raise ReviewError(f"Invalid {key}: {value}")
    return value


def treatment_rank(value: str) -> int:
    return {
        "source_mix": 4,
        "mossformer2_source_mix": 3,
        "mossformer2_blend50": 2,
        "mel_roformer_vocal": 1,
    }.get(str(value), 0)


def analyze_direct(answer: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    if answer.get("round_id") != DIRECT_ROUND_ID or review.get("round_id") != DIRECT_ROUND_ID:
        raise ReviewError("Direct round mismatch")
    candidates = answer.get("candidates")
    results = review.get("results")
    if not isinstance(candidates, dict) or not isinstance(results, dict):
        raise ReviewError("Direct candidates/results missing")
    if len(candidates) != 5 or set(candidates) != set(results):
        raise ReviewError("Direct review must contain all five candidates")
    rows: list[dict[str, Any]] = []
    by_chunk: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for candidate_id, source in candidates.items():
        human = results[candidate_id]
        decision = str(human.get("decision") or "").casefold()
        if decision not in {"pass", "fail"}:
            raise ReviewError(f"Missing direct decision: {candidate_id}")
        scores = {key: score(human, key) for key in ("boundaries", "isolation", "naturalness", "usefulness")}
        eligible = decision == "pass" and min(scores.values()) >= 4
        row = {
            "candidate_id": candidate_id,
            "chunk_id": int(source["chunk_id"]),
            "character": str(source["character"]),
            "treatment": str(source["treatment"]),
            "human_decision": decision,
            "human_scores": scores,
            "promotion_eligible": eligible,
            "selected": False,
        }
        rows.append(row)
        by_chunk[row["chunk_id"]].append(row)
    if set(by_chunk) != set(DIRECT_CHUNKS):
        raise ReviewError(f"Unexpected direct chunks: {sorted(by_chunk)}")
    decisions = []
    for chunk_id in DIRECT_CHUNKS:
        eligible = [row for row in by_chunk[chunk_id] if row["promotion_eligible"]]
        winner = None
        if eligible:
            winner = max(
                eligible,
                key=lambda row: (
                    sum(row["human_scores"].values()),
                    treatment_rank(row["treatment"]),
                ),
            )
            winner["selected"] = True
        first = by_chunk[chunk_id][0]
        decisions.append({
            "chunk_id": chunk_id,
            "character": first["character"],
            "outcome": "exact-line substitution eligible" if winner else "requires another exact source",
            "selected_candidate_id": winner["candidate_id"] if winner else None,
            "selected_treatment": winner["treatment"] if winner else None,
        })
    return {
        "round_id": DIRECT_ROUND_ID,
        "candidate_count": len(rows),
        "chunk_decisions": decisions,
        "candidates": rows,
    }


def analyze_chris(answer: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    if answer.get("round_id") != CHRIS_ROUND_ID or review.get("round_id") != CHRIS_ROUND_ID:
        raise ReviewError("Chris round mismatch")
    candidates = answer.get("candidates")
    results = review.get("results")
    if not isinstance(candidates, dict) or not isinstance(results, dict):
        raise ReviewError("Chris candidates/results missing")
    if len(candidates) != 2 or set(candidates) != set(results):
        raise ReviewError("Chris review must contain both candidates")
    rows = []
    for candidate_id, source in candidates.items():
        human = results[candidate_id]
        decision = str(human.get("decision") or "").casefold()
        if decision not in {"pass", "fail"}:
            raise ReviewError(f"Missing Chris decision: {candidate_id}")
        scores = {key: score(human, key) for key in ("identity", "delivery", "naturalness", "artifacts")}
        notes = str(human.get("notes") or "").strip()
        eligible = (
            decision == "pass"
            and scores["identity"] >= 4
            and scores["delivery"] >= 4
            and scores["naturalness"] >= 4
            and scores["artifacts"] >= 4
            and not notes
        )
        rows.append({
            "candidate_id": candidate_id,
            "route_key": str(source["route_key"]),
            "actual_backend": str(source["actual_backend"]),
            "reference_treatment": str(source["reference_treatment"]),
            "human_decision": decision,
            "human_scores": scores,
            "notes": notes,
            "promotion_eligible": eligible,
        })
    eligible = [row for row in rows if row["promotion_eligible"]]
    return {
        "round_id": CHRIS_ROUND_ID,
        "candidate_count": len(rows),
        "outcome": "approved expressive generation route" if eligible else "urgent authority remains unproven",
        "selected_candidate_id": eligible[0]["candidate_id"] if eligible else None,
        "candidates": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--direct-answer-key", type=Path, required=True)
    parser.add_argument("--direct-review", type=Path, required=True)
    parser.add_argument("--chris-answer-key", type=Path, required=True)
    parser.add_argument("--chris-review", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    args = parser.parse_args()
    report = {
        "schema_version": 1,
        "direct_round": analyze_direct(read_json(args.direct_answer_key), read_json(args.direct_review)),
        "chris_round": analyze_chris(read_json(args.chris_answer_key), read_json(args.chris_review)),
        "production_changes": False,
    }
    write_json(args.output_json, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
