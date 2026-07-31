#!/usr/bin/env python3
"""Unblind Original Sin boundary-repair v8 and expansion batch 008."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
from typing import Any


BOUNDARY_ROUND = "alexandria_original_sin_direct_overlap_boundary_repair_v8"
BATCH_ROUND = "alexandria_original_sin_direct_overlap_expansion_batch_008"
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
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


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
    if re.search(r"benny not roz|wrong speaker|not roz", value):
        flags.append("wrong_speaker")
    if re.search(r"artifact at the start|artifact sound at the start|before she says|muddy section at the start", value):
        flags.append("start_artifact")
    if re.search(r"ends abruptly|ends too abruptly|ends slightly too early|finished saying|before he has finished", value):
        flags.append("boundary_incomplete")
    if re.search(r"sound at the very end|artifact at the end|another character beginning", value):
        flags.append("trailing_contamination")
    if re.search(r"muddy|muffled|strangled|cleaned for clarity|needs a bit more clarity|clean it up", value):
        flags.append("quality_limited")
    if re.search(r"background sounds", value):
        flags.append("contamination")
    return flags


def treatment_rank(treatment: str) -> tuple[int, int]:
    value = str(treatment)
    extraction = 2 if value.startswith("mossformer2") else 1 if value.startswith("mel_roformer") else 0
    postroll = re.search(r"postroll([0-9.]+)", value)
    preroll = re.search(r"preroll([0-9.]+)", value)
    margin = float(postroll.group(1)) if postroll else float(preroll.group(1)) if preroll else 0.0
    return extraction, -int(round(margin * 1000))


def rows_for_round(
    answer_key: dict[str, Any],
    review: dict[str, Any],
    expected_round: str,
) -> list[dict[str, Any]]:
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
        scores, missing = score_bundle(human)
        notes = str(human.get("notes") or "").strip()
        flags = note_flags(notes)
        clean = (
            decision == "pass"
            and not missing
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
                "missing_scores": missing,
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


def decision_rows(
    rows: list[dict[str, Any]],
    expected: tuple[int, ...],
    fixed: dict[int, str],
    reference: dict[int, str],
) -> list[dict[str, Any]]:
    by_chunk: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_chunk[row["chunk_id"]].append(row)
    if set(by_chunk) != set(expected):
        raise ReviewAnalysisError("Unexpected review chunks")
    decisions: list[dict[str, Any]] = []
    for chunk_id in expected:
        group = by_chunk[chunk_id]
        winner = choose(group)
        outcome = "exact-line substitution eligible" if winner else fixed.get(chunk_id, "human rejected")
        decisions.append(
            {
                "chunk_id": chunk_id,
                "character": group[0]["character"],
                "book_speaker": group[0]["book_speaker"],
                "transcript": group[0]["transcript"],
                "outcome": outcome,
                "selected_candidate_id": winner["candidate_id"] if winner else None,
                "selected_treatment": winner["treatment"] if winner else None,
                "reference_bank_disposition": reference.get(chunk_id),
            }
        )
    return decisions


def analyze_boundary(answer_key: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    rows = rows_for_round(answer_key, review, BOUNDARY_ROUND)
    expected = (5055, 973, 2373, 3, 5462, 1731, 3116, 2231)
    fixed = {
        5055: "requires final clarity-preserving tail cleanup",
        973: "source blocked after in-boundary final-word recovery",
        3: "quality blocked after bounded clarity enhancement",
        3116: "requires final trailing-artifact cleanup",
        2231: "quality blocked after bounded clarity enhancement",
    }
    reference = {
        5462: "approved expressive Doctor reference-bank evidence",
        1731: "approved expressive Doctor reference-bank evidence with strong rolled-R evidence",
    }
    return {
        "round_id": BOUNDARY_ROUND,
        "candidate_count": len(rows),
        "chunk_decisions": decision_rows(rows, expected, fixed, reference),
        "candidates": rows,
    }


def analyze_batch(answer_key: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    rows = rows_for_round(answer_key, review, BATCH_ROUND)
    expected = (365, 1210, 1801, 1897, 1939, 2394, 2840, 3016, 3025, 4071, 4443, 4715, 4888, 4907)
    fixed = {
        365: "requires start trim and bounded clarity enhancement",
        1801: "requires bounded clarity enhancement",
        1897: "source blocked by mud and background sound",
        2840: "excluded wrong-speaker textual match",
        3016: "requires clarity and final-word repair",
        3025: "requires bounded artifact cleanup",
        4071: "requires first-word start trim",
        4443: "requires first-word start trim",
        4715: "requires final-word tail repair",
        4888: "requires final-word tail repair",
        4907: "requires start trim and bounded clarity enhancement",
    }
    reference = {
        1939: "approved Bernice reference-bank evidence",
        4888: "pending expressive Doctor reference-bank re-review after tail repair",
    }
    return {
        "round_id": BATCH_ROUND,
        "candidate_count": len(rows),
        "chunk_decisions": decision_rows(rows, expected, fixed, reference),
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
        "batch_008_round": analyze_batch(batch_answer, batch_review),
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
