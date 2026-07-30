#!/usr/bin/env python3
"""Unblind the final Chris canonical-reference repair validation.

This analysis is evaluation-only. It joins the human pairwise export to the
private answer key, records model/style tallies, and emits the final
model-specific reference policy. It does not change any production Voice or
audiobook state.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPORT = Path(
    "/Users/tristan/Downloads/"
    "alexandria_chris_reference_repair_pairwise_v2-tristan.json"
)
DEFAULT_ANSWER = (
    ROOT
    / ".omo/evidence/chris-reference-repair-pairwise-v2/private/answer-key.json"
)
DEFAULT_OUTPUT = ROOT / "benchmarks/chris_reference_repair_final_v2.json"
ROUND_ID = "alexandria_chris_reference_repair_pairwise_v2"


def read_json(path: Path) -> Any:
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", type=Path, default=DEFAULT_EXPORT)
    parser.add_argument("--answer-key", type=Path, default=DEFAULT_ANSWER)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    export = read_json(args.export.expanduser().resolve())
    answer_payload = read_json(args.answer_key.expanduser().resolve())
    if export.get("round_id") != ROUND_ID:
        raise ValueError(f"Unexpected export round: {export.get('round_id')}")
    if answer_payload.get("round_id") != ROUND_ID:
        raise ValueError(
            f"Unexpected answer-key round: {answer_payload.get('round_id')}"
        )

    results = export.get("results") or {}
    answers = answer_payload.get("pairs") or {}
    if set(results) != set(answers) or len(results) != 12:
        raise ValueError(
            f"Expected 12 matching pairs; export={len(results)}, answer={len(answers)}"
        )

    total = Counter()
    by_model: dict[str, Counter[str]] = defaultdict(Counter)
    by_style: dict[str, Counter[str]] = defaultdict(Counter)
    rows: list[dict[str, Any]] = []

    for pair_id, human in results.items():
        answer = answers[pair_id]
        choice = str(human.get("choice") or "")
        if choice not in {"a", "b", "tie"}:
            raise ValueError(f"Missing or invalid choice for {pair_id}: {choice!r}")
        winner = "tie" if choice == "tie" else str(answer[f"{choice}_reference"])
        total[winner] += 1
        by_model[str(answer["model_key"])][winner] += 1
        by_style[str(answer["style"])][winner] += 1
        rows.append(
            {
                "pair_id": pair_id,
                "model_key": answer["model_key"],
                "style": answer["style"],
                "a_reference": answer["a_reference"],
                "b_reference": answer["b_reference"],
                "choice": choice,
                "winner": winner,
                "notes": str(human.get("notes") or ""),
            }
        )

    repaired = "canonical_repaired_mossformer2"
    old = "canonical_cleaned_old"
    model_policy = {
        "fish_s2_pro_cloud": {
            "canonical_identity_reference": "mossformer2_demucs",
            "status": "accepted_optional_canonical_lane",
            "tally": dict(by_model["fish_s2_pro_cloud"]),
            "reason": (
                "Repaired reference won three Fish comparisons; the remaining "
                "comparison was a tie."
            ),
        },
        "indextts2_matched_control": {
            "canonical_identity_reference": None,
            "status": "canonical_identity_lane_disabled",
            "tally": dict(by_model["indextts2_matched_control"]),
            "reason": (
                "The old source won three IndexTTS2 comparisons, but it retains "
                "the known echo defect. Use the clean actor identity anchor and "
                "same-character audio only as delivery references."
            ),
        },
        "voxcpm2_controllable_clone": {
            "canonical_identity_reference": "mossformer2_demucs",
            "status": "restricted_research_fallback",
            "tally": dict(by_model["voxcpm2_controllable_clone"]),
            "reason": (
                "The repair won one comparison and tied three. Human notes say "
                "it removes background sound but can introduce identity drift."
            ),
        },
    }

    if by_model["fish_s2_pro_cloud"][repaired] != 3:
        raise ValueError("Fish repair tally changed unexpectedly.")
    if by_model["indextts2_matched_control"][old] != 3:
        raise ValueError("IndexTTS2 old-reference tally changed unexpectedly.")
    if by_model["voxcpm2_controllable_clone"][repaired] != 1:
        raise ValueError("VoxCPM2 repair tally changed unexpectedly.")

    report = {
        "schema_version": 1,
        "analysis_id": "alexandria_chris_reference_repair_final_v2",
        "round_id": ROUND_ID,
        "exported_at": export.get("exported_at"),
        "analyzed_at": utc_now(),
        "pair_count": len(rows),
        "overall_tally": dict(total),
        "by_model": {key: dict(value) for key, value in sorted(by_model.items())},
        "by_style": {key: dict(value) for key, value in sorted(by_style.items())},
        "selected_repair": "mossformer2_demucs",
        "universal_replacement_allowed": False,
        "default_identity_reference": "clean_actor",
        "model_specific_policy": model_policy,
        "rows": rows,
        "tnia_miller_included": False,
        "additional_human_review_required": False,
        "production_assignment_changed": False,
    }
    write_json(args.output.expanduser().resolve(), report)
    print(
        json.dumps(
            {
                "output": str(args.output.expanduser().resolve()),
                "overall_tally": report["overall_tally"],
                "model_specific_policy": model_policy,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
