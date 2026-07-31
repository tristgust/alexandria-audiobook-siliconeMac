#!/usr/bin/env python3
"""Unblind Original Sin boundary-repair v3 and expansion batch 003."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
from typing import Any


BOUNDARY_ROUND = "alexandria_original_sin_direct_overlap_boundary_repair_v3"
BATCH_ROUND = "alexandria_original_sin_direct_overlap_expansion_batch_003"


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
    if re.search(r"not cwej|that is roz|wrong speaker", value):
        flags.append("wrong_speaker")
    if re.search(r"start|beginning|before .* says|before .* sentence", value):
        flags.append("start_artifact")
    if re.search(r"cuts off|cut off|midway through|boundary", value):
        flags.append("boundary_incomplete")
    if re.search(r"echo|gun|groan|background|someone else talking|music|sound effects", value):
        flags.append("contamination")
    if re.search(r"enhance|muffled", value):
        flags.append("quality_limited")
    if re.search(r"breath", value):
        flags.append("trailing_other_voice")
    return flags


def treatment_rank(treatment: str) -> int:
    value = str(treatment)
    if value.startswith("mossformer2"):
        return 2
    if value.startswith("mel_roformer"):
        return 1
    return 0


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
            and all(scores[key] == 5 for key in scores)
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
    expected = (5351, 2745, 2720, 218, 5371, 1320, 2919)
    by_chunk: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_chunk[row["chunk_id"]].append(row)
    if set(by_chunk) != set(expected):
        raise ReviewAnalysisError("Unexpected boundary-repair chunks")
    fixed_outcomes = {
        2745: "source blocked after tail-splice repair",
        2720: "requires tighter trailing-breath trim",
        1320: "source blocked after tail-splice repair",
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
    return {"round_id": BOUNDARY_ROUND, "candidate_count": len(rows), "chunk_decisions": decisions, "candidates": rows}


def analyze_batch(answer_key: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    rows = rows_for_round(answer_key, review, BATCH_ROUND)
    expected = (4432, 5014, 2089, 2099, 1985, 658, 1098, 4735, 1575, 3989, 615, 3157, 3209, 4880, 4698, 3036, 3293)
    by_chunk: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_chunk[row["chunk_id"]].append(row)
    if set(by_chunk) != set(expected):
        raise ReviewAnalysisError("Unexpected batch-003 chunks")
    fixed_outcomes = {
        4432: "requires final-word tail repair",
        2099: "requires contamination/source repair",
        658: "requires first-word start trim",
        1098: "excluded wrong-speaker textual match",
        1575: "requires first-word start trim",
        3989: "requires first-word start trim",
        3157: "requires contamination/source repair",
        3209: "reference-bank evidence only",
        3036: "requires first-word start trim",
    }
    decisions = []
    for chunk_id in expected:
        group = by_chunk[chunk_id]
        winner = choose(group)
        outcome = "exact-line substitution eligible" if winner else fixed_outcomes.get(chunk_id, "human rejected")
        selected = winner
        if chunk_id == 3209:
            selected = max(
                group,
                key=lambda row: (
                    row["human_decision"] == "pass",
                    sum((row["human_scores"] or {}).values()),
                    treatment_rank(row["treatment"]),
                ),
            )
        decisions.append(
            {
                "chunk_id": chunk_id,
                "character": group[0]["character"],
                "book_speaker": group[0]["book_speaker"],
                "transcript": group[0]["transcript"],
                "outcome": outcome,
                "selected_candidate_id": selected["candidate_id"] if selected and outcome != "excluded wrong-speaker textual match" else None,
                "selected_treatment": selected["treatment"] if selected and outcome != "excluded wrong-speaker textual match" else None,
            }
        )
    return {"round_id": BATCH_ROUND, "candidate_count": len(rows), "chunk_decisions": decisions, "candidates": rows}


def analyze(boundary_answer: dict[str, Any], boundary_review: dict[str, Any], batch_answer: dict[str, Any], batch_review: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "boundary_repair_round": analyze_boundary(boundary_answer, boundary_review),
        "batch_003_round": analyze_batch(batch_answer, batch_review),
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
    report = analyze(
        read_json(args.boundary_answer),
        read_json(args.boundary_review),
        read_json(args.batch_answer),
        read_json(args.batch_review),
    )
    write_json(args.output, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
