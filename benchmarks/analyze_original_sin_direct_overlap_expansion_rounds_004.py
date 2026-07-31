#!/usr/bin/env python3
"""Unblind Original Sin boundary-repair v4 and expansion batch 004."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
from typing import Any


BOUNDARY_ROUND = "alexandria_original_sin_direct_overlap_boundary_repair_v4"
BATCH_ROUND = "alexandria_original_sin_direct_overlap_expansion_batch_004"


class ReviewAnalysisError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReviewAnalysisError(f"Expected JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def score_bundle(row: dict[str, Any]) -> dict[str, int] | None:
    keys = ("boundaries", "isolation", "music_effects", "artifacts", "naturalness", "usefulness")
    if not any(key in row for key in keys):
        return None
    scores: dict[str, int] = {}
    for key in keys:
        try:
            value = int(row[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise ReviewAnalysisError(f"Invalid {key}: {row.get(key)!r}") from exc
        if value not in range(1, 6):
            raise ReviewAnalysisError(f"Score outside 1-5: {key}={value}")
        scores[key] = value
    return scores


def note_flags(notes: str) -> list[str]:
    value = str(notes or "").strip().casefold()
    flags: list[str] = []
    if re.search(r"doctor speaking not cwej|not cwej|wrong speaker", value):
        flags.append("wrong_speaker")
    if re.search(r"artifact at the start|sound at the start|at the start before|before she says|before .* says", value):
        flags.append("start_artifact")
    if re.search(r"cuts off|cuts out|abrupt|before he finishes|end of myself", value):
        flags.append("boundary_incomplete")
    if re.search(r"echo|gun|screaming|sipping|background|sound effects", value):
        flags.append("contamination")
    if re.search(r"muffled", value):
        flags.append("quality_limited")
    return flags


def treatment_rank(treatment: str) -> tuple[int, int]:
    value = str(treatment)
    extraction = 2 if value.startswith("mossformer2") else 1 if value.startswith("mel_roformer") else 0
    postroll = 1 if "postroll0.02" in value else 0
    return extraction, postroll


def rows_for_round(answer_key: dict[str, Any], review: dict[str, Any], expected_round: str) -> list[dict[str, Any]]:
    if answer_key.get("round_id") != expected_round or review.get("round_id") != expected_round:
        raise ReviewAnalysisError("round_id mismatch")
    candidates = answer_key.get("candidates")
    results = review.get("results")
    if not isinstance(candidates, dict) or not isinstance(results, dict) or set(candidates) != set(results):
        raise ReviewAnalysisError("Review must account for every candidate")
    rows: list[dict[str, Any]] = []
    for candidate_id, source in candidates.items():
        human = results[candidate_id]
        decision = str(human.get("decision") or "").casefold()
        if decision not in {"pass", "fail"}:
            raise ReviewAnalysisError(f"Missing decision: {candidate_id}")
        scores = score_bundle(human)
        notes = str(human.get("notes") or "").strip()
        flags = note_flags(notes)
        clean = (
            decision == "pass"
            and scores is not None
            and all(value == 5 for value in scores.values())
            and not flags
        )
        rows.append(
            {
                "candidate_id": candidate_id,
                "chunk_id": int(source["chunk_id"]),
                "character": str(source["character"]),
                "book_speaker": str(source["book_speaker"]),
                "transcript": str(source["transcript"]),
                "treatment": str(source["treatment"]),
                "human_decision": decision,
                "human_scores": scores,
                "notes": notes,
                "note_flags": flags,
                "promotion_eligible": clean,
                "selected": False,
            }
        )
    return rows


def choose(group: list[dict[str, Any]]) -> dict[str, Any] | None:
    eligible = [row for row in group if row["promotion_eligible"]]
    if not eligible:
        return None
    winner = max(
        eligible,
        key=lambda row: (
            sum((row["human_scores"] or {}).values()),
            treatment_rank(row["treatment"]),
        ),
    )
    winner["selected"] = True
    return winner


def analyze_boundary(answer_key: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    rows = rows_for_round(answer_key, review, BOUNDARY_ROUND)
    expected = (2720, 4432, 658, 1575, 3989, 3036)
    by_chunk: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_chunk[row["chunk_id"]].append(row)
    if set(by_chunk) != set(expected):
        raise ReviewAnalysisError("Unexpected boundary-repair chunks")
    decisions = []
    for chunk_id in expected:
        group = by_chunk[chunk_id]
        winner = choose(group)
        if winner is None:
            raise ReviewAnalysisError(f"Boundary repair did not close chunk {chunk_id}")
        decisions.append(
            {
                "chunk_id": chunk_id,
                "character": group[0]["character"],
                "book_speaker": group[0]["book_speaker"],
                "transcript": group[0]["transcript"],
                "outcome": "exact-line substitution eligible",
                "selected_candidate_id": winner["candidate_id"],
                "selected_treatment": winner["treatment"],
            }
        )
    return {"round_id": BOUNDARY_ROUND, "candidate_count": len(rows), "chunk_decisions": decisions, "candidates": rows}


def analyze_batch(answer_key: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    rows = rows_for_round(answer_key, review, BATCH_ROUND)
    expected = (2047, 2716, 2737, 66, 1995, 1259, 1676, 2979, 4866, 2555, 636, 506, 5018, 4687, 4758, 4780)
    by_chunk: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_chunk[row["chunk_id"]].append(row)
    if set(by_chunk) != set(expected):
        raise ReviewAnalysisError("Unexpected batch-004 chunks")
    fixed_outcomes = {
        2047: "requires first-word start trim",
        1676: "excluded wrong-speaker textual match",
        2979: "requires final-word tail repair",
        2555: "requires first-word start trim",
        506: "requires first-word start trim",
        5018: "requires contamination/source repair",
        4687: "requires contamination/source repair",
        4758: "requires final-word tail repair",
        4780: "requires final-word and post-line sound repair",
    }
    decisions = []
    for chunk_id in expected:
        group = by_chunk[chunk_id]
        winner = choose(group)
        outcome = "exact-line substitution eligible" if winner else fixed_outcomes.get(chunk_id, "human rejected")
        decisions.append(
            {
                "chunk_id": chunk_id,
                "character": group[0]["character"],
                "book_speaker": group[0]["book_speaker"],
                "transcript": group[0]["transcript"],
                "outcome": outcome,
                "selected_candidate_id": winner["candidate_id"] if winner else None,
                "selected_treatment": winner["treatment"] if winner else None,
            }
        )
    return {"round_id": BATCH_ROUND, "candidate_count": len(rows), "chunk_decisions": decisions, "candidates": rows}


def analyze(boundary_answer: dict[str, Any], boundary_review: dict[str, Any], batch_answer: dict[str, Any], batch_review: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "boundary_repair_round": analyze_boundary(boundary_answer, boundary_review),
        "batch_004_round": analyze_batch(batch_answer, batch_review),
        "production_changes": False,
        "project_voice_config_changed": False,
        "project_chunks_changed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--boundary-answer", type=Path, required=True)
    parser.add_argument("--boundary-review", type=Path, required=True)
    parser.add_argument("--batch-answer", type=Path, required=True)
    parser.add_argument("--batch-review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    write_json(
        args.output,
        analyze(
            read_json(args.boundary_answer),
            read_json(args.boundary_review),
            read_json(args.batch_answer),
            read_json(args.batch_review),
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
