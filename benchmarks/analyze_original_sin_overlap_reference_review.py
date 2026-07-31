#!/usr/bin/env python3
"""Unblind and classify the Original Sin overlap-reference cleanliness round.

The analyzer is deliberately read-only with respect to Alexandria project state.
It consumes a private answer key plus a completed blind-review export and writes
only the explicitly requested report files.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROUND_ID = "alexandria_original_sin_overlap_reference_cleanliness_v1"
EXPECTED_CANDIDATE_COUNT = 51
EXPECTED_CHARACTERS = (
    "Bernice Summerfield",
    "The Doctor",
    "Chris Cwej",
    "Roz Forrester",
    "Beltempest",
    "Under-Sergeant",
    "Rashid",
    "Computer",
    "Doc Dantalion",
    "Homeless Forsaken",
    "Powerless Friendless",
    "Zebulon Pryce",
    "Hater of Humans",
    "Evan Claple",
    "Shythe Shahid",
    "Securitybot",
    "Tobias Vaughn / Robot",
)
EXPECTED_TREATMENTS = {
    "source_mix",
    "mel_roformer_vocal",
    "mossformer2_source_mix",
}

OUTCOME_NEUTRAL = "approved neutral identity anchor"
OUTCOME_PERFORMANCE = "approved performance-only reference"
OUTCOME_EXACT = "exact-line substitution eligible"
OUTCOME_REPAIR = "useful after bounded repair"
OUTCOME_INTRINSIC = "reference-only because scene processing is intrinsic"
OUTCOME_NONE = "no usable candidate yet"
OUTCOME_REPLACE = "requires a replacement source or new extraction"


class ReviewAnalysisError(RuntimeError):
    """Raised when the answer key and blind export do not form a complete round."""


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReviewAnalysisError(f"Expected a JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _score(review: dict[str, Any], name: str) -> int:
    try:
        value = int(review.get(name, 0))
    except (TypeError, ValueError) as exc:
        raise ReviewAnalysisError(f"Invalid {name} score: {review.get(name)!r}") from exc
    if value not in range(1, 6):
        raise ReviewAnalysisError(f"Missing or invalid {name} score: {value!r}")
    return value


def _contains(notes: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, notes, flags=re.IGNORECASE) for pattern in patterns)


def classify_notes(notes: str) -> list[str]:
    """Return load-bearing review-note flags in priority order."""
    normalized = str(notes or "").strip()
    if not normalized:
        return []

    flags: list[str] = []
    if _contains(
        normalized,
        (
            r"computery sounds?.*part of (?:her|his|the) voice",
            r"character[- ]intrinsic",
            r"intrinsic (?:computer|robot|synthetic)",
        ),
    ):
        flags.append("intrinsic_scene_processing")

    if _contains(
        normalized,
        (
            r"actual voice",
            r"imitating a robot",
            r"talking over a radio",
            r"radio or speaker",
            r"is that just how .* sounds?",
        ),
    ):
        flags.append("identity_or_processing_uncertain")

    if _contains(
        normalized,
        (
            r"cut(?:s)? off",
            r"cut off .* early",
            r"too early",
            r"before .* is said",
            r"final word",
            r"last word",
        ),
    ):
        flags.append("boundary_incomplete")

    if _contains(
        normalized,
        (
            r"another voice",
            r"dog bark",
            r"background (?:sound|noise|music)",
            r"music .*background",
            r"sound effects?",
            r"tons of background",
            r"so much background",
            r"\becho\b",
        ),
    ):
        flags.append("scene_contamination")

    if _contains(normalized, (r"\bartifact", r"very muffled", r"not good enough")):
        flags.append("audio_damage")
    return flags


def objective_result(candidate: dict[str, Any], max_word_error_rate: float) -> dict[str, Any]:
    try:
        word_error_rate = float(candidate["word_error_rate"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ReviewAnalysisError("Candidate has no valid word_error_rate") from exc
    first_word_present = candidate.get("first_word_present") is True
    transcript_present = bool(str(candidate.get("automatic_transcript") or "").strip())
    transcript_pass = transcript_present and word_error_rate <= max_word_error_rate
    return {
        "automatic_transcript_result": "pass" if transcript_pass else "fail",
        "word_error_rate": word_error_rate,
        "first_word_present": first_word_present,
        "objective_eligible": transcript_pass and first_word_present,
    }


def candidate_classification(
    *,
    objective_eligible: bool,
    human_decision: str,
    scores: dict[str, int],
    note_flags: list[str],
) -> str:
    if not objective_eligible:
        return "objective-ineligible"
    if "identity_or_processing_uncertain" in note_flags:
        return OUTCOME_REPAIR
    repair_flags = {"boundary_incomplete", "scene_contamination", "audio_damage"}
    if repair_flags.intersection(note_flags):
        return OUTCOME_REPAIR if scores["identity"] >= 4 else OUTCOME_NONE
    if human_decision != "pass":
        return "human-rejected"
    if "intrinsic_scene_processing" in note_flags:
        return OUTCOME_INTRINSIC
    if min(scores.values()) >= 4:
        return OUTCOME_NEUTRAL
    if scores["identity"] >= 4 and scores["usefulness"] >= 3:
        return OUTCOME_PERFORMANCE
    return OUTCOME_NONE


def _selection_rank(row: dict[str, Any]) -> tuple[int, int, int, int, int]:
    classification_priority = {
        OUTCOME_NEUTRAL: 3,
        OUTCOME_PERFORMANCE: 2,
        OUTCOME_INTRINSIC: 1,
    }.get(row["final_classification"], 0)
    scores = row["human_scores"]
    # Prefer less destructive processing when the listening evidence is tied.
    treatment_preference = {
        "source_mix": 2,
        "mossformer2_source_mix": 1,
        "mel_roformer_vocal": 0,
    }[row["treatment"]]
    return (
        classification_priority,
        scores["isolation"] + scores["naturalness"] + scores["identity"] + scores["usefulness"],
        scores["isolation"],
        scores["usefulness"],
        treatment_preference,
    )


def _group_outcome(rows: list[dict[str, Any]]) -> tuple[str, str | None]:
    selectable = [
        row
        for row in rows
        if row["final_classification"]
        in {OUTCOME_NEUTRAL, OUTCOME_PERFORMANCE, OUTCOME_INTRINSIC}
    ]
    if selectable:
        winner = max(selectable, key=_selection_rank)
        return winner["final_classification"], winner["candidate_id"]

    all_notes = " ".join(str(row.get("notes") or "") for row in rows)
    if re.search(r"not good enough|very muffled", all_notes, flags=re.IGNORECASE):
        return OUTCOME_REPLACE, None

    repairable = [row for row in rows if row["final_classification"] == OUTCOME_REPAIR]
    if repairable:
        return OUTCOME_REPAIR, None

    if all(not row["objective_eligible"] or row["human_decision"] == "fail" for row in rows):
        return OUTCOME_REPLACE, None
    return OUTCOME_NONE, None


def analyze_round(
    answer_key: dict[str, Any],
    review_export: dict[str, Any],
    *,
    max_word_error_rate: float = 0.0,
) -> dict[str, Any]:
    if answer_key.get("round_id") != ROUND_ID:
        raise ReviewAnalysisError("Answer key round_id does not match")
    if review_export.get("round_id") != ROUND_ID:
        raise ReviewAnalysisError("Review export round_id does not match")

    candidates = answer_key.get("candidates")
    results = review_export.get("results")
    if not isinstance(candidates, dict) or not isinstance(results, dict):
        raise ReviewAnalysisError("Answer key candidates and review results must be objects")
    if len(candidates) != EXPECTED_CANDIDATE_COUNT:
        raise ReviewAnalysisError(
            f"Expected {EXPECTED_CANDIDATE_COUNT} answer-key candidates; found {len(candidates)}"
        )
    if set(candidates) != set(results):
        missing = sorted(set(candidates) - set(results))
        extra = sorted(set(results) - set(candidates))
        raise ReviewAnalysisError(f"Review candidate mismatch; missing={missing}, extra={extra}")

    characters = Counter(str(candidate.get("character") or "") for candidate in candidates.values())
    if set(characters) != set(EXPECTED_CHARACTERS):
        raise ReviewAnalysisError(
            f"Character groups do not match expected set: {sorted(characters)}"
        )
    if any(count != 3 for count in characters.values()):
        raise ReviewAnalysisError(f"Every character must have three candidates: {characters}")

    rows: list[dict[str, Any]] = []
    for candidate_id, candidate in candidates.items():
        review = results[candidate_id]
        if not isinstance(candidate, dict) or not isinstance(review, dict):
            raise ReviewAnalysisError(f"Malformed candidate or review: {candidate_id}")
        treatment = str(candidate.get("variant") or "")
        if treatment not in EXPECTED_TREATMENTS:
            raise ReviewAnalysisError(f"Unknown treatment for {candidate_id}: {treatment}")
        human_decision = str(review.get("decision") or "").casefold()
        if human_decision not in {"pass", "fail"}:
            raise ReviewAnalysisError(f"Missing decision for {candidate_id}")
        scores = {
            "isolation": _score(review, "isolation"),
            "naturalness": _score(review, "naturalness"),
            "identity": _score(review, "identity"),
            "usefulness": _score(review, "usefulness"),
        }
        notes = str(review.get("notes") or "").strip()
        note_flags = classify_notes(notes)
        objective = objective_result(candidate, max_word_error_rate)
        final_classification = candidate_classification(
            objective_eligible=objective["objective_eligible"],
            human_decision=human_decision,
            scores=scores,
            note_flags=note_flags,
        )
        rows.append(
            {
                "candidate_id": candidate_id,
                "character": str(candidate["character"]),
                "book_speaker": str(candidate.get("book_speaker") or ""),
                "treatment": treatment,
                **objective,
                "human_decision": human_decision,
                "human_scores": scores,
                "notes": notes,
                "note_flags": note_flags,
                "final_classification": final_classification,
                "selected": False,
            }
        )

    by_character: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_character[row["character"]].append(row)

    character_decisions: list[dict[str, Any]] = []
    for character in EXPECTED_CHARACTERS:
        group_rows = by_character[character]
        outcome, winner_id = _group_outcome(group_rows)
        if winner_id:
            next(row for row in group_rows if row["candidate_id"] == winner_id)["selected"] = True
        character_decisions.append(
            {
                "character": character,
                "outcome": outcome,
                "selected_candidate_id": winner_id,
                "selected_treatment": next(
                    (row["treatment"] for row in group_rows if row["candidate_id"] == winner_id),
                    None,
                ),
                "exact_line_substitution_status": "requires separate direct-line blind round",
            }
        )

    selected_rows = [row for row in rows if row["selected"]]
    if any(not row["objective_eligible"] for row in selected_rows):
        raise ReviewAnalysisError("Internal error: an objective failure was selected")
    if any(
        {"boundary_incomplete", "scene_contamination", "audio_damage"}.intersection(row["note_flags"])
        for row in selected_rows
    ):
        raise ReviewAnalysisError("Internal error: a note-blocked candidate was selected")

    return {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "review_exported_at": review_export.get("exported_at"),
        "candidate_count": len(rows),
        "character_count": len(character_decisions),
        "max_word_error_rate": max_word_error_rate,
        "production_changes": False,
        "project_voice_config_changed": False,
        "project_chunks_changed": False,
        "character_decisions": character_decisions,
        "candidates": sorted(rows, key=lambda row: (EXPECTED_CHARACTERS.index(row["character"]), row["candidate_id"])),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Original Sin overlap reference cleanliness decisions",
        "",
        f"Round: `{report['round_id']}`",
        "",
        f"Candidates: {report['candidate_count']} across {report['character_count']} characters.",
        "",
        "This analysis made no Alexandria Voice or chunk assignments. Exact-line substitution remains gated by a separate blind round.",
        "",
        "## Character decisions",
        "",
        "| Character | Outcome | Winner | Treatment |",
        "|---|---|---|---|",
    ]
    for decision in report["character_decisions"]:
        lines.append(
            "| {character} | {outcome} | {winner} | {treatment} |".format(
                character=decision["character"],
                outcome=decision["outcome"],
                winner=f"`{decision['selected_candidate_id']}`" if decision["selected_candidate_id"] else "—",
                treatment=f"`{decision['selected_treatment']}`" if decision["selected_treatment"] else "—",
            )
        )

    lines.extend(
        [
            "",
            "## Candidate evidence",
            "",
            "| Candidate | Character | Treatment | Transcript | First word | Scores I/N/ID/U | Human | Classification | Notes |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for row in report["candidates"]:
        scores = row["human_scores"]
        notes = row["notes"].replace("|", "\\|") or "—"
        selected = " **SELECTED**" if row["selected"] else ""
        lines.append(
            "| `{candidate}` | {character} | `{treatment}` | {transcript} (WER {wer:.3f}) | {first_word} | {isolation}/{naturalness}/{identity}/{usefulness} | {human} | {classification}{selected} | {notes} |".format(
                candidate=row["candidate_id"],
                character=row["character"],
                treatment=row["treatment"],
                transcript=row["automatic_transcript_result"],
                wer=row["word_error_rate"],
                first_word="pass" if row["first_word_present"] else "fail",
                isolation=scores["isolation"],
                naturalness=scores["naturalness"],
                identity=scores["identity"],
                usefulness=scores["usefulness"],
                human=row["human_decision"],
                classification=row["final_classification"],
                selected=selected,
                notes=notes,
            )
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--answer-key", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    parser.add_argument("--max-word-error-rate", type=float, default=0.0)
    args = parser.parse_args()

    report = analyze_round(
        read_json(args.answer_key),
        read_json(args.review),
        max_word_error_rate=args.max_word_error_rate,
    )
    write_json(args.output_json, report)
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.write_text(render_markdown(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
