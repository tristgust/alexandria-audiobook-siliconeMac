#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROUND_ID = "alexandria_original_sin_shythe_identity_completion_round_v7"
REVIEW = ROOT / "benchmarks/original_sin_shythe_identity_completion_round_v7_review.json"
SALVAGE_ANSWER = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665/external_workflows/big_finish_overlap_reference_v1/"
    "overlap_identity_salvage_round_v6/private/answer-key.json"
)
OUTPUT = ROOT / "benchmarks/original_sin_shythe_identity_completion_round_v7_decision.json"
CANDIDATE_ID = "5ad130953556d32b"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> int:
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    answer = json.loads(SALVAGE_ANSWER.read_text(encoding="utf-8"))
    if review.get("round_id") != ROUND_ID:
        raise RuntimeError("Review belongs to another round.")
    result = review["results"][CANDIDATE_ID]
    row = answer["candidates"][CANDIDATE_ID]
    scores = {
        "identity": int(result["identity"]),
        "cleanliness": int(result["cleanliness"]),
        "naturalness": int(result["naturalness"]),
        "intelligibility": int(result["intelligibility"]),
        "contamination": int(result["contamination"]),
    }
    if result.get("decision") != "pass" or result.get("completeness") != "complete":
        raise RuntimeError("Shythe completion review is not a pass.")
    if any(scores[field] < 4 for field in ("identity", "cleanliness", "naturalness", "intelligibility")):
        raise RuntimeError("Shythe positive-quality scores are insufficient.")
    # Operator clarification: contamination is an amount, so 1/5 means none
    # or effectively none and is the best outcome on this field.
    if scores["contamination"] > 2:
        raise RuntimeError("Shythe contamination is too high.")
    decision = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "closed_at_utc": utc_now(),
        "review_exported_at": review.get("exported_at"),
        "selected": {
            "candidate_id": CANDIDATE_ID,
            "character": row["character"],
            "book_speaker": row["book_speaker"],
            "treatment": row["treatment"],
            "scores": scores,
            "contamination_scale": "1_is_none_or_best_5_is_most_or_worst",
            "audio_path": row["audio_path"],
            "audio_sha256": row["audio"]["sha256"],
            "transcript": row["transcript"],
            "status": "identity_source_approved_pending_generated_mode_review",
        },
        "production_changes": False,
    }
    OUTPUT.write_text(json.dumps(decision, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "candidate_id": CANDIDATE_ID, "contamination_interpretation": decision["selected"]["contamination_scale"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
