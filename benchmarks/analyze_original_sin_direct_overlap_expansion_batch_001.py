#!/usr/bin/env python3
"""Unblind Original Sin strict-overlap expansion batch 001.

Written notes are authoritative. A nominal human pass cannot promote audio
whose note reports a shortened word, background sound, music, or another
artifact. This analyzer is evidence-only and never mutates project state.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
from typing import Any


ROUND_ID = "alexandria_original_sin_direct_overlap_expansion_batch_001"
EXPECTED_CANDIDATES = 26
EXPECTED_CHUNKS = (
    2718,
    1586,
    12,
    5351,
    2070,
    696,
    1261,
    2741,
    2745,
    90,
    2090,
    4764,
    1318,
    3285,
)

OUTCOME_DIRECT = "exact-line substitution eligible"
OUTCOME_BOUNDARY = "requires segment-tail timing repair"
OUTCOME_CONTAMINATION = "requires contamination/source repair"


class Batch001ReviewError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise Batch001ReviewError(f"Expected JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def score(row: dict[str, Any], key: str) -> int:
    try:
        value = int(row[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise Batch001ReviewError(f"Missing or invalid {key}: {row.get(key)!r}") from exc
    if value not in range(1, 6):
        raise Batch001ReviewError(f"Score outside 1-5: {key}={value}")
    return value


def note_flags(notes: str) -> list[str]:
    value = str(notes or "").strip().casefold()
    flags: list[str] = []
    if re.search(
        r"cut(?:s)? off|cut her off|cuts her off|abrupt|before .*finish|"
        r"shorten|classify$|end of the word|word .* abruptly",
        value,
    ):
        flags.append("boundary_incomplete")
    if re.search(r"background|music|sound midway|slight sound|sound in the background", value):
        flags.append("music_or_effect")
    if re.search(r"artifact|echo|other voice|adjacent", value):
        flags.append("artifact_or_other_voice")
    return flags


def treatment_rank(treatment: str) -> int:
    # Prefer the less destructive enhancement when human scores tie.
    return {
        "mossformer2_source_mix": 2,
        "mel_roformer_vocal": 1,
    }.get(str(treatment), 0)


def analyze(answer_key: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    if answer_key.get("round_id") != ROUND_ID or review.get("round_id") != ROUND_ID:
        raise Batch001ReviewError("round_id mismatch")
    candidates = answer_key.get("candidates")
    results = review.get("results")
    if not isinstance(candidates, dict) or not isinstance(results, dict):
        raise Batch001ReviewError("Candidates/results must be objects")
    if len(candidates) != EXPECTED_CANDIDATES or set(candidates) != set(results):
        raise Batch001ReviewError("Review must account for all 26 candidates")

    rows: list[dict[str, Any]] = []
    by_chunk: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for candidate_id, source in candidates.items():
        human = results[candidate_id]
        decision = str(human.get("decision") or "").casefold()
        if decision not in {"pass", "fail"}:
            raise Batch001ReviewError(f"Missing decision: {candidate_id}")
        scores = {
            key: score(human, key)
            for key in (
                "boundaries",
                "isolation",
                "music_effects",
                "artifacts",
                "naturalness",
                "usefulness",
            )
        }
        notes = str(human.get("notes") or "").strip()
        flags = note_flags(notes)
        clean = (
            decision == "pass"
            and scores["boundaries"] == 5
            and scores["isolation"] == 5
            and scores["music_effects"] == 5
            and scores["artifacts"] == 5
            and scores["naturalness"] >= 4
            and scores["usefulness"] >= 4
            and not flags
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
            "promotion_eligible": clean,
            "selected": False,
        }
        rows.append(row)
        by_chunk[row["chunk_id"]].append(row)

    if set(by_chunk) != set(EXPECTED_CHUNKS):
        raise Batch001ReviewError(f"Unexpected chunks: {sorted(by_chunk)}")

    decisions: list[dict[str, Any]] = []
    for chunk_id in EXPECTED_CHUNKS:
        group = by_chunk[chunk_id]
        eligible = [row for row in group if row["promotion_eligible"]]
        winner = None
        if eligible:
            winner = max(
                eligible,
                key=lambda row: (
                    sum(row["human_scores"].values()),
                    treatment_rank(row["treatment"]),
                ),
            )
            winner["selected"] = True
            outcome = OUTCOME_DIRECT
        else:
            all_flags = {
                flag
                for row in group
                for flag in row["note_flags"]
            }
            low_boundary = any(row["human_scores"]["boundaries"] < 5 for row in group)
            if "boundary_incomplete" in all_flags or low_boundary:
                outcome = OUTCOME_BOUNDARY
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
        "schema_version": 1,
        "round_id": ROUND_ID,
        "review_exported_at": review.get("exported_at"),
        "candidate_count": len(rows),
        "chunk_count": len(decisions),
        "chunk_decisions": decisions,
        "candidates": rows,
        "production_changes": False,
        "project_voice_config_changed": False,
        "project_chunks_changed": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Original Sin strict direct-overlap expansion batch 001 decisions",
        "",
        "Written contamination and boundary notes override nominal pass selections.",
        "No Alexandria project state was changed by this analysis.",
        "",
        "| Chunk | Character | Outcome | Winner | Treatment |",
        "|---:|---|---|---|---|",
    ]
    for row in report["chunk_decisions"]:
        winner = f"`{row['selected_candidate_id']}`" if row["selected_candidate_id"] else "—"
        treatment = f"`{row['selected_treatment']}`" if row["selected_treatment"] else "—"
        lines.append(
            f"| {row['chunk_id']} | {row['character']} | {row['outcome']} | "
            f"{winner} | {treatment} |"
        )
    lines.extend(
        [
            "",
            "## Timing finding",
            "",
            "The fixed final-word margin was insufficient. Several lines passed ASR "
            "while losing the final consonant or ending unnaturally. Future cuts must "
            "retain the transcript segment tail up to the next-speaker boundary and "
            "append deterministic silence after processing.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--answer-key", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    args = parser.parse_args()
    report = analyze(read_json(args.answer_key), read_json(args.review))
    write_json(args.output_json, report)
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.write_text(render_markdown(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
