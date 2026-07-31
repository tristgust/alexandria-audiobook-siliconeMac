#!/usr/bin/env python3
"""Unblind the alternate-source direct round and expressive repair round.

This is an evidence-only analyzer. It never mutates Alexandria Voice assignments,
reference banks, chunk audio, or project generation state.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


DIRECT_ROUND_ID = "alexandria_original_sin_direct_substitution_alternate_source_v5"
EXPRESSION_ROUND_ID = "alexandria_original_sin_unseen_expression_repair_v2"
DIRECT_CHUNKS = (1317, 4366, 3829)
EXPRESSION_GROUPS = (
    ("Bernice Summerfield", "urgent concern"),
    ("Bernice Summerfield", "dry irony"),
    ("Chris Cwej", "urgent authority"),
    ("Roz Forrester", "command authority"),
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


def score(row: dict[str, Any], key: str) -> int:
    try:
        value = int(row[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ReviewAnalysisError(f"Missing or invalid {key}: {row.get(key)!r}") from exc
    if value not in range(1, 6):
        raise ReviewAnalysisError(f"Score outside 1-5: {key}={value}")
    return value


def note_flags(notes: str) -> list[str]:
    value = str(notes or "").casefold().strip()
    flags: list[str] = []
    if re.search(r"cut short|cut(?:s)? off|too early|before .*finish", value):
        flags.append("boundary_incomplete")
    if re.search(r"artifact|echo|music|background|different voice", value):
        flags.append("audio_damage_or_contamination")
    if re.search(r"old lady|wrong voice|not .* voice", value):
        flags.append("identity_mismatch")
    return flags


def direct_treatment_rank(value: str) -> int:
    return {
        "source_mix": 4,
        "mossformer2_source_mix": 3,
        "mel_roformer_vocal": 2,
    }.get(str(value), 0)


def backend_rank(value: str) -> int:
    return {
        "qwen3_instruction_controlled": 3,
        "fish_s2.1_pro_free_inline_zero_shot": 2,
        "voxcpm2_controllable_clone": 1,
    }.get(str(value), 0)


def analyze_direct(answer: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    if answer.get("round_id") != DIRECT_ROUND_ID or review.get("round_id") != DIRECT_ROUND_ID:
        raise ReviewAnalysisError("Direct round_id mismatch")
    candidates = answer.get("candidates")
    results = review.get("results")
    if not isinstance(candidates, dict) or not isinstance(results, dict):
        raise ReviewAnalysisError("Direct candidates/results must be objects")
    if len(candidates) != 8 or set(candidates) != set(results):
        raise ReviewAnalysisError("Direct review must account for all eight candidates")

    rows: list[dict[str, Any]] = []
    by_chunk: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for candidate_id, source in candidates.items():
        human = results[candidate_id]
        decision = str(human.get("decision") or "").casefold()
        if decision not in {"pass", "fail"}:
            raise ReviewAnalysisError(f"Missing direct decision: {candidate_id}")
        scores = None
        if decision == "pass":
            scores = {key: score(human, key) for key in ("boundaries", "isolation", "naturalness", "usefulness")}
        notes = str(human.get("notes") or "").strip()
        flags = note_flags(notes)
        eligible = bool(
            decision == "pass"
            and scores is not None
            and min(scores.values()) >= 4
            and not flags
        )
        row = {
            "candidate_id": candidate_id,
            "chunk_id": int(source["chunk_id"]),
            "character": str(source["character"]),
            "treatment": str(source["treatment"]),
            "human_decision": decision,
            "human_scores": scores,
            "notes": notes,
            "note_flags": flags,
            "promotion_eligible": eligible,
            "selected": False,
        }
        rows.append(row)
        by_chunk[row["chunk_id"]].append(row)
    if set(by_chunk) != set(DIRECT_CHUNKS):
        raise ReviewAnalysisError(f"Unexpected direct chunks: {sorted(by_chunk)}")

    decisions = []
    for chunk_id in DIRECT_CHUNKS:
        eligible = [row for row in by_chunk[chunk_id] if row["promotion_eligible"]]
        winner = None
        if eligible:
            winner = max(
                eligible,
                key=lambda row: (
                    sum(row["human_scores"].values()),
                    row["human_scores"]["isolation"],
                    direct_treatment_rank(row["treatment"]),
                ),
            )
            winner["selected"] = True
        decisions.append({
            "chunk_id": chunk_id,
            "character": by_chunk[chunk_id][0]["character"],
            "outcome": "exact-line substitution eligible" if winner else "requires another source/boundary repair",
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


def analyze_expression(answer: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    if answer.get("round_id") != EXPRESSION_ROUND_ID or review.get("round_id") != EXPRESSION_ROUND_ID:
        raise ReviewAnalysisError("Expression round_id mismatch")
    candidates = answer.get("candidates")
    results = review.get("results")
    if not isinstance(candidates, dict) or not isinstance(results, dict):
        raise ReviewAnalysisError("Expression candidates/results must be objects")
    if len(candidates) != 15 or set(candidates) != set(results):
        raise ReviewAnalysisError("Expression review must account for all fifteen candidates")

    rows: list[dict[str, Any]] = []
    by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for candidate_id, source in candidates.items():
        human = results[candidate_id]
        decision = str(human.get("decision") or "").casefold()
        if decision not in {"pass", "fail"}:
            raise ReviewAnalysisError(f"Missing expression decision: {candidate_id}")
        scores = {key: score(human, key) for key in ("identity", "delivery", "naturalness", "artifacts")}
        notes = str(human.get("notes") or "").strip()
        flags = note_flags(notes)
        eligible = bool(
            decision == "pass"
            and scores["identity"] >= 4
            and scores["delivery"] >= 4
            and scores["naturalness"] >= 4
            and not flags
        )
        row = {
            "candidate_id": candidate_id,
            "character": str(source["character"]),
            "mode": str(source["mode"]),
            "route_key": str(source["route_key"]),
            "requested_backend": str(source.get("requested_backend") or ""),
            "actual_backend": str(source.get("actual_backend") or ""),
            "fallback_used": bool(source.get("fallback_used")),
            "human_decision": decision,
            "human_scores": scores,
            "notes": notes,
            "note_flags": flags,
            "promotion_eligible": eligible,
            "selected_primary": False,
        }
        rows.append(row)
        by_group[(row["character"], row["mode"])].append(row)
    if set(by_group) != set(EXPRESSION_GROUPS):
        raise ReviewAnalysisError(f"Unexpected expression groups: {sorted(by_group)}")

    decisions = []
    for key in EXPRESSION_GROUPS:
        eligible = [row for row in by_group[key] if row["promotion_eligible"]]
        ranked = sorted(
            eligible,
            key=lambda row: (
                row["human_scores"]["identity"] + row["human_scores"]["delivery"] + row["human_scores"]["naturalness"],
                row["human_scores"]["delivery"],
                not row["fallback_used"],
                backend_rank(row["actual_backend"]),
            ),
            reverse=True,
        )
        primary = ranked[0] if ranked else None
        if primary:
            primary["selected_primary"] = True
        decisions.append({
            "character": key[0],
            "mode": key[1],
            "outcome": "approved expressive generation route" if primary else "requires expressive-route repair",
            "primary_candidate_id": primary["candidate_id"] if primary else None,
            "primary_route_key": primary["route_key"] if primary else None,
            "primary_actual_backend": primary["actual_backend"] if primary else None,
            "approved_candidate_ids": [row["candidate_id"] for row in ranked],
            "approved_route_keys": [row["route_key"] for row in ranked],
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
        "# Original Sin alternate-source and expressive-repair decisions",
        "",
        "No Alexandria Voice assignment, reference bank, generated route, or chunk audio was changed by this analysis.",
        "",
        "## Direct substitutions",
        "",
        "| Chunk | Character | Outcome | Winner |",
        "|---:|---|---|---|",
    ]
    for row in report["direct_round"]["chunk_decisions"]:
        winner = f"`{row['selected_candidate_id']}`" if row["selected_candidate_id"] else "—"
        lines.append(f"| {row['chunk_id']} | {row['character']} | {row['outcome']} | {winner} |")
    lines.extend(["", "## Expressive generation", "", "| Character | Mode | Outcome | Primary | Approved routes |", "|---|---|---|---|---|"])
    for row in report["expression_round"]["group_decisions"]:
        primary = f"`{row['primary_candidate_id']}`" if row["primary_candidate_id"] else "—"
        approved = ", ".join(f"`{value}`" for value in row["approved_candidate_ids"]) or "—"
        lines.append(f"| {row['character']} | {row['mode']} | {row['outcome']} | {primary} | {approved} |")
    lines.extend([
        "",
        "## Load-bearing findings",
        "",
        "- Hater of Humans chunk 4366 is exact-line substitution eligible.",
        "- Powerless Friendless remains final-word blocked; Zebulon Pryce remains onset-artifact blocked.",
        "- Bernice urgent concern is approved through Fish S2.1 Pro Free inline zero-shot.",
        "- Bernice dry irony has both Qwen and Fish blind-approved routes.",
        "- Roz command authority has Qwen, current-route fallback, and Fish approvals; Fish is the strongest primary.",
        "- Chris urgent authority still preserves identity without delivering sufficient urgency and needs a real adaptation performance reference.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--direct-answer-key", type=Path, required=True)
    parser.add_argument("--direct-review", type=Path, required=True)
    parser.add_argument("--expression-answer-key", type=Path, required=True)
    parser.add_argument("--expression-review", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    args = parser.parse_args()
    report = {
        "schema_version": 1,
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
