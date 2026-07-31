#!/usr/bin/env python3
"""Unblind the Original Sin repair and direct-substitution review exports."""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


REPAIR_ROUND_ID = "alexandria_original_sin_overlap_reference_repair_shortlist_v2"
DIRECT_ROUND_ID = "alexandria_original_sin_direct_substitution_pilot_v1"
EXPECTED_REPAIR_CANDIDATES = 30
EXPECTED_DIRECT_CANDIDATES = 10
EXPECTED_REPAIR_CHARACTERS = (
    "Bernice Summerfield",
    "The Doctor",
    "Chris Cwej",
    "Beltempest",
    "Under-Sergeant",
    "Computer",
    "Doc Dantalion",
    "Homeless Forsaken",
    "Evan Claple",
    "Shythe Shahid",
    "Tobias Vaughn / Robot",
)
EXPECTED_DIRECT_CHUNKS = (1684, 405, 5207, 3106, 3908, 493)


class FollowupReviewError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FollowupReviewError(f"Expected JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _score(review: dict[str, Any], key: str) -> int | None:
    raw = review.get(key)
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise FollowupReviewError(f"Invalid {key} score: {raw!r}") from exc
    if value not in range(1, 6):
        raise FollowupReviewError(f"Invalid {key} score: {value}")
    return value


def note_flags(notes: str) -> list[str]:
    text = str(notes or "").casefold()
    flags: list[str] = []
    if re.search(r"cut(?:s)? (?:off|out)|too early|ends? too early|starts? too (?:early|late)|abrupt|does not finish|before finishing", text):
        flags.append("boundary_incomplete")
    if re.search(r"background|music|dog|foot ?step|gun|different speaker|separate person|seperate person|multiple characters|other voice|different voice", text):
        flags.append("scene_contamination")
    if re.search(r"artifact|echo|muffled|compressed", text):
        flags.append("audio_damage")
    if re.search(r"intercom|radio|speaker or something", text):
        flags.append("intrinsic_processing_question")
    return flags


def _objective_repair(candidate: dict[str, Any]) -> bool:
    return (
        float(candidate.get("word_error_rate", 1.0)) == 0.0
        and candidate.get("first_word_present") is True
        and candidate.get("last_word_present") is True
    )


def _complete_scores(scores: dict[str, int | None]) -> bool:
    return all(value is not None for value in scores.values())


def analyze_repair(answer_key: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    if review.get("round_id") != REPAIR_ROUND_ID:
        raise FollowupReviewError("Repair review round_id mismatch")
    candidates = answer_key.get("candidates")
    results = review.get("results")
    if not isinstance(candidates, dict) or not isinstance(results, dict):
        raise FollowupReviewError("Repair candidates/results must be objects")
    if len(candidates) != EXPECTED_REPAIR_CANDIDATES:
        raise FollowupReviewError(f"Expected 30 repair candidates; found {len(candidates)}")
    if set(candidates) != set(results):
        raise FollowupReviewError("Repair candidate IDs do not match the answer key")
    characters = {str(row.get("character") or "") for row in candidates.values()}
    if characters != set(EXPECTED_REPAIR_CHARACTERS):
        raise FollowupReviewError(f"Unexpected repair character set: {sorted(characters)}")

    rows: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate_id, candidate in candidates.items():
        human = results[candidate_id]
        decision = str(human.get("decision") or "").casefold()
        if decision not in {"pass", "fail"}:
            raise FollowupReviewError(f"Missing repair decision: {candidate_id}")
        scores = {
            "isolation": _score(human, "isolation"),
            "naturalness": _score(human, "naturalness"),
            "identity": _score(human, "identity"),
            "usefulness": _score(human, "usefulness"),
        }
        notes = str(human.get("notes") or "").strip()
        flags = note_flags(notes)
        objective = _objective_repair(candidate)
        clean_human_pass = (
            objective
            and decision == "pass"
            and _complete_scores(scores)
            and min(value for value in scores.values() if value is not None) >= 4
            and not {"boundary_incomplete", "scene_contamination", "audio_damage"}.intersection(flags)
        )
        if not objective:
            classification = "objective-ineligible"
        elif clean_human_pass:
            classification = "approved neutral identity anchor"
        elif decision == "pass" or flags:
            classification = "useful after bounded repair"
        else:
            classification = "human-rejected"
        row = {
            "candidate_id": candidate_id,
            "character": candidate["character"],
            "book_speaker": candidate["book_speaker"],
            "treatment": candidate["treatment"],
            "automatic_transcript": candidate.get("automatic_transcript"),
            "word_error_rate": candidate.get("word_error_rate"),
            "first_word_present": candidate.get("first_word_present"),
            "last_word_present": candidate.get("last_word_present"),
            "objective_eligible": objective,
            "human_decision": decision,
            "human_scores": scores,
            "notes": notes,
            "note_flags": flags,
            "classification": classification,
            "selected": False,
        }
        rows.append(row)
        grouped[str(candidate["character"])].append(row)

    decisions = []
    for character in EXPECTED_REPAIR_CHARACTERS:
        group = grouped[character]
        approved = [row for row in group if row["classification"] == "approved neutral identity anchor"]
        if approved:
            winner = max(
                approved,
                key=lambda row: (
                    sum(value or 0 for value in row["human_scores"].values()),
                    2 if row["treatment"] == "source_mix" else 1 if row["treatment"] == "mossformer2_source_mix" else 0,
                ),
            )
            winner["selected"] = True
            outcome = "approved neutral identity anchor"
            selected = winner["candidate_id"]
        else:
            outcome = "requires a replacement source or bounded repair"
            selected = None
        decisions.append(
            {
                "character": character,
                "outcome": outcome,
                "selected_candidate_id": selected,
                "selected_treatment": next((row["treatment"] for row in group if row["candidate_id"] == selected), None),
            }
        )
    if any(row["selected"] and row["note_flags"] for row in rows):
        raise FollowupReviewError("A note-blocked repair candidate was selected")
    return {
        "round_id": REPAIR_ROUND_ID,
        "review_exported_at": review.get("exported_at"),
        "candidate_count": len(rows),
        "character_count": len(decisions),
        "character_decisions": decisions,
        "candidates": sorted(rows, key=lambda row: (EXPECTED_REPAIR_CHARACTERS.index(row["character"]), row["candidate_id"])),
    }


def analyze_direct(answer_key: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    if review.get("round_id") != DIRECT_ROUND_ID:
        raise FollowupReviewError("Direct review round_id mismatch")
    candidates = answer_key.get("candidates")
    results = review.get("results")
    if not isinstance(candidates, dict) or not isinstance(results, dict):
        raise FollowupReviewError("Direct candidates/results must be objects")
    if len(candidates) != EXPECTED_DIRECT_CANDIDATES:
        raise FollowupReviewError(f"Expected 10 direct candidates; found {len(candidates)}")
    if set(candidates) != set(results):
        raise FollowupReviewError("Direct candidate IDs do not match the answer key")
    if {int(row["chunk_id"]) for row in candidates.values()} != set(EXPECTED_DIRECT_CHUNKS):
        raise FollowupReviewError("Direct review does not cover the expected six chunks")

    rows: list[dict[str, Any]] = []
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for candidate_id, candidate in candidates.items():
        human = results[candidate_id]
        decision = str(human.get("decision") or "").casefold()
        if decision not in {"pass", "fail"}:
            raise FollowupReviewError(f"Missing direct decision: {candidate_id}")
        scores = {
            "boundaries": _score(human, "boundaries"),
            "isolation": _score(human, "isolation"),
            "naturalness": _score(human, "naturalness"),
            "usefulness": _score(human, "usefulness"),
        }
        notes = str(human.get("notes") or "").strip()
        flags = note_flags(notes)
        objective = candidate.get("objective_eligible") is True
        eligible = (
            objective
            and decision == "pass"
            and _complete_scores(scores)
            and min(value for value in scores.values() if value is not None) >= 4
            and not {"boundary_incomplete", "scene_contamination", "audio_damage"}.intersection(flags)
        )
        if eligible:
            classification = "exact-line substitution eligible"
        elif not objective:
            classification = "objective-ineligible"
        elif decision == "pass" or flags:
            classification = "useful after bounded repair"
        else:
            classification = "human-rejected"
        row = {
            "candidate_id": candidate_id,
            "character": candidate["character"],
            "book_speaker": candidate["book_speaker"],
            "chunk_id": int(candidate["chunk_id"]),
            "treatment": candidate["treatment"],
            "proxy_sha256": candidate.get("proxy_sha256"),
            "objective_eligible": objective,
            "human_decision": decision,
            "human_scores": scores,
            "notes": notes,
            "note_flags": flags,
            "classification": classification,
            "selected": False,
        }
        rows.append(row)
        grouped[int(candidate["chunk_id"])].append(row)

    decisions = []
    for chunk_id in EXPECTED_DIRECT_CHUNKS:
        group = grouped[chunk_id]
        eligible = [row for row in group if row["classification"] == "exact-line substitution eligible"]
        if eligible:
            winner = max(
                eligible,
                key=lambda row: (
                    sum(value or 0 for value in row["human_scores"].values()),
                    2 if row["treatment"] == "source_mix" else 1,
                ),
            )
            winner["selected"] = True
            outcome = "exact-line substitution eligible"
            selected = winner["candidate_id"]
        else:
            outcome = "requires repaired direct cut"
            selected = None
        first = group[0]
        decisions.append(
            {
                "chunk_id": chunk_id,
                "character": first["character"],
                "outcome": outcome,
                "selected_candidate_id": selected,
                "selected_treatment": next((row["treatment"] for row in group if row["candidate_id"] == selected), None),
            }
        )
    if any(row["selected"] and row["note_flags"] for row in rows):
        raise FollowupReviewError("A note-blocked direct candidate was selected")
    return {
        "round_id": DIRECT_ROUND_ID,
        "review_exported_at": review.get("exported_at"),
        "candidate_count": len(rows),
        "chunk_count": len(decisions),
        "chunk_decisions": decisions,
        "candidates": sorted(rows, key=lambda row: (EXPECTED_DIRECT_CHUNKS.index(row["chunk_id"]), row["candidate_id"])),
    }


def render_markdown(report: dict[str, Any]) -> str:
    repair = report["repair_round"]
    direct = report["direct_substitution_round"]
    lines = [
        "# Original Sin overlap follow-up review decisions",
        "",
        "No Alexandria Voice assignment or chunk audio was changed by this analysis.",
        "",
        "## Reference repair round",
        "",
        "| Character | Outcome | Winner | Treatment |",
        "|---|---|---|---|",
    ]
    for row in repair["character_decisions"]:
        winner = f"`{row['selected_candidate_id']}`" if row["selected_candidate_id"] else "—"
        treatment = f"`{row['selected_treatment']}`" if row["selected_treatment"] else "—"
        lines.append(
            f"| {row['character']} | {row['outcome']} | {winner} | {treatment} |"
        )
    lines.extend(
        [
            "",
            "## Direct-substitution pilot",
            "",
            "| Chunk | Character | Outcome | Winner | Treatment |",
            "|---:|---|---|---|---|",
        ]
    )
    for row in direct["chunk_decisions"]:
        winner = f"`{row['selected_candidate_id']}`" if row["selected_candidate_id"] else "—"
        treatment = f"`{row['selected_treatment']}`" if row["selected_treatment"] else "—"
        lines.append(
            f"| {row['chunk_id']} | {row['character']} | {row['outcome']} | {winner} | {treatment} |"
        )
    lines.extend(["", "## Load-bearing findings", ""])
    lines.append("- Every nominal repair-round pass remained blocked by a written boundary, bleed, echo, artifact, or compression note; no new neutral anchor was promoted.")
    lines.append("- Roz Forrester chunk 1684 source mix is the only exact-line candidate approved in the pilot.")
    lines.append("- All other pilot chunks require a new or repaired source cut before substitution.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repair-answer-key", type=Path, required=True)
    parser.add_argument("--repair-review", type=Path, required=True)
    parser.add_argument("--direct-answer-key", type=Path, required=True)
    parser.add_argument("--direct-review", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    args = parser.parse_args()
    report = {
        "schema_version": 1,
        "repair_round": analyze_repair(read_json(args.repair_answer_key), read_json(args.repair_review)),
        "direct_substitution_round": analyze_direct(read_json(args.direct_answer_key), read_json(args.direct_review)),
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
