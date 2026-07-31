#!/usr/bin/env python3
"""Unblind batch-001 timing repair and direct-overlap expansion batch 002.

Direct substitution uses a strict voice-only contract: every scored dimension
must be 5/5, the reviewer must pass the candidate, and no written note may
report clipping, adjacent breath/voice, music/effects, echo, or extraction
artifacts. Lower-but-useful clean passes may be retained as reference-bank
evidence but are not direct substitutions.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
from typing import Any


TIMING_ROUND_ID = "alexandria_original_sin_direct_overlap_expansion_batch_001_timing_repair_v2"
BATCH2_ROUND_ID = "alexandria_original_sin_direct_overlap_expansion_batch_002"
TIMING_CHUNKS = (5351, 696, 1261, 2741, 2745, 90, 2090, 4764, 3285)
BATCH2_CHUNKS = (1590, 2720, 5375, 218, 5037, 1247, 3080, 5371, 11, 5020, 1320, 3161, 2002, 2919, 3273)

OUTCOME_DIRECT = "exact-line substitution eligible"
OUTCOME_REFERENCE = "reference-bank evidence only"
OUTCOME_START = "requires first-word start trim"
OUTCOME_TAIL = "requires final-word tail repair"
OUTCOME_START_TAIL = "requires start trim and final-word tail repair"
OUTCOME_TRAILING = "requires trailing overrun trim"
OUTCOME_CONTAMINATION = "requires contamination/source repair"
OUTCOME_WRONG_SPEAKER = "excluded wrong-speaker textual match"


class FollowupReviewError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FollowupReviewError(f"Expected object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def score(row: dict[str, Any], key: str) -> int:
    try:
        value = int(row[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise FollowupReviewError(f"Missing or invalid {key}: {row.get(key)!r}") from exc
    if value not in range(1, 6):
        raise FollowupReviewError(f"Score outside 1-5: {key}={value}")
    return value


def note_flags(notes: str) -> list[str]:
    value = str(notes or "").strip().casefold()
    flags: list[str] = []
    if re.search(r"artifact at the (?:very )?start|sound at the (?:very )?start|start when he says", value):
        flags.append("start_artifact")
    if re.search(r"cut(?:s)? off|abrupt|before he is done|escape$|final word|ends a bit", value):
        flags.append("tail_incomplete")
    if re.search(r"someone else.*breath|breath in the last|other voice|adjacent", value):
        flags.append("trailing_other_voice")
    if re.search(r"gun sound|music|echo|background|vague .*sound", value):
        flags.append("contamination")
    if re.search(r"doctor not zebulon|this is the doctor", value):
        flags.append("wrong_speaker")
    return flags


def treatment_rank(treatment: str) -> int:
    return {"mossformer2_source_mix": 2, "mel_roformer_vocal": 1}.get(str(treatment), 0)


def analyze_round(
    *,
    answer_key: dict[str, Any],
    review: dict[str, Any],
    round_id: str,
    expected_chunks: tuple[int, ...],
) -> dict[str, Any]:
    if answer_key.get("round_id") != round_id or review.get("round_id") != round_id:
        raise FollowupReviewError(f"round_id mismatch: {round_id}")
    candidates = answer_key.get("candidates")
    results = review.get("results")
    if not isinstance(candidates, dict) or not isinstance(results, dict):
        raise FollowupReviewError("Candidates/results must be objects")
    if set(candidates) != set(results):
        raise FollowupReviewError(f"Review does not account for every candidate: {round_id}")

    rows: list[dict[str, Any]] = []
    by_chunk: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for candidate_id, source in candidates.items():
        human = results[candidate_id]
        decision = str(human.get("decision") or "").casefold()
        notes = str(human.get("notes") or "").strip()
        flags = note_flags(notes)
        if decision not in {"pass", "fail"}:
            raise FollowupReviewError(f"Missing decision: {candidate_id}")
        if set(human).isdisjoint({"boundaries", "isolation", "music_effects", "artifacts", "naturalness", "usefulness"}):
            scores = None
        else:
            scores = {
                key: score(human, key)
                for key in ("boundaries", "isolation", "music_effects", "artifacts", "naturalness", "usefulness")
            }
        direct = bool(
            decision == "pass"
            and scores
            and all(value == 5 for value in scores.values())
            and not flags
        )
        reference_only = bool(
            decision == "pass"
            and scores
            and min(scores.values()) >= 4
            and not flags
            and not direct
        )
        row = {
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
            "direct_promotion_eligible": direct,
            "reference_bank_eligible": reference_only,
            "selected": False,
        }
        rows.append(row)
        by_chunk[row["chunk_id"]].append(row)

    if set(by_chunk) != set(expected_chunks):
        raise FollowupReviewError(f"Unexpected chunks for {round_id}: {sorted(by_chunk)}")

    decisions: list[dict[str, Any]] = []
    for chunk_id in expected_chunks:
        group = by_chunk[chunk_id]
        direct = [row for row in group if row["direct_promotion_eligible"]]
        reference = [row for row in group if row["reference_bank_eligible"]]
        winner = None
        if direct:
            winner = max(direct, key=lambda row: treatment_rank(row["treatment"]))
            winner["selected"] = True
            outcome = OUTCOME_DIRECT
        elif any("wrong_speaker" in row["note_flags"] for row in group):
            outcome = OUTCOME_WRONG_SPEAKER
        else:
            flags = {flag for row in group for flag in row["note_flags"]}
            if "start_artifact" in flags and "tail_incomplete" in flags:
                outcome = OUTCOME_START_TAIL
            elif "start_artifact" in flags:
                outcome = OUTCOME_START
            elif "tail_incomplete" in flags:
                outcome = OUTCOME_TAIL
            elif "trailing_other_voice" in flags:
                outcome = OUTCOME_TRAILING
            elif reference:
                outcome = OUTCOME_REFERENCE
                winner = max(
                    reference,
                    key=lambda row: (sum(row["human_scores"].values()), treatment_rank(row["treatment"])),
                )
                winner["selected"] = True
            else:
                outcome = OUTCOME_CONTAMINATION
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
        "round_id": round_id,
        "review_exported_at": review.get("exported_at"),
        "candidate_count": len(rows),
        "chunk_count": len(decisions),
        "chunk_decisions": decisions,
        "candidates": rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Original Sin direct-overlap expansion follow-up decisions",
        "",
        "Direct substitution requires 5/5 in every scored category and no blocking written note.",
        "No Alexandria project state was changed.",
        "",
    ]
    for section_key, title in (("timing_repair_round", "Batch 001 timing repair"), ("batch_002_round", "Batch 002")):
        lines.extend([f"## {title}", "", "| Chunk | Character | Outcome | Winner | Treatment |", "|---:|---|---|---|---|"])
        for row in report[section_key]["chunk_decisions"]:
            winner = f"`{row['selected_candidate_id']}`" if row["selected_candidate_id"] else "—"
            treatment = f"`{row['selected_treatment']}`" if row["selected_treatment"] else "—"
            lines.append(f"| {row['chunk_id']} | {row['character']} | {row['outcome']} | {winner} | {treatment} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timing-answer-key", type=Path, required=True)
    parser.add_argument("--timing-review", type=Path, required=True)
    parser.add_argument("--batch2-answer-key", type=Path, required=True)
    parser.add_argument("--batch2-review", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    args = parser.parse_args()
    report = {
        "schema_version": 1,
        "timing_repair_round": analyze_round(
            answer_key=read_json(args.timing_answer_key),
            review=read_json(args.timing_review),
            round_id=TIMING_ROUND_ID,
            expected_chunks=TIMING_CHUNKS,
        ),
        "batch_002_round": analyze_round(
            answer_key=read_json(args.batch2_answer_key),
            review=read_json(args.batch2_review),
            round_id=BATCH2_ROUND_ID,
            expected_chunks=BATCH2_CHUNKS,
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
