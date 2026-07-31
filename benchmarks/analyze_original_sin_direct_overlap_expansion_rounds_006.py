#!/usr/bin/env python3
"""Unblind Original Sin boundary-repair v6 and expansion batch 006."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
from typing import Any


BOUNDARY_ROUND = "alexandria_original_sin_direct_overlap_boundary_repair_v6"
BATCH_ROUND = "alexandria_original_sin_direct_overlap_expansion_batch_006"
SCORE_KEYS = (
    "boundaries",
    "isolation",
    "music_effects",
    "artifacts",
    "naturalness",
    "usefulness",
)


class ReviewAnalysisError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReviewAnalysisError(f"Expected JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def score_bundle(row: dict[str, Any]) -> tuple[dict[str, int], list[str]]:
    scores: dict[str, int] = {}
    missing: list[str] = []
    for key in SCORE_KEYS:
        if key not in row or row[key] in (None, ""):
            missing.append(key)
            continue
        try:
            value = int(row[key])
        except (TypeError, ValueError) as exc:
            raise ReviewAnalysisError(f"Invalid {key}: {row.get(key)!r}") from exc
        if value not in range(1, 6):
            raise ReviewAnalysisError(f"Score outside 1-5: {key}={value}")
        scores[key] = value
    return scores, missing


def note_flags(notes: str) -> list[str]:
    value = str(notes or "").strip().casefold()
    flags: list[str] = []
    if re.search(r"doesn't sound like beltempest|wrong speaker|not beltempest", value):
        flags.append("wrong_speaker_identity")
    if re.search(r"muffled|needs some enhancement", value):
        flags.append("quality_limited")
    if re.search(r"trimmed out|needs to be trimmed|bit at the end", value):
        flags.append("trailing_contamination")
    if re.search(r"cuts off|abrupt|before .* finishes", value):
        flags.append("boundary_incomplete")
    if re.search(r"echo|sounds on either end", value):
        flags.append("contamination")
    return flags


def treatment_rank(treatment: str) -> tuple[int, int]:
    value = str(treatment)
    extraction = 2 if value.startswith("mossformer2") else 1 if value.startswith("mel_roformer") else 0
    match = re.search(r"(?:extension|recovery)_(?:ms)?(\d+)|(?:extension|recovery)_(\d+)", value)
    milliseconds = int(next(part for part in match.groups() if part)) if match else 0
    return extraction, -milliseconds


def rows_for_round(
    answer_key: dict[str, Any],
    review: dict[str, Any],
    expected_round: str,
) -> list[dict[str, Any]]:
    if answer_key.get("round_id") != expected_round or review.get("round_id") != expected_round:
        raise ReviewAnalysisError("round_id mismatch")
    candidates = answer_key.get("candidates")
    results = review.get("results")
    if not isinstance(candidates, dict) or not isinstance(results, dict):
        raise ReviewAnalysisError("Candidates/results must be objects")
    if set(candidates) != set(results):
        raise ReviewAnalysisError("Review must account for every blind candidate")

    rows: list[dict[str, Any]] = []
    for candidate_id, source in candidates.items():
        human = results[candidate_id]
        decision = str(human.get("decision") or "").casefold()
        if decision not in {"pass", "fail"}:
            raise ReviewAnalysisError(f"Missing decision: {candidate_id}")
        scores, missing_scores = score_bundle(human)
        notes = str(human.get("notes") or "").strip()
        flags = note_flags(notes)
        complete_scores = not missing_scores
        clean = (
            decision == "pass"
            and complete_scores
            and all(scores[key] == 5 for key in SCORE_KEYS)
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
                "missing_scores": missing_scores,
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
            sum(row["human_scores"].values()),
            treatment_rank(row["treatment"]),
        ),
    )
    winner["selected"] = True
    return winner


def analyze_boundary(answer_key: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    rows = rows_for_round(answer_key, review, BOUNDARY_ROUND)
    expected = (2979, 2746, 5336, 5120, 5353, 4675, 3090)
    by_chunk: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_chunk[row["chunk_id"]].append(row)
    if set(by_chunk) != set(expected):
        raise ReviewAnalysisError("Unexpected boundary-repair v6 chunks")
    blocked = {
        2746: "source blocked after bounded final-word recovery",
        5120: "quality blocked after start/end cleanup",
        4675: "source blocked after in-boundary tail recovery",
    }
    decisions: list[dict[str, Any]] = []
    for chunk_id in expected:
        group = by_chunk[chunk_id]
        winner = choose(group)
        outcome = "exact-line substitution eligible" if winner else blocked.get(chunk_id, "human rejected")
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
    return {
        "round_id": BOUNDARY_ROUND,
        "candidate_count": len(rows),
        "chunk_decisions": decisions,
        "candidates": rows,
    }


def analyze_batch(answer_key: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    rows = rows_for_round(answer_key, review, BATCH_ROUND)
    expected = (4580, 2584, 3471, 1401, 750, 3979, 561, 3189, 5431, 5055, 973)
    by_chunk: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_chunk[row["chunk_id"]].append(row)
    if set(by_chunk) != set(expected):
        raise ReviewAnalysisError("Unexpected batch-006 chunks")
    fixed_outcomes = {
        4580: "excluded speaker-identity mismatch",
        2584: "requires explicit isolation re-review",
        3471: "quality blocked by muffling and extraction artifacts",
        5055: "requires trailing trim",
        973: "requires trailing trim and reference-bank re-review",
    }
    decisions: list[dict[str, Any]] = []
    for chunk_id in expected:
        group = by_chunk[chunk_id]
        winner = choose(group)
        outcome = "exact-line substitution eligible" if winner else fixed_outcomes.get(chunk_id, "human rejected")
        reference_bank = None
        if chunk_id == 561 and winner:
            reference_bank = "approved expressive Doctor reference-bank evidence"
        elif chunk_id == 3189 and winner:
            reference_bank = "direct placement only; explicitly not reference-bank evidence"
        decisions.append(
            {
                "chunk_id": chunk_id,
                "character": group[0]["character"],
                "book_speaker": group[0]["book_speaker"],
                "transcript": group[0]["transcript"],
                "outcome": outcome,
                "selected_candidate_id": winner["candidate_id"] if winner else None,
                "selected_treatment": winner["treatment"] if winner else None,
                "reference_bank_disposition": reference_bank,
            }
        )
    return {
        "round_id": BATCH_ROUND,
        "candidate_count": len(rows),
        "chunk_decisions": decisions,
        "candidates": rows,
    }


def analyze(
    boundary_answer: dict[str, Any],
    boundary_review: dict[str, Any],
    batch_answer: dict[str, Any],
    batch_review: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "boundary_repair_round": analyze_boundary(boundary_answer, boundary_review),
        "batch_006_round": analyze_batch(batch_answer, batch_review),
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
