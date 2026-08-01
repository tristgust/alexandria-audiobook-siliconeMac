#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
ROUND_ID = "alexandria_original_sin_dantalion_mode_completion_round_v1"
REVIEW = ROOT / "benchmarks/original_sin_dantalion_mode_completion_round_v1_review.json"
ANSWER = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665/external_workflows/big_finish_overlap_reference_v1/"
    "overlap_character_repairs_round_v4/private/answer-key.json"
)
OUTPUT = ROOT / "benchmarks/original_sin_dantalion_mode_completion_round_v1_decision.json"
CANDIDATE_ID = "0dff7471f2e22ead"
FIELDS = ("identity", "delivery", "naturalness", "intelligibility", "effects")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> int:
    review = read_json(REVIEW)
    answer = read_json(ANSWER)
    if review.get("round_id") != ROUND_ID:
        raise RuntimeError("Review belongs to another round.")
    result = review["results"][CANDIDATE_ID]
    scores = {field: int(result[field]) for field in FIELDS}
    if result.get("decision") != "pass" or result.get("completeness") != "complete":
        raise RuntimeError("Dantalion completion did not pass.")
    if min(scores.values()) != 5:
        raise RuntimeError("Dantalion completion is not all-five evidence.")
    row = answer["candidates"][CANDIDATE_ID]
    decision = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "closed_at_utc": utc_now(),
        "review_exported_at": review.get("exported_at"),
        "selected": {
            "mode_id": row["mode_id"],
            "candidate_id": CANDIDATE_ID,
            "backend": row["backend"],
            "approval_tier": "strict",
            "scores": scores,
            "audio_path": row["audio_path"],
            "audio_sha256": row["audio"]["sha256"],
            "identity_audio_sha256": row.get("identity_audio_sha256"),
            "text": row["text"],
            "instruct": row["instruct"],
        },
        "production_changes": False,
    }
    OUTPUT.write_text(json.dumps(decision, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "candidate_id": CANDIDATE_ID}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
