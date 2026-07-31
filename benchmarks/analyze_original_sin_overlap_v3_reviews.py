#!/usr/bin/env python3
"""Unblind the Original Sin reference-v3 and direct-repair-v2 reviews.

This analyzer is evidence-only. It never writes Alexandria project state and
does not install Voice references or chunk audio.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


REFERENCE_ROUND_ID = "alexandria_original_sin_overlap_reference_repair_v3"
DIRECT_ROUND_ID = "alexandria_original_sin_direct_substitution_repair_v2"
EXPECTED_REFERENCE_COUNT = 24
EXPECTED_DIRECT_COUNT = 14
EXPECTED_REFERENCE_CHARACTERS = (
    "Bernice Summerfield",
    "The Doctor",
    "Chris Cwej",
    "Beltempest",
    "Under-Sergeant",
    "Computer",
    "Doc Dantalion",
    "Evan Claple",
    "Shythe Shahid",
    "Tobias Vaughn / Robot",
)
EXPECTED_DIRECT_CHUNKS = (405, 5207, 3106, 3908, 493)

OUTCOME_NEUTRAL = "approved neutral identity anchor"
OUTCOME_PERFORMANCE = "approved performance-only reference"
OUTCOME_REPAIR = "requires a replacement source or bounded repair"
OUTCOME_EXACT = "exact-line substitution eligible"
OUTCOME_DIRECT_REPAIR = "requires repaired direct cut"


class V3ReviewError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise V3ReviewError(f"Expected JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _score(review: dict[str, Any], key: str) -> int:
    try:
        value = int(review.get(key, 0))
    except (TypeError, ValueError) as exc:
        raise V3ReviewError(f"Invalid {key} score: {review.get(key)!r}") from exc
    if value not in range(1, 6):
        raise V3ReviewError(f"Missing or invalid {key} score: {value!r}")
    return value


def note_flags(notes: str) -> list[str]:
    value = str(notes or "").strip().casefold()
    flags: list[str] = []
    if not value:
        return flags
    if re.search(r"background|music|barking|dogs?|gun clicks?|beep", value):
        flags.append("scene_contamination")
    if re.search(r"artifact|echo|muffled|compressed", value):
        flags.append("audio_damage")
    if re.search(r"cut(?:s)? off|too soon|too early|starts too early", value):
        flags.append("boundary_incomplete")
    if re.search(r"does not sound like the same character|separate person's voice|different speaker", value):
        flags.append("other_speaker")
    if "intercom overlay" in value:
        flags.append("scene_specific_intercom")
    if "hard to understand" in value and "workable" in value:
        flags.append("restricted_intelligibility")
    return flags


def _objective(candidate: dict[str, Any]) -> bool:
    if candidate.get("objective_eligible") is False:
        return False
    if "word_error_rate" in candidate and float(candidate["word_error_rate"]) != 0.0:
        return False
    if "first_word_present" in candidate and candidate.get("first_word_present") is not True:
        return False
    if "last_word_present" in candidate and candidate.get("last_word_present") is not True:
        return False
    return True


def _treatment_rank(treatment: str) -> int:
    return {
        "source_mix": 5,
        "center_channel_mid": 4,
        "mossformer2_source_mix": 3,
        "mossformer2_blend70": 2,
        "mel_roformer_blend70": 1,
        "mel_roformer_vocal": 0,
    }.get(str(treatment), -1)


def analyze_reference(answer_key: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    if answer_key.get("round_id") != REFERENCE_ROUND_ID or review.get("round_id") != REFERENCE_ROUND_ID:
        raise V3ReviewError("Reference round_id mismatch")
    candidates = answer_key.get("candidates")
    results = review.get("results")
    if not isinstance(candidates, dict) or not isinstance(results, dict):
        raise V3ReviewError("Reference candidates/results must be objects")
    if len(candidates) != EXPECTED_REFERENCE_COUNT or set(candidates) != set(results):
        raise V3ReviewError("Reference review must account for all 24 candidates")
    characters = {str(row.get("character")) for row in candidates.values()}
    if characters != set(EXPECTED_REFERENCE_CHARACTERS):
        raise V3ReviewError(f"Reference character mismatch: {sorted(characters)}")

    rows: list[dict[str, Any]] = []
    by_character: dict[str, list[dict[str, Any]]] = defaultdict(list)
    blockers = {
        "scene_contamination",
        "audio_damage",
        "boundary_incomplete",
        "other_speaker",
        "scene_specific_intercom",
    }
    for candidate_id, candidate in candidates.items():
        human = results[candidate_id]
        decision = str(human.get("decision") or "").casefold()
        if decision not in {"pass", "fail"}:
            raise V3ReviewError(f"Missing reference decision: {candidate_id}")
        scores = {
            "isolation": _score(human, "isolation"),
            "naturalness": _score(human, "naturalness"),
            "identity": _score(human, "identity"),
            "usefulness": _score(human, "usefulness"),
        }
        notes = str(human.get("notes") or "").strip()
        flags = note_flags(notes)
        objective = _objective(candidate)
        if not objective:
            classification = "objective-ineligible"
        elif decision != "pass":
            classification = "human-rejected"
        elif blockers.intersection(flags):
            classification = OUTCOME_REPAIR
        elif "restricted_intelligibility" in flags:
            classification = OUTCOME_PERFORMANCE
        elif min(scores.values()) >= 4:
            classification = OUTCOME_NEUTRAL
        elif scores["identity"] >= 4 and scores["usefulness"] >= 3:
            classification = OUTCOME_PERFORMANCE
        else:
            classification = OUTCOME_REPAIR
        row = {
            "candidate_id": candidate_id,
            "character": str(candidate["character"]),
            "book_speaker": str(candidate.get("book_speaker") or ""),
            "treatment": str(candidate.get("treatment") or ""),
            "objective_eligible": objective,
            "human_decision": decision,
            "human_scores": scores,
            "notes": notes,
            "note_flags": flags,
            "classification": classification,
            "selected": False,
        }
        rows.append(row)
        by_character[row["character"]].append(row)

    decisions = []
    for character in EXPECTED_REFERENCE_CHARACTERS:
        group = by_character[character]
        selectable = [
            row for row in group
            if row["classification"] in {OUTCOME_NEUTRAL, OUTCOME_PERFORMANCE}
        ]
        winner = None
        if selectable:
            winner = max(
                selectable,
                key=lambda row: (
                    2 if row["classification"] == OUTCOME_NEUTRAL else 1,
                    sum(row["human_scores"].values()),
                    row["human_scores"]["isolation"],
                    row["human_scores"]["usefulness"],
                    _treatment_rank(row["treatment"]),
                ),
            )
            winner["selected"] = True
        decisions.append(
            {
                "character": character,
                "outcome": winner["classification"] if winner else OUTCOME_REPAIR,
                "selected_candidate_id": winner["candidate_id"] if winner else None,
                "selected_treatment": winner["treatment"] if winner else None,
            }
        )
    if any(not row["objective_eligible"] for row in rows if row["selected"]):
        raise V3ReviewError("Objective-ineligible reference candidate selected")
    return {
        "round_id": REFERENCE_ROUND_ID,
        "review_exported_at": review.get("exported_at"),
        "candidate_count": len(rows),
        "character_count": len(decisions),
        "character_decisions": decisions,
        "candidates": sorted(rows, key=lambda row: (EXPECTED_REFERENCE_CHARACTERS.index(row["character"]), row["candidate_id"])),
    }


def analyze_direct(answer_key: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    if answer_key.get("round_id") != DIRECT_ROUND_ID or review.get("round_id") != DIRECT_ROUND_ID:
        raise V3ReviewError("Direct round_id mismatch")
    candidates = answer_key.get("candidates")
    results = review.get("results")
    if not isinstance(candidates, dict) or not isinstance(results, dict):
        raise V3ReviewError("Direct candidates/results must be objects")
    if len(candidates) != EXPECTED_DIRECT_COUNT or set(candidates) != set(results):
        raise V3ReviewError("Direct review must account for all 14 candidates")
    chunks = {int(row.get("chunk_id", -1)) for row in candidates.values()}
    if chunks != set(EXPECTED_DIRECT_CHUNKS):
        raise V3ReviewError(f"Direct chunk mismatch: {sorted(chunks)}")

    blockers = {
        "scene_contamination",
        "audio_damage",
        "boundary_incomplete",
        "other_speaker",
    }
    rows: list[dict[str, Any]] = []
    by_chunk: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for candidate_id, candidate in candidates.items():
        human = results[candidate_id]
        decision = str(human.get("decision") or "").casefold()
        if decision not in {"pass", "fail"}:
            raise V3ReviewError(f"Missing direct decision: {candidate_id}")
        scores = {
            "boundaries": _score(human, "boundaries"),
            "isolation": _score(human, "isolation"),
            "naturalness": _score(human, "naturalness"),
            "usefulness": _score(human, "usefulness"),
        }
        notes = str(human.get("notes") or "").strip()
        flags = note_flags(notes)
        objective = _objective(candidate)
        if (
            objective
            and decision == "pass"
            and not blockers.intersection(flags)
            and min(scores.values()) >= 4
        ):
            classification = OUTCOME_EXACT
        elif not objective:
            classification = "objective-ineligible"
        else:
            classification = OUTCOME_DIRECT_REPAIR
        row = {
            "candidate_id": candidate_id,
            "chunk_id": int(candidate["chunk_id"]),
            "character": str(candidate["character"]),
            "book_speaker": str(candidate.get("book_speaker") or ""),
            "treatment": str(candidate.get("treatment") or ""),
            "objective_eligible": objective,
            "human_decision": decision,
            "human_scores": scores,
            "notes": notes,
            "note_flags": flags,
            "classification": classification,
            "selected": False,
        }
        rows.append(row)
        by_chunk[row["chunk_id"]].append(row)

    decisions = []
    for chunk_id in EXPECTED_DIRECT_CHUNKS:
        group = by_chunk[chunk_id]
        selectable = [row for row in group if row["classification"] == OUTCOME_EXACT]
        winner = None
        if selectable:
            winner = max(
                selectable,
                key=lambda row: (
                    sum(row["human_scores"].values()),
                    row["human_scores"]["isolation"],
                    _treatment_rank(row["treatment"]),
                ),
            )
            winner["selected"] = True
        decisions.append(
            {
                "chunk_id": chunk_id,
                "character": group[0]["character"],
                "outcome": OUTCOME_EXACT if winner else OUTCOME_DIRECT_REPAIR,
                "selected_candidate_id": winner["candidate_id"] if winner else None,
                "selected_treatment": winner["treatment"] if winner else None,
            }
        )
    return {
        "round_id": DIRECT_ROUND_ID,
        "review_exported_at": review.get("exported_at"),
        "candidate_count": len(rows),
        "chunk_count": len(decisions),
        "chunk_decisions": decisions,
        "candidates": sorted(rows, key=lambda row: (EXPECTED_DIRECT_CHUNKS.index(row["chunk_id"]), row["candidate_id"])),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Original Sin v3 reference and direct-repair decisions",
        "",
        "No Alexandria Voice assignment, reference-bank approval, or chunk audio was changed by this analysis.",
        "",
        "## Reference decisions",
        "",
        "| Character | Outcome | Winner | Treatment |",
        "|---|---|---|---|",
    ]
    for row in report["reference_round"]["character_decisions"]:
        winner = f"`{row['selected_candidate_id']}`" if row["selected_candidate_id"] else "—"
        treatment = f"`{row['selected_treatment']}`" if row["selected_treatment"] else "—"
        lines.append(f"| {row['character']} | {row['outcome']} | {winner} | {treatment} |")
    lines.extend([
        "",
        "## Direct-substitution decisions",
        "",
        "| Chunk | Character | Outcome | Winner | Treatment |",
        "|---:|---|---|---|---|",
    ])
    for row in report["direct_round"]["chunk_decisions"]:
        winner = f"`{row['selected_candidate_id']}`" if row["selected_candidate_id"] else "—"
        treatment = f"`{row['selected_treatment']}`" if row["selected_treatment"] else "—"
        lines.append(f"| {row['chunk_id']} | {row['character']} | {row['outcome']} | {winner} | {treatment} |")
    lines.extend([
        "",
        "## Interpretation",
        "",
        "- Bernice Summerfield and Chris Cwej gained clean neutral anchors.",
        "- Beltempest gained a performance-only reference because intelligibility was rated workable rather than fully clear.",
        "- Under-Sergeant is not intercom-only in the book; the intercom-heavy adaptation clip is not a neutral identity anchor.",
        "- Rashid chunk 405 is exact-line substitution eligible.",
        "- All other nominal passes remain blocked by their written notes.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-answer-key", type=Path, required=True)
    parser.add_argument("--reference-review", type=Path, required=True)
    parser.add_argument("--direct-answer-key", type=Path, required=True)
    parser.add_argument("--direct-review", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
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
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.write_text(render_markdown(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
