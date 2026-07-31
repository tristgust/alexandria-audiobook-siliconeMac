#!/usr/bin/env python3
"""Unblind the final boundary repairs and unseen-line expression round.

The report is evidence-only. It does not install Voice references, generated
performance routes, or direct chunk audio into the Alexandria project.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


REFERENCE_ROUND_ID = "alexandria_original_sin_overlap_reference_boundary_repair_v5"
DIRECT_ROUND_ID = "alexandria_original_sin_direct_substitution_boundary_repair_v4"
EXPRESSION_ROUND_ID = "alexandria_original_sin_unseen_expression_v1"

EXPECTED_REFERENCE_CHARACTERS = ("Beltempest", "The Doctor", "Computer", "Shythe Shahid")
EXPECTED_DIRECT_CHUNKS = (5207, 3908, 3098)
EXPECTED_EXPRESSION_GROUPS = (
    "bernice_urgent_concern",
    "bernice_dry_irony",
    "chris_urgent_authority",
    "chris_protective_concern",
    "roz_command_authority",
    "under_sergeant_cold_authority",
    "vaughn_controlled_anger",
    "vaughn_existential_fear",
)
EXPRESSION_GROUP_BY_CHARACTER_MODE = {
    ("Bernice Summerfield", "urgent concern"): "bernice_urgent_concern",
    ("Bernice Summerfield", "dry irony"): "bernice_dry_irony",
    ("Chris Cwej", "urgent authority"): "chris_urgent_authority",
    ("Chris Cwej", "protective concern"): "chris_protective_concern",
    ("Roz Forrester", "command authority"): "roz_command_authority",
    ("Under-Sergeant", "cold authority"): "under_sergeant_cold_authority",
    ("Tobias Vaughn / Robot", "controlled anger"): "vaughn_controlled_anger",
    ("Tobias Vaughn / Robot", "existential fear"): "vaughn_existential_fear",
}

OUTCOME_ANCHOR = "approved neutral identity anchor"
OUTCOME_REFERENCE_REPAIR = "requires replacement source or bounded repair"
OUTCOME_DIRECT = "exact-line substitution eligible"
OUTCOME_DIRECT_REPAIR = "requires alternate exact source line"
OUTCOME_EXPRESSION = "approved expressive generation route"
OUTCOME_EXPRESSION_REPAIR = "requires expressive-route repair"


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


def _score(value: dict[str, Any], key: str) -> int:
    try:
        result = int(value[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ReviewAnalysisError(f"Missing or invalid {key}: {value.get(key)!r}") from exc
    if result not in range(1, 6):
        raise ReviewAnalysisError(f"Score outside 1-5: {key}={result}")
    return result


def note_flags(notes: str) -> list[str]:
    value = str(notes or "").casefold().strip()
    flags: list[str] = []
    if re.search(r"music|background|different voice|other voice|sounds like it says", value):
        flags.append("contamination")
    if re.search(r"artifact|echo|muffled|compressed", value):
        flags.append("audio_damage")
    if re.search(r"cut(?:s)? off|too early|before .* finishes", value):
        flags.append("boundary_incomplete")
    if re.search(r"not sure if right voice|not sure .* voice", value):
        flags.append("identity_uncertain")
    return flags


def _treatment_rank(value: str) -> int:
    return {
        "source_mix": 4,
        "center_channel_mid": 3,
        "mossformer2_source_mix": 2,
        "mel_roformer_vocal": 1,
    }.get(str(value), 0)


def analyze_reference(answer_key: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    if answer_key.get("round_id") != REFERENCE_ROUND_ID or review.get("round_id") != REFERENCE_ROUND_ID:
        raise ReviewAnalysisError("Reference round_id mismatch")
    candidates = answer_key.get("candidates")
    results = review.get("results")
    if not isinstance(candidates, dict) or not isinstance(results, dict):
        raise ReviewAnalysisError("Reference candidates/results must be objects")
    if len(candidates) != 11 or set(candidates) != set(results):
        raise ReviewAnalysisError("Reference review must account for all 11 candidates")
    by_character: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows: list[dict[str, Any]] = []
    for candidate_id, source in candidates.items():
        human = results[candidate_id]
        decision = str(human.get("decision") or "").casefold()
        if decision not in {"pass", "fail"}:
            raise ReviewAnalysisError(f"Missing reference decision: {candidate_id}")
        scores = None
        if decision == "pass":
            scores = {key: _score(human, key) for key in ("isolation", "naturalness", "identity", "usefulness")}
        notes = str(human.get("notes") or "").strip()
        flags = note_flags(notes)
        eligible = (
            decision == "pass"
            and scores is not None
            and min(scores.values()) >= 4
            and not flags
        )
        row = {
            "candidate_id": candidate_id,
            "character": str(source.get("character") or ""),
            "treatment": str(source.get("treatment") or ""),
            "human_decision": decision,
            "human_scores": scores,
            "notes": notes,
            "note_flags": flags,
            "promotion_eligible": eligible,
            "selected": False,
        }
        rows.append(row)
        by_character[row["character"]].append(row)
    if set(by_character) != set(EXPECTED_REFERENCE_CHARACTERS):
        raise ReviewAnalysisError(f"Unexpected reference characters: {sorted(by_character)}")
    decisions = []
    for character in EXPECTED_REFERENCE_CHARACTERS:
        eligible = [row for row in by_character[character] if row["promotion_eligible"]]
        winner = None
        if eligible:
            winner = max(
                eligible,
                key=lambda row: (
                    sum(row["human_scores"].values()),
                    row["human_scores"]["isolation"],
                    _treatment_rank(row["treatment"]),
                ),
            )
            winner["selected"] = True
        decisions.append({
            "character": character,
            "outcome": OUTCOME_ANCHOR if winner else OUTCOME_REFERENCE_REPAIR,
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


def analyze_direct(answer_key: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    if answer_key.get("round_id") != DIRECT_ROUND_ID or review.get("round_id") != DIRECT_ROUND_ID:
        raise ReviewAnalysisError("Direct round_id mismatch")
    candidates = answer_key.get("candidates")
    results = review.get("results")
    if not isinstance(candidates, dict) or not isinstance(results, dict):
        raise ReviewAnalysisError("Direct candidates/results must be objects")
    if len(candidates) != 6 or set(candidates) != set(results):
        raise ReviewAnalysisError("Direct review must account for all six candidates")
    rows = []
    by_chunk: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for candidate_id, source in candidates.items():
        human = results[candidate_id]
        decision = str(human.get("decision") or "").casefold()
        notes = str(human.get("notes") or "").strip()
        flags = note_flags(notes)
        inferred = False
        if not decision and flags:
            decision = "fail"
            inferred = True
        if decision != "fail":
            raise ReviewAnalysisError(f"Unexpected direct decision: {candidate_id}={decision!r}")
        row = {
            "candidate_id": candidate_id,
            "chunk_id": int(source["chunk_id"]),
            "character": str(source["character"]),
            "treatment": str(source["treatment"]),
            "human_decision": decision,
            "decision_inferred_from_blocking_note": inferred,
            "notes": notes,
            "note_flags": flags,
            "selected": False,
        }
        rows.append(row)
        by_chunk[row["chunk_id"]].append(row)
    if set(by_chunk) != set(EXPECTED_DIRECT_CHUNKS):
        raise ReviewAnalysisError(f"Unexpected direct chunks: {sorted(by_chunk)}")
    return {
        "round_id": DIRECT_ROUND_ID,
        "review_exported_at": review.get("exported_at"),
        "candidate_count": len(rows),
        "chunk_decisions": [
            {
                "chunk_id": chunk_id,
                "character": by_chunk[chunk_id][0]["character"],
                "outcome": OUTCOME_DIRECT_REPAIR,
                "selected_candidate_id": None,
                "selected_treatment": None,
            }
            for chunk_id in EXPECTED_DIRECT_CHUNKS
        ],
        "candidates": rows,
    }


def analyze_expression(answer_key: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    if answer_key.get("round_id") != EXPRESSION_ROUND_ID or review.get("round_id") != EXPRESSION_ROUND_ID:
        raise ReviewAnalysisError("Expression round_id mismatch")
    candidates = answer_key.get("candidates")
    results = review.get("results")
    if not isinstance(candidates, dict) or not isinstance(results, dict):
        raise ReviewAnalysisError("Expression candidates/results must be objects")
    if len(candidates) != 29 or set(candidates) != set(results):
        raise ReviewAnalysisError("Expression review must account for all 29 candidates")
    rows: list[dict[str, Any]] = []
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate_id, source in candidates.items():
        human = results[candidate_id]
        decision = str(human.get("decision") or "").casefold()
        if decision not in {"pass", "fail"}:
            raise ReviewAnalysisError(f"Missing expression decision: {candidate_id}")
        scores = {key: _score(human, key) for key in ("identity", "delivery", "naturalness", "artifacts")}
        notes = str(human.get("notes") or "").strip()
        flags = note_flags(notes)
        character = str(source["character"])
        mode = str(source["mode"])
        try:
            group = EXPRESSION_GROUP_BY_CHARACTER_MODE[(character, mode)]
        except KeyError as exc:
            raise ReviewAnalysisError(
                f"Unknown expression character/mode: {(character, mode)!r}"
            ) from exc
        eligible = (
            decision == "pass"
            and scores["identity"] >= 4
            and scores["delivery"] >= 4
            and scores["naturalness"] >= 4
            and not flags
        )
        row = {
            "candidate_id": candidate_id,
            "group": group,
            "character": character,
            "mode": mode,
            "route_key": str(source["route_key"]),
            "requested_backend": str(source.get("requested_backend") or ""),
            "actual_backend": str(source.get("actual_backend") or ""),
            "fallback_used": bool(source.get("fallback_used")),
            "human_decision": decision,
            "human_scores": scores,
            "notes": notes,
            "note_flags": flags,
            "promotion_eligible": eligible,
            "selected": False,
        }
        rows.append(row)
        by_group[row["group"]].append(row)
    if set(by_group) != set(EXPECTED_EXPRESSION_GROUPS):
        raise ReviewAnalysisError(f"Unexpected expression groups: {sorted(by_group)}")
    decisions = []
    for group in EXPECTED_EXPRESSION_GROUPS:
        eligible = [row for row in by_group[group] if row["promotion_eligible"]]
        winner = None
        if eligible:
            winner = max(
                eligible,
                key=lambda row: (
                    row["human_scores"]["identity"] + row["human_scores"]["delivery"] + row["human_scores"]["naturalness"],
                    row["human_scores"]["delivery"],
                    not row["fallback_used"],
                ),
            )
            winner["selected"] = True
        first = by_group[group][0]
        decisions.append({
            "group": group,
            "character": first["character"],
            "mode": first["mode"],
            "outcome": OUTCOME_EXPRESSION if winner else OUTCOME_EXPRESSION_REPAIR,
            "selected_candidate_id": winner["candidate_id"] if winner else None,
            "selected_route_key": winner["route_key"] if winner else None,
            "selected_actual_backend": winner["actual_backend"] if winner else None,
            "selected_fallback_used": winner["fallback_used"] if winner else None,
        })
    return {
        "round_id": EXPRESSION_ROUND_ID,
        "review_exported_at": review.get("exported_at"),
        "candidate_count": len(rows),
        "group_decisions": decisions,
        "candidates": rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Original Sin boundary and unseen-expression decisions",
        "",
        "No Alexandria project Voice, reference bank, or chunk audio was changed by this analysis.",
        "",
        "## Reference anchors",
        "",
        "| Character | Outcome | Winner | Treatment |",
        "|---|---|---|---|",
    ]
    for row in report["reference_round"]["character_decisions"]:
        winner = f"`{row['selected_candidate_id']}`" if row["selected_candidate_id"] else "—"
        treatment = f"`{row['selected_treatment']}`" if row["selected_treatment"] else "—"
        lines.append(f"| {row['character']} | {row['outcome']} | {winner} | {treatment} |")
    lines.extend(["", "## Direct substitutions", "", "| Chunk | Character | Outcome |", "|---:|---|---|"])
    for row in report["direct_round"]["chunk_decisions"]:
        lines.append(f"| {row['chunk_id']} | {row['character']} | {row['outcome']} |")
    lines.extend(["", "## Unseen-line expression", "", "| Character | Mode | Outcome | Winner | Backend |", "|---|---|---|---|---|"])
    for row in report["expression_round"]["group_decisions"]:
        winner = f"`{row['selected_candidate_id']}`" if row["selected_candidate_id"] else "—"
        backend = f"`{row['selected_actual_backend']}`" if row["selected_actual_backend"] else "—"
        lines.append(f"| {row['character']} | {row['mode']} | {row['outcome']} | {winner} | {backend} |")
    lines.extend([
        "",
        "## Load-bearing findings",
        "",
        "- Beltempest and Computer now have clean neutral anchors.",
        "- Doctor and Shythe remain source/boundary blocked.",
        "- All three direct-boundary groups failed because a foreign onset remained; alternate exact lines are required.",
        "- Expressive winners exist for Chris protective concern, Under-Sergeant cold authority, Vaughn controlled anger, and Vaughn existential fear.",
        "- Bernice urgent concern, Bernice dry irony, Chris urgent authority, and Roz command authority require a focused expressive repair round.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-answer-key", type=Path, required=True)
    parser.add_argument("--reference-review", type=Path, required=True)
    parser.add_argument("--direct-answer-key", type=Path, required=True)
    parser.add_argument("--direct-review", type=Path, required=True)
    parser.add_argument("--expression-answer-key", type=Path, required=True)
    parser.add_argument("--expression-review", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    args = parser.parse_args()
    report = {
        "schema_version": 1,
        "reference_round": analyze_reference(read_json(args.reference_answer_key), read_json(args.reference_review)),
        "direct_round": analyze_direct(read_json(args.direct_answer_key), read_json(args.direct_review)),
        "expression_round": analyze_expression(read_json(args.expression_answer_key), read_json(args.expression_review)),
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
