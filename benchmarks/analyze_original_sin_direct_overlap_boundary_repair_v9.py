#!/usr/bin/env python3
"""Unblind and close the terminal Original Sin direct-overlap repair round."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from typing import Any


ROUND_ID = "alexandria_original_sin_direct_overlap_boundary_repair_v9"
SCORE_KEYS = (
    "boundaries",
    "isolation",
    "music_effects",
    "artifacts",
    "naturalness",
    "usefulness",
)

STRICT_SELECTED = {
    365: "2a0cf9fbac010a7c",
    4071: "877fe999c6f59109",
    4443: "5ad7fa17c8dab54d",
}
RESTRICTED_SELECTED = {
    1801: "e21ba01427d6bd58",
    3025: "4b771af2458709cf",
    4907: "034836cc57f0ac5c",
}
TERMINAL_OUTCOMES = {
    5055: "terminal source blocked after final-word tail cleanup",
    3116: "terminal source blocked by adjacent speaker after final cleanup",
    3016: "terminal source blocked after final-word recovery",
    4715: "terminal source blocked by music after final-word recovery",
    4888: "terminal source blocked by clipped final word and separator artifact",
}
REFERENCE_BANK_DISPOSITIONS = {
    4443: {
        "status": "approved",
        "character": "Doctor",
        "delivery_tags": ["general_expressive_delivery"],
        "reason": "User explicitly marked the clean repaired line as useful for the Doctor character reference bank.",
    },
    1801: {
        "status": "excluded",
        "character": "Doctor",
        "delivery_tags": [],
        "reason": "User explicitly approved direct placement only and rejected use for generating unadapted lines or the reference bank.",
    },
    3025: {
        "status": "excluded",
        "character": "Bernice",
        "delivery_tags": [],
        "reason": "User explicitly approved direct placement only and rejected use for generating unadapted lines or the reference bank.",
    },
    4888: {
        "status": "rejected_terminal",
        "character": "Doctor",
        "delivery_tags": ["general_delivery", "rolled_r"],
        "reason": "The user wanted the line for direct and reference use only if the clipped final word and artifact pop were repaired; the terminal repair did not fix them.",
    },
    4907: {
        "status": "excluded",
        "character": "Doctor",
        "delivery_tags": [],
        "reason": "User explicitly approved direct placement only and rejected use for generating lines or the character reference bank.",
    },
}


class TerminalReviewError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TerminalReviewError(f"Expected JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


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
            raise TerminalReviewError(f"Invalid {key}: {row.get(key)!r}") from exc
        if value not in range(1, 6):
            raise TerminalReviewError(f"Score outside 1-5: {key}={value}")
        scores[key] = value
    return scores, missing


def candidate_rows(answer_key: dict[str, Any], review: dict[str, Any]) -> list[dict[str, Any]]:
    if answer_key.get("round_id") != ROUND_ID or review.get("round_id") != ROUND_ID:
        raise TerminalReviewError("round_id mismatch")
    candidates = answer_key.get("candidates")
    results = review.get("results")
    if not isinstance(candidates, dict) or not isinstance(results, dict):
        raise TerminalReviewError("Candidates/results must be objects")
    if set(candidates) != set(results):
        raise TerminalReviewError("Review must account for every terminal candidate")

    rows: list[dict[str, Any]] = []
    selected_ids = set(STRICT_SELECTED.values()) | set(RESTRICTED_SELECTED.values())
    for candidate_id, source in candidates.items():
        human = results[candidate_id]
        decision = str(human.get("decision") or "").casefold()
        if decision not in {"pass", "fail"}:
            raise TerminalReviewError(f"Missing decision: {candidate_id}")
        scores, missing_scores = score_bundle(human)
        notes = str(human.get("notes") or "").strip()
        strict_clean = (
            decision == "pass"
            and not missing_scores
            and all(scores[key] == 5 for key in SCORE_KEYS)
            and candidate_id in STRICT_SELECTED.values()
        )
        restricted_direct = candidate_id in RESTRICTED_SELECTED.values()
        rows.append(
            {
                "candidate_id": candidate_id,
                "chunk_id": int(source["chunk_id"]),
                "character": str(source["character"]),
                "book_speaker": str(source["book_speaker"]),
                "transcript": str(source["transcript"]),
                "treatment": str(source["treatment"]),
                "wav_path": str(source["wav_path"]),
                "proxy_path": str(source["proxy_path"]),
                "proxy_sha256": str(source["proxy_sha256"]),
                "human_decision": decision,
                "human_scores": scores,
                "missing_scores": missing_scores,
                "notes": notes,
                "strict_clean_eligible": strict_clean,
                "restricted_direct_eligible": restricted_direct,
                "selected": candidate_id in selected_ids,
            }
        )
    return rows


def analyze(answer_key: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    rows = candidate_rows(answer_key, review)
    by_chunk: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_chunk[row["chunk_id"]].append(row)
    expected = {5055, 3116, 365, 1801, 3016, 3025, 4071, 4443, 4715, 4888, 4907}
    if set(by_chunk) != expected:
        raise TerminalReviewError(f"Unexpected terminal chunks: {sorted(by_chunk)}")

    decisions: list[dict[str, Any]] = []
    for chunk_id in sorted(expected):
        group = by_chunk[chunk_id]
        selected = next((row for row in group if row["selected"]), None)
        if chunk_id in STRICT_SELECTED:
            if selected is None or not selected["strict_clean_eligible"]:
                raise TerminalReviewError(f"Strict selection is not clean: {chunk_id}")
            outcome = "strict-clean exact-line substitution eligible"
            direct_tier = "strict_clean"
        elif chunk_id in RESTRICTED_SELECTED:
            if selected is None or selected["human_decision"] != "pass":
                raise TerminalReviewError(f"Restricted direct selection is not user-approved: {chunk_id}")
            if "direct placement" not in selected["notes"].casefold() and "chunk's audio" not in selected["notes"].casefold():
                raise TerminalReviewError(f"Restricted direct note is missing: {chunk_id}")
            outcome = "restricted direct-placement-only eligible"
            direct_tier = "restricted_user_accepted_artifacts"
        else:
            selected = None
            outcome = TERMINAL_OUTCOMES[chunk_id]
            direct_tier = "rejected_terminal"
        decisions.append(
            {
                "chunk_id": chunk_id,
                "character": group[0]["character"],
                "book_speaker": group[0]["book_speaker"],
                "transcript": group[0]["transcript"],
                "outcome": outcome,
                "direct_placement_tier": direct_tier,
                "selected_candidate_id": selected["candidate_id"] if selected else None,
                "selected_treatment": selected["treatment"] if selected else None,
                "reference_bank_disposition": REFERENCE_BANK_DISPOSITIONS.get(chunk_id),
            }
        )

    return {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "candidate_count": len(rows),
        "chunk_count": len(decisions),
        "strict_clean_approved_count": len(STRICT_SELECTED),
        "restricted_direct_approved_count": len(RESTRICTED_SELECTED),
        "terminal_rejected_count": len(TERMINAL_OUTCOMES),
        "chunk_decisions": decisions,
        "candidates": rows,
        "production_changes": False,
        "project_voice_config_changed": False,
        "project_chunks_changed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--answer-key", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    write_json(args.output, analyze(read_json(args.answer_key), read_json(args.review)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
