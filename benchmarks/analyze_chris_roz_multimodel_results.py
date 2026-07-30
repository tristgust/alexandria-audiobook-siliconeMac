#!/usr/bin/env python3
"""Unblind and summarize the completed Chris/Roz multimodel review."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = ROOT / ".omo/evidence/chris-roz-multimodel-round1-v1"

MODEL_LABELS = {
    "fish_s2_pro_cloud": "Fish S2.1 Pro Free",
    "voxcpm2_controllable_clone": "VoxCPM2",
    "indextts2_matched_control": "IndexTTS2",
}

ROUTING = {
    "chris": {
        "neutral": {"status": "winner", "model": "indextts2_matched_control"},
        "dry_humour": {"status": "pairwise", "models": ["fish_s2_pro_cloud", "indextts2_matched_control"]},
        "urgent_authority": {"status": "control_retest", "models": ["fish_s2_pro_cloud", "voxcpm2_controllable_clone", "indextts2_matched_control"]},
        "vulnerability": {"status": "winner", "model": "fish_s2_pro_cloud"},
    },
    "roz": {
        "neutral": {"status": "pairwise", "models": ["fish_s2_pro_cloud", "indextts2_matched_control"]},
        "dry_humour": {"status": "winner", "model": "voxcpm2_controllable_clone"},
        "urgent_authority": {"status": "winner", "model": "indextts2_matched_control"},
        "vulnerability": {"status": "winner", "model": "fish_s2_pro_cloud"},
    },
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "sample_count": len(rows),
        "approval_count": sum(bool(row["approve_for_comparison"]) for row in rows),
        "approval_rate": mean(bool(row["approve_for_comparison"]) for row in rows),
        "mode_clear_rate": mean(bool(row["requested_mode_is_clear"]) for row in rows),
        "text_match_rate": mean(bool(row["spoken_text_matches_expected"]) for row in rows),
        "identity_mean": mean(int(row["identity_1_to_5"]) for row in rows),
        "delivery_mean": mean(int(row["delivery_1_to_5"]) for row in rows),
        "naturalness_mean": mean(int(row["naturalness_1_to_5"]) for row in rows),
        "artifact_severity_mean": mean(int(row["artifact_severity_1_to_5"]) for row in rows),
    }


def grouped(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[tuple(str(row[field]) for field in fields)].append(row)
    return [
        {**dict(zip(fields, key)), **summary(bucket)}
        for key, bucket in sorted(buckets.items())
    ]


def build_report(review: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any]:
    if review.get("round_id") != answer.get("round_id"):
        raise ValueError("Review and answer-key round IDs differ.")
    key_rows = answer.get("samples")
    if not isinstance(key_rows, dict) or len(key_rows) != 96:
        raise ValueError("Expected 96 answer-key samples.")
    scores = review.get("rows")
    if not isinstance(scores, list) or len(scores) != 96:
        raise ValueError("Expected a complete 96-row review export.")
    seen: set[str] = set()
    joined: list[dict[str, Any]] = []
    for score in scores:
        sample_id = str(score.get("sample_id") or "")
        if sample_id in seen or sample_id not in key_rows:
            raise ValueError(f"Unknown or duplicate sample ID: {sample_id}")
        seen.add(sample_id)
        joined.append({**key_rows[sample_id], **score})

    tier_rows = grouped(joined, ("reference_tier",))
    tier_counts = {row["reference_tier"]: row["approval_count"] for row in tier_rows}
    if tier_counts != {"canonical_cleaned": 7, "clean_actor": 33}:
        raise ValueError(f"Unexpected reference-tier approval totals: {tier_counts}")

    artifact_inconsistencies = [
        {
            "sample_id": row["sample_id"],
            "model_key": row["model_key"],
            "identity_key": row["identity_key"],
            "style": row["style"],
            "artifact_severity": row["artifact_severity_1_to_5"],
            "approved": row["approve_for_comparison"],
            "notes": row.get("notes") or "",
        }
        for row in joined
        if (
            int(row["artifact_severity_1_to_5"]) >= 4
            and bool(row["approve_for_comparison"])
        )
        or (
            int(row["artifact_severity_1_to_5"]) == 1
            and re.search(r"echo|artifact|compressed|degraded|background", str(row.get("notes") or ""), re.I)
        )
    ]

    decisive = []
    pairwise = []
    retests = []
    for identity, styles in ROUTING.items():
        for style, decision in styles.items():
            record = {"identity_key": identity, "style": style, **decision}
            if decision["status"] == "winner":
                decisive.append(record)
            elif decision["status"] == "pairwise":
                pairwise.append(record)
            else:
                retests.append(record)

    return {
        "schema_version": 1,
        "analysis_id": "alexandria_chris_roz_multimodel_results_v1",
        "round_id": review["round_id"],
        "reviewer": review.get("reviewer"),
        "review_exported_at": review.get("exported_at"),
        "analyzed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sample_count": len(joined),
        "follow_up_flag_count": review.get("summary", {}).get("follow_up_flag_count"),
        "reference_tier_decision": {
            "winner": "clean_actor",
            "clean_actor_approvals": 33,
            "canonical_cleaned_approvals": 7,
            "canonical_identity_use_allowed": False,
            "canonical_performance_reference_use_allowed": True,
        },
        "by_model": grouped(joined, ("model_key",)),
        "by_model_and_identity": grouped(joined, ("model_key", "identity_key")),
        "by_reference_tier": tier_rows,
        "by_clean_actor_cell": grouped(
            [row for row in joined if row["reference_tier"] == "clean_actor"],
            ("identity_key", "style", "model_key"),
        ),
        "routing": ROUTING,
        "decisive_winners": decisive,
        "pairwise_required": pairwise,
        "control_retests_required": retests,
        "artifact_score_inconsistencies": artifact_inconsistencies,
        "joined_rows": joined,
        "tnia_miller_included": False,
        "production_promotion_allowed": False,
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Chris Cwej / Roz Forrester multimodel results",
        "",
        f"Review: `{report['round_id']}` — 96/96 samples completed.",
        "",
        "## Reference decision",
        "",
        "Clean actor identity references won 33 approvals to 7. Cleaned drama clips remain performance references only; they are rejected as speaker-identity anchors.",
        "",
        "## Routing decisions",
        "",
        "| Character | Delivery | Decision |",
        "|---|---|---|",
    ]
    for identity, styles in report["routing"].items():
        for style, decision in styles.items():
            if decision["status"] == "winner":
                value = MODEL_LABELS[decision["model"]]
            elif decision["status"] == "pairwise":
                value = "Pairwise: " + " vs ".join(MODEL_LABELS[item] for item in decision["models"])
            else:
                value = "Targeted control retest"
            lines.append(f"| {identity.title()} | {style.replace('_', ' ')} | {value} |")
    lines.extend([
        "",
        "## Interpretation",
        "",
        "Fish is the strongest emotional specialist, IndexTTS2 is strongest for neutral/command lanes, and VoxCPM2 has a specific Roz dry-humour win. No single model should be assigned universally.",
        "",
        "Artifact ratings contain a few contradictory rows, so final ties are resolved by blind pairwise listening rather than arithmetic alone.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("review")
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE))
    args = parser.parse_args()
    evidence = Path(args.evidence_root).expanduser().resolve()
    output = evidence / "results-analysis"
    output.mkdir(parents=True, exist_ok=True)
    report = build_report(
        read_json(Path(args.review).expanduser().resolve()),
        read_json(evidence / "private/answer-key.json"),
    )
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (output / "report.md").write_text(markdown(report) + "\n", encoding="utf-8")
    print(json.dumps({
        "report": str(output / "report.json"),
        "reference_tier": report["reference_tier_decision"],
        "decisive_winners": report["decisive_winners"],
        "pairwise_required": report["pairwise_required"],
        "control_retests_required": report["control_retests_required"],
        "artifact_inconsistency_count": len(report["artifact_score_inconsistencies"]),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
