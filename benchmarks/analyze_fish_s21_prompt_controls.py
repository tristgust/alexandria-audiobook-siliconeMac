#!/usr/bin/env python3
"""Decode completed Fish S2.1 prompt-control reviews after blind scoring."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Mapping

from run_fish_s21_prompt_controls import DEFAULT_OUTPUT_ROOT

RATING_FIELDS = (
    "identity_1_to_5",
    "delivery_1_to_5",
    "naturalness_1_to_5",
    "artifact_severity_1_to_5",
)
BOOLEAN_FIELDS = (
    "spoken_text_matches_expected",
    "requested_mode_is_clear",
    "approve_for_comparison",
)


class PromptControlAnalysisError(ValueError):
    """Raised when an exported review cannot be safely joined to its key."""


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _answer_keys(evidence_root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in evidence_root.glob("*/private/answer-key.json"):
        payload = _read_json(path)
        round_id = str(payload.get("round_id") or "")
        rows = payload.get("rows")
        if not round_id or not isinstance(rows, list):
            raise PromptControlAnalysisError(f"Malformed answer key: {path}.")
        if round_id in result:
            raise PromptControlAnalysisError(f"Duplicate answer-key round: {round_id}.")
        result[round_id] = {
            "path": str(path),
            "identity": path.parents[1].name,
            "rows": {str(row["sample_id"]): dict(row) for row in rows},
        }
    if not result:
        raise PromptControlAnalysisError(
            f"No prompt-control answer keys found under {evidence_root}."
        )
    return result


def _validate_score(row: Mapping[str, Any], path: Path) -> None:
    for field in RATING_FIELDS:
        value = row.get(field)
        if not isinstance(value, int) or not 1 <= value <= 5:
            raise PromptControlAnalysisError(
                f"Invalid {field!r} in {path}: {row.get('sample_id')!r}."
            )
    for field in BOOLEAN_FIELDS:
        if not isinstance(row.get(field), bool):
            raise PromptControlAnalysisError(
                f"Invalid {field!r} in {path}: {row.get('sample_id')!r}."
            )


def join_exports(
    evidence_root: str | Path,
    result_paths: Iterable[str | Path],
    *,
    require_complete: bool = True,
) -> list[dict[str, Any]]:
    root = Path(evidence_root).expanduser().resolve()
    keys = _answer_keys(root)
    joined: list[dict[str, Any]] = []
    seen_rounds: set[str] = set()
    for raw_path in result_paths:
        path = Path(raw_path).expanduser().resolve()
        payload = _read_json(path)
        round_id = str(payload.get("round_id") or "")
        if round_id not in keys:
            raise PromptControlAnalysisError(
                f"No matching prompt-control key for {round_id!r}: {path}."
            )
        if round_id in seen_rounds:
            raise PromptControlAnalysisError(
                f"Duplicate export for round {round_id!r}."
            )
        seen_rounds.add(round_id)
        rows = payload.get("rows")
        if not isinstance(rows, list):
            raise PromptControlAnalysisError(f"Export rows are missing: {path}.")
        answer = keys[round_id]
        if require_complete and len(rows) != len(answer["rows"]):
            raise PromptControlAnalysisError(
                f"Incomplete export {path}: {len(rows)} of {len(answer['rows'])}."
            )
        seen_ids: set[str] = set()
        for score in rows:
            if not isinstance(score, Mapping):
                raise PromptControlAnalysisError(f"Malformed score row: {path}.")
            sample_id = str(score.get("sample_id") or "")
            if sample_id in seen_ids:
                raise PromptControlAnalysisError(
                    f"Duplicate sample ID {sample_id!r}: {path}."
                )
            seen_ids.add(sample_id)
            source = answer["rows"].get(sample_id)
            if source is None:
                raise PromptControlAnalysisError(
                    f"Unknown sample ID {sample_id!r}: {path}."
                )
            _validate_score(score, path)
            joined.append(
                {
                    **source,
                    **dict(score),
                    "identity": answer["identity"],
                    "reviewer": payload.get("reviewer"),
                    "result_file": str(path),
                }
            )
    return joined


def _summary(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"sample_count": 0}
    return {
        "sample_count": len(rows),
        "identity_mean": mean(float(row["identity_1_to_5"]) for row in rows),
        "delivery_mean": mean(float(row["delivery_1_to_5"]) for row in rows),
        "naturalness_mean": mean(float(row["naturalness_1_to_5"]) for row in rows),
        "artifact_severity_mean": mean(
            float(row["artifact_severity_1_to_5"]) for row in rows
        ),
        "approve_rate": mean(
            1.0 if row["approve_for_comparison"] else 0.0 for row in rows
        ),
        "mode_clear_rate": mean(
            1.0 if row["requested_mode_is_clear"] else 0.0 for row in rows
        ),
        "text_match_rate": mean(
            1.0 if row["spoken_text_matches_expected"] else 0.0 for row in rows
        ),
    }


def _group(
    rows: list[dict[str, Any]], fields: tuple[str, ...]
) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = tuple(str(row.get(field) or "") for field in fields)
        buckets[key].append(row)
    result = []
    for key in sorted(buckets):
        result.append(
            {
                **dict(zip(fields, key)),
                **_summary(buckets[key]),
            }
        )
    return result


def build_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fish = [row for row in rows if row.get("kind") == "fish_cloud"]
    baselines = [row for row in rows if row.get("kind") == "existing_baseline"]
    prompt_rows = _group(fish, ("prompt_mode",))
    prompt_ranking = sorted(
        prompt_rows,
        key=lambda row: (
            row["delivery_mean"],
            row["mode_clear_rate"],
            row["approve_rate"],
            row["identity_mean"],
            row["naturalness_mean"],
            -row["artifact_severity_mean"],
        ),
        reverse=True,
    )
    return {
        "schema_version": 1,
        "sample_count": len(rows),
        "identity_count": len({row["identity"] for row in rows}),
        "overall": {
            "fish_cloud": _summary(fish),
            "local_baselines": _summary(baselines),
        },
        "by_identity": _group(rows, ("identity", "kind")),
        "by_prompt_mode": prompt_rows,
        "by_prompt_mode_and_identity": _group(
            fish, ("identity", "prompt_mode")
        ),
        "by_prompt_mode_and_delivery": _group(
            fish, ("style", "prompt_mode")
        ),
        "by_baseline_model": _group(
            baselines, ("model_key",)
        ),
        "prompt_ranking": [row["prompt_mode"] for row in prompt_ranking],
        "notes": [
            {
                "identity": row["identity"],
                "style": row.get("style"),
                "prompt_mode": row.get("prompt_mode"),
                "model_key": row.get("model_key"),
                "notes": row["notes"],
            }
            for row in rows
            if str(row.get("notes") or "").strip()
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", nargs="+")
    parser.add_argument("--evidence-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--output")
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()

    rows = join_exports(
        args.evidence_root,
        args.results,
        require_complete=not args.allow_partial,
    )
    report = build_report(rows)
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output:
        Path(args.output).expanduser().resolve().write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
