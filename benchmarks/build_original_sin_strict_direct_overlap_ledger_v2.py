#!/usr/bin/env python3
"""Build the durable strict direct-dialogue overlap ledger for Original Sin.

The ledger contains book dialogue of at least four normalized words whose full
text is exactly equal to one, two, or three contiguous adaptation transcript
segments.  It does not claim that matching text alone proves a production-safe
substitution.  Every row retains boundary, ambiguity, context, and review state
needed by the later extraction and blind-listening stages.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
from typing import Any


ROUND_ID = "alexandria_original_sin_strict_direct_overlap_ledger_v2"
DEFAULT_PROJECT = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665"
)
DEFAULT_OUTPUT = Path(__file__).with_name(
    "original_sin_strict_direct_overlap_ledger_v2.json"
)

# Repeated exact text must be bound by scene context rather than by first hit.
MANUAL_WINDOW_SELECTIONS: dict[int, tuple[int, int]] = {
    696: (479, 479),
    1210: (688, 688),
    2231: (1188, 1188),
    3025: (1453, 1453),
    3036: (1477, 1478),
    4366: (2017, 2017),
    4580: (990, 990),
}

# The words occur in the adaptation, but neither occurrence is Bernice's book
# utterance in the matching scene.  Text equality therefore cannot bind it.
MANUAL_EXCLUSIONS: dict[int, str] = {
    1297: (
        "Exact words occur twice, but the adaptation contexts attribute the "
        "utterance elsewhere; no speaker-correct Bernice occurrence is proven."
    )
}


class StrictOverlapLedgerError(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def normalized_words(value: Any) -> list[str]:
    normalized = str(value or "").casefold().replace("’", "'")
    return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", normalized)


def normalized_text(value: Any) -> str:
    return " ".join(normalized_words(value))


def transcript_windows(segments: list[dict[str, Any]]) -> dict[str, list[tuple[int, int]]]:
    windows: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for start in range(len(segments)):
        text_parts: list[str] = []
        for width in range(1, 4):
            end = start + width - 1
            if end >= len(segments):
                break
            text_parts.append(str(segments[end].get("text") or ""))
            windows[normalized_text(" ".join(text_parts))].append((start, end))
    return windows


def selected_window(
    chunk_id: int,
    occurrences: list[tuple[int, int]],
) -> tuple[tuple[int, int] | None, str, str | None]:
    if chunk_id in MANUAL_EXCLUSIONS:
        return None, "excluded_wrong_speaker_context", MANUAL_EXCLUSIONS[chunk_id]
    if chunk_id in MANUAL_WINDOW_SELECTIONS:
        selected = MANUAL_WINDOW_SELECTIONS[chunk_id]
        if selected not in occurrences:
            raise StrictOverlapLedgerError(
                f"Manual occurrence {selected} is unavailable for chunk {chunk_id}."
            )
        return selected, "manual_scene_context", None
    if len(occurrences) == 1:
        return occurrences[0], "unique_exact_text", None
    return None, "ambiguous_repeated_text", "Repeated exact text lacks a resolved scene binding."


def context_support(
    *,
    chunk_position: int,
    selected: tuple[int, int],
    exact_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    start, end = selected
    support: list[dict[str, Any]] = []
    for other in exact_rows:
        if other["chunk_position"] == chunk_position:
            continue
        book_delta = int(other["chunk_position"]) - chunk_position
        if abs(book_delta) > 40:
            continue
        occurrences = other["occurrences"]
        if len(occurrences) != 1:
            continue
        other_start, other_end = occurrences[0]
        same_direction = (
            book_delta < 0 and other_end < start
        ) or (
            book_delta > 0 and other_start > end
        )
        if not same_direction:
            continue
        transcript_delta = (
            start - other_end if book_delta < 0 else other_start - end
        )
        if transcript_delta > 80:
            continue
        weight = 1.0 / (1.0 + abs(book_delta) / 8.0)
        weight *= 1.0 / (1.0 + transcript_delta / 20.0)
        if weight < 0.04:
            continue
        support.append(
            {
                "chunk_id": other["chunk_id"],
                "speaker": other["speaker"],
                "transcript_window": [other_start, other_end],
                "book_delta": book_delta,
                "transcript_delta": transcript_delta,
                "weight": round(weight, 6),
            }
        )
    support.sort(key=lambda row: row["weight"], reverse=True)
    return support[:6]


def build_ledger(project: Path) -> dict[str, Any]:
    chunks = read_json(project / "chunks.json")
    segments = read_json(
        project
        / "external_workflows/big_finish_overlap_reference_v1/private/transcript.json"
    )["segments"]
    promotion = read_json(
        Path(__file__).with_name(
            "original_sin_overlap_production_promotion_manifest_v1.json"
        )
    )
    approved_chunks = {
        int(row["chunk_id"]): str(row["candidate_id"])
        for row in promotion["direct_substitutions"]
    }
    workflow = project / "external_workflows/big_finish_overlap_reference_v1"
    previously_reviewed_chunks: set[int] = set()
    for answer_key in workflow.rglob("answer-key.json"):
        relative = answer_key.relative_to(workflow).as_posix()
        if not (
            relative.startswith("direct_substitution_")
            or relative.startswith("powerless_final_source_")
        ):
            continue
        payload = read_json(answer_key)
        candidates = payload.get("candidates")
        if not isinstance(candidates, dict):
            continue
        for candidate in candidates.values():
            if isinstance(candidate, dict) and candidate.get("chunk_id") is not None:
                previously_reviewed_chunks.add(int(candidate["chunk_id"]))
    windows = transcript_windows(segments)

    exact_rows: list[dict[str, Any]] = []
    for position, chunk in enumerate(chunks):
        speaker = str(chunk.get("speaker") or "")
        words = normalized_words(chunk.get("text"))
        if speaker == "NARRATOR" or len(words) < 4:
            continue
        occurrences = windows.get(normalized_text(chunk.get("text")), [])
        if not occurrences:
            continue
        exact_rows.append(
            {
                "chunk_position": position,
                "chunk_id": int(chunk["id"]),
                "speaker": speaker,
                "text": str(chunk.get("text") or ""),
                "normalized_text": normalized_text(chunk.get("text")),
                "word_count": len(words),
                "occurrences": occurrences,
            }
        )

    rows: list[dict[str, Any]] = []
    for raw in exact_rows:
        chunk_id = int(raw["chunk_id"])
        occurrence, basis, exclusion = selected_window(chunk_id, raw["occurrences"])
        selected_payload = None
        support: list[dict[str, Any]] = []
        if occurrence is not None:
            start_index, end_index = occurrence
            previous_end = (
                float(segments[start_index - 1]["end"])
                if start_index > 0
                else float(segments[start_index]["start"])
            )
            next_start = (
                float(segments[end_index + 1]["start"])
                if end_index + 1 < len(segments)
                else float(segments[end_index]["end"])
            )
            start_seconds = float(segments[start_index]["start"])
            end_seconds = float(segments[end_index]["end"])
            support = context_support(
                chunk_position=int(raw["chunk_position"]),
                selected=occurrence,
                exact_rows=exact_rows,
            )
            selected_payload = {
                "segment_start": start_index,
                "segment_end": end_index,
                "segment_count": end_index - start_index + 1,
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
                "duration_seconds": round(end_seconds - start_seconds, 6),
                "leading_gap_seconds": round(max(0.0, start_seconds - previous_end), 6),
                "trailing_gap_seconds": round(max(0.0, next_start - end_seconds), 6),
            }
        rows.append(
            {
                "chunk_id": chunk_id,
                "speaker": raw["speaker"],
                "text": raw["text"],
                "normalized_text": raw["normalized_text"],
                "word_count": raw["word_count"],
                "occurrence_count": len(raw["occurrences"]),
                "all_occurrences": [list(value) for value in raw["occurrences"]],
                "binding_basis": basis,
                "binding_exclusion": exclusion,
                "selected_window": selected_payload,
                "context_support": support,
                "context_support_score": round(
                    sum(item["weight"] for item in support), 6
                ),
                "already_blind_approved": chunk_id in approved_chunks,
                "approved_candidate_id": approved_chunks.get(chunk_id),
                "previously_direct_reviewed": chunk_id in previously_reviewed_chunks,
                "production_changes": False,
            }
        )

    unique_text_count = len({row["normalized_text"] for row in rows})
    if len(rows) != 144 or unique_text_count != 142:
        raise StrictOverlapLedgerError(
            f"Strict ledger drifted: rows={len(rows)}, unique_texts={unique_text_count}."
        )
    counts = Counter(row["speaker"] for row in rows)
    return {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "matching_contract": {
            "minimum_normalized_words": 4,
            "maximum_contiguous_transcript_segments": 3,
            "normalization": "casefold, curly-apostrophe normalization, alphanumeric words",
            "speaker_binding": (
                "Unique text occurrence or explicit scene-context resolution; "
                "text equality alone does not authorize production."
            ),
        },
        "book_chunk_match_count": len(rows),
        "unique_quotation_count": unique_text_count,
        "resolved_binding_count": sum(row["selected_window"] is not None for row in rows),
        "excluded_binding_count": sum(row["binding_exclusion"] is not None for row in rows),
        "promotion_manifest_direct_count": len(approved_chunks),
        "already_blind_approved_count": sum(row["already_blind_approved"] for row in rows),
        "previously_direct_reviewed_count": sum(
            row["previously_direct_reviewed"] for row in rows
        ),
        "speaker_counts": dict(sorted(counts.items())),
        "rows": rows,
        "production_changes": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    ledger = build_ledger(args.project_root.expanduser().resolve())
    write_json(args.output.expanduser().resolve(), ledger)
    print(
        json.dumps(
            {
                "book_chunk_match_count": ledger["book_chunk_match_count"],
                "unique_quotation_count": ledger["unique_quotation_count"],
                "resolved_binding_count": ledger["resolved_binding_count"],
                "excluded_binding_count": ledger["excluded_binding_count"],
                "promotion_manifest_direct_count": ledger["promotion_manifest_direct_count"],
                "already_blind_approved_count": ledger["already_blind_approved_count"],
                "output": str(args.output.expanduser().resolve()),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
