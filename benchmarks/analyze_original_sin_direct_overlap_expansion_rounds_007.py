#!/usr/bin/env python3
"""Unblind Original Sin boundary-repair v7 and expansion batch 007."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
from typing import Any


BOUNDARY_ROUND = "alexandria_original_sin_direct_overlap_boundary_repair_v7"
BATCH_ROUND = "alexandria_original_sin_direct_overlap_expansion_batch_007"
SCORE_KEYS = ("boundaries", "isolation", "music_effects", "artifacts", "naturalness", "usefulness")


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
        value = int(row[key])
        if value not in range(1, 6):
            raise ReviewAnalysisError(f"Score outside 1-5: {key}={value}")
        scores[key] = value
    return scores, missing


def note_flags(notes: str) -> list[str]:
    value = str(notes or "").strip().casefold()
    flags: list[str] = []
    if re.search(r"cwej,? not roz|cwej not roz|wrong speaker", value):
        flags.append("wrong_speaker")
    if re.search(r"artifact at the start|artifact at the beginning|before he starts|before .* starts", value):
        flags.append("start_artifact")
    if re.search(r"abrupt|cuts out|before she finishes|end feels", value):
        flags.append("boundary_incomplete")
    if re.search(r"other voices|footstep|another character sound|echo", value):
        flags.append("contamination")
    if re.search(r"muffled|enhanced|cleaned|audio quality", value):
        flags.append("quality_limited")
    return flags


def treatment_rank(treatment: str) -> int:
    return 2 if str(treatment).startswith("mossformer2") else 1


def rows_for_round(answer_key: dict[str, Any], review: dict[str, Any], expected_round: str) -> list[dict[str, Any]]:
    if answer_key.get("round_id") != expected_round or review.get("round_id") != expected_round:
        raise ReviewAnalysisError("round_id mismatch")
    candidates = answer_key.get("candidates")
    results = review.get("results")
    if not isinstance(candidates, dict) or not isinstance(results, dict) or set(candidates) != set(results):
        raise ReviewAnalysisError("Review must account for every blind candidate")
    rows: list[dict[str, Any]] = []
    for candidate_id, source in candidates.items():
        human = results[candidate_id]
        decision = str(human.get("decision") or "").casefold()
        if decision not in {"pass", "fail"}:
            raise ReviewAnalysisError(f"Missing decision: {candidate_id}")
        scores, missing = score_bundle(human)
        notes = str(human.get("notes") or "").strip()
        flags = note_flags(notes)
        clean = decision == "pass" and not missing and all(scores[key] == 5 for key in SCORE_KEYS) and not flags
        rows.append({
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
        })
    return rows


def choose(group: list[dict[str, Any]]) -> dict[str, Any] | None:
    eligible = [row for row in group if row["promotion_eligible"]]
    if not eligible:
        return None
    winner = max(eligible, key=lambda row: (sum(row["human_scores"].values()), treatment_rank(row["treatment"])))
    winner["selected"] = True
    return winner


def analyze_boundary(answer_key: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    rows = rows_for_round(answer_key, review, BOUNDARY_ROUND)
    expected = (2584, 5055, 973)
    by_chunk: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_chunk[row["chunk_id"]].append(row)
    if set(by_chunk) != set(expected):
        raise ReviewAnalysisError("Unexpected boundary-repair v7 chunks")
    fixed = {
        5055: "requires final clarity and tail repair",
        973: "requires final-word in-boundary recovery",
    }
    decisions = []
    for chunk_id in expected:
        group = by_chunk[chunk_id]
        winner = choose(group)
        decisions.append({
            "chunk_id": chunk_id,
            "character": group[0]["character"],
            "book_speaker": group[0]["book_speaker"],
            "transcript": group[0]["transcript"],
            "outcome": "exact-line substitution eligible" if winner else fixed.get(chunk_id, "human rejected"),
            "selected_candidate_id": winner["candidate_id"] if winner else None,
            "selected_treatment": winner["treatment"] if winner else None,
        })
    return {"round_id": BOUNDARY_ROUND, "candidate_count": len(rows), "chunk_decisions": decisions, "candidates": rows}


def analyze_batch(answer_key: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    rows = rows_for_round(answer_key, review, BATCH_ROUND)
    expected = (2080, 2373, 15, 3431, 2144, 3, 1618, 5462, 1731, 3116, 2231, 2398, 3451, 2175, 223, 5198, 426)
    by_chunk: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_chunk[row["chunk_id"]].append(row)
    if set(by_chunk) != set(expected):
        raise ReviewAnalysisError("Unexpected batch-007 chunks")
    fixed = {
        2373: "requires first-word start trim",
        15: "source blocked by adjacent voice and extraction artifacts",
        3: "requires bounded clarity enhancement",
        5462: "requires trailing contamination trim",
        1731: "requires first-word start trim",
        3116: "requires start and final-word repair",
        2231: "requires bounded clarity enhancement",
        2175: "excluded wrong-speaker textual match",
        426: "excluded wrong-speaker textual match",
    }
    decisions = []
    for chunk_id in expected:
        group = by_chunk[chunk_id]
        winner = choose(group)
        outcome = "exact-line substitution eligible" if winner else fixed.get(chunk_id, "human rejected")
        reference_bank = None
        if chunk_id == 2398 and winner:
            reference_bank = "approved expressive Doctor reference-bank evidence"
        if chunk_id == 1731:
            reference_bank = "pending Doctor reference-bank re-review after start repair"
        decisions.append({
            "chunk_id": chunk_id,
            "character": group[0]["character"],
            "book_speaker": group[0]["book_speaker"],
            "transcript": group[0]["transcript"],
            "outcome": outcome,
            "selected_candidate_id": winner["candidate_id"] if winner else None,
            "selected_treatment": winner["treatment"] if winner else None,
            "reference_bank_disposition": reference_bank,
        })
    return {"round_id": BATCH_ROUND, "candidate_count": len(rows), "chunk_decisions": decisions, "candidates": rows}


def analyze(boundary_answer: dict[str, Any], boundary_review: dict[str, Any], batch_answer: dict[str, Any], batch_review: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "boundary_repair_round": analyze_boundary(boundary_answer, boundary_review),
        "batch_007_round": analyze_batch(batch_answer, batch_review),
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
    write_json(args.output, analyze(read_json(args.boundary_answer), read_json(args.boundary_review), read_json(args.batch_answer), read_json(args.batch_review)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
