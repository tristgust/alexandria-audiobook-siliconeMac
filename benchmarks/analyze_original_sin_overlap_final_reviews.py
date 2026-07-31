#!/usr/bin/env python3
"""Unblind the final Original Sin repair reviews without production mutation."""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

REFERENCE_ROUND_ID = "alexandria_original_sin_overlap_reference_final_repair_v4"
DIRECT_ROUND_ID = "alexandria_original_sin_direct_substitution_final_repair_v3"
REFERENCE_CHARACTERS = (
    "The Doctor", "Under-Sergeant", "Computer", "Evan Claple",
    "Shythe Shahid", "Tobias Vaughn / Robot",
)
DIRECT_CHUNKS = (5207, 3908, 3098, 618)

NEUTRAL = "approved neutral identity anchor"
PERFORMANCE = "approved performance-only reference"
REPAIR = "requires a replacement source or bounded repair"
EXACT = "exact-line substitution eligible"
DIRECT_REPAIR = "requires repaired direct cut"


class FinalReviewError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FinalReviewError(f"Expected object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def score(row: dict[str, Any], key: str) -> int:
    try:
        value = int(row.get(key, 0))
    except (TypeError, ValueError) as exc:
        raise FinalReviewError(f"Invalid {key}: {row.get(key)!r}") from exc
    if value not in range(1, 6):
        raise FinalReviewError(f"Missing {key}")
    return value


def flags(notes: str) -> list[str]:
    value = str(notes or "").casefold()
    found: list[str] = []
    if re.search(r"music|background|barking|dogs?", value):
        found.append("contamination")
    if re.search(r"echo|artifact|muffled|lost|not as clear|hard to understand", value):
        found.append("damage")
    if re.search(r"cut(?:s)? off|too early|too soon|is classify|before .* done", value):
        found.append("boundary")
    return found


def objective(candidate: dict[str, Any]) -> bool:
    return (
        candidate.get("objective_eligible") is not False
        and float(candidate.get("word_error_rate", 0.0)) == 0.0
        and candidate.get("first_word_present", True) is True
        and candidate.get("last_word_present", True) is True
    )


def treatment_rank(name: str) -> int:
    return {
        "source_mix": 5, "center_channel_mid": 4,
        "mossformer2_source_mix": 3, "mel_roformer_vocal": 2,
    }.get(str(name), 0)


def analyze_reference(answer: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    if answer.get("round_id") != REFERENCE_ROUND_ID or review.get("round_id") != REFERENCE_ROUND_ID:
        raise FinalReviewError("reference round mismatch")
    candidates, results = answer.get("candidates"), review.get("results")
    if not isinstance(candidates, dict) or not isinstance(results, dict):
        raise FinalReviewError("reference payload malformed")
    if len(candidates) != 16 or set(candidates) != set(results):
        raise FinalReviewError("all 16 reference candidates are required")
    if {str(row.get("character")) for row in candidates.values()} != set(REFERENCE_CHARACTERS):
        raise FinalReviewError("reference character mismatch")

    rows, grouped = [], defaultdict(list)
    for candidate_id, candidate in candidates.items():
        human = results[candidate_id]
        decision = str(human.get("decision") or "").casefold()
        if decision not in {"pass", "fail"}:
            raise FinalReviewError(f"missing decision: {candidate_id}")
        ratings = {k: score(human, k) for k in ("isolation", "naturalness", "identity", "usefulness")}
        notes = str(human.get("notes") or "").strip()
        note_flags = flags(notes)
        eligible = objective(candidate)
        if not eligible:
            classification = "objective-ineligible"
        elif decision != "pass":
            classification = "human-rejected"
        elif note_flags:
            classification = REPAIR
        elif min(ratings.values()) >= 4:
            classification = NEUTRAL
        elif ratings["identity"] >= 4 and ratings["usefulness"] >= 3:
            classification = PERFORMANCE
        else:
            classification = REPAIR
        row = {
            "candidate_id": candidate_id,
            "character": str(candidate["character"]),
            "book_speaker": str(candidate.get("book_speaker") or ""),
            "treatment": str(candidate.get("treatment") or ""),
            "objective_eligible": eligible,
            "human_decision": decision,
            "human_scores": ratings,
            "notes": notes,
            "note_flags": note_flags,
            "classification": classification,
            "selected": False,
        }
        rows.append(row)
        grouped[row["character"]].append(row)

    decisions = []
    for character in REFERENCE_CHARACTERS:
        available = [r for r in grouped[character] if r["classification"] in {NEUTRAL, PERFORMANCE}]
        winner = max(
            available,
            key=lambda r: (
                r["classification"] == NEUTRAL,
                sum(r["human_scores"].values()),
                r["human_scores"]["isolation"],
                treatment_rank(r["treatment"]),
            ),
        ) if available else None
        if winner:
            winner["selected"] = True
        decisions.append({
            "character": character,
            "outcome": winner["classification"] if winner else REPAIR,
            "selected_candidate_id": winner["candidate_id"] if winner else None,
            "selected_treatment": winner["treatment"] if winner else None,
        })
    return {
        "round_id": REFERENCE_ROUND_ID,
        "review_exported_at": review.get("exported_at"),
        "candidate_count": len(rows),
        "character_decisions": decisions,
        "candidates": rows,
    }


def analyze_direct(answer: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    if answer.get("round_id") != DIRECT_ROUND_ID or review.get("round_id") != DIRECT_ROUND_ID:
        raise FinalReviewError("direct round mismatch")
    candidates, results = answer.get("candidates"), review.get("results")
    if not isinstance(candidates, dict) or not isinstance(results, dict):
        raise FinalReviewError("direct payload malformed")
    if len(candidates) != 10 or set(candidates) != set(results):
        raise FinalReviewError("all 10 direct candidates are required")
    if {int(row.get("chunk_id", -1)) for row in candidates.values()} != set(DIRECT_CHUNKS):
        raise FinalReviewError("direct chunk mismatch")

    rows, grouped = [], defaultdict(list)
    for candidate_id, candidate in candidates.items():
        human = results[candidate_id]
        decision = str(human.get("decision") or "").casefold()
        ratings = {k: score(human, k) for k in ("boundaries", "isolation", "naturalness", "usefulness")}
        notes = str(human.get("notes") or "").strip()
        note_flags = flags(notes)
        classification = (
            EXACT if objective(candidate) and decision == "pass" and not note_flags and min(ratings.values()) >= 4
            else DIRECT_REPAIR
        )
        row = {
            "candidate_id": candidate_id,
            "chunk_id": int(candidate["chunk_id"]),
            "character": str(candidate["character"]),
            "treatment": str(candidate.get("treatment") or ""),
            "human_scores": ratings,
            "notes": notes,
            "note_flags": note_flags,
            "classification": classification,
            "selected": False,
        }
        rows.append(row)
        grouped[row["chunk_id"]].append(row)

    decisions = []
    for chunk_id in DIRECT_CHUNKS:
        available = [r for r in grouped[chunk_id] if r["classification"] == EXACT]
        winner = max(
            available,
            key=lambda r: (sum(r["human_scores"].values()), treatment_rank(r["treatment"])),
        ) if available else None
        if winner:
            winner["selected"] = True
        decisions.append({
            "chunk_id": chunk_id,
            "character": grouped[chunk_id][0]["character"],
            "outcome": EXACT if winner else DIRECT_REPAIR,
            "selected_candidate_id": winner["candidate_id"] if winner else None,
            "selected_treatment": winner["treatment"] if winner else None,
        })
    return {
        "round_id": DIRECT_ROUND_ID,
        "review_exported_at": review.get("exported_at"),
        "candidate_count": len(rows),
        "chunk_decisions": decisions,
        "candidates": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-answer-key", type=Path, required=True)
    parser.add_argument("--reference-review", type=Path, required=True)
    parser.add_argument("--direct-answer-key", type=Path, required=True)
    parser.add_argument("--direct-review", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    args = parser.parse_args()
    report = {
        "schema_version": 1,
        "reference_round": analyze_reference(read_json(args.reference_answer_key), read_json(args.reference_review)),
        "direct_round": analyze_direct(read_json(args.direct_answer_key), read_json(args.direct_review)),
        "production_changes": False,
        "project_voice_config_changed": False,
        "project_chunks_changed": False,
    }
    write_json(args.output_json, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
