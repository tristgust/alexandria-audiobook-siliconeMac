#!/usr/bin/env python3
"""Merge partial Round 1 blind-review exports and prepare Round 2 evidence.

The listener never selects a complex Round 2 disposition. This tool derives a
conservative preliminary disposition from the actual ratings, yes/no decisions,
notes, and optional follow-up flag after unblinding through the internal
manifest. It is cumulative: any number of style, group, or full exports may be
merged over time.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_EVIDENCE = Path(
    "/Users/tristan/.devspace/worktrees/"
    "alexandria-audiobook.git-78fc5814/.omo/evidence/"
    "b17-t05-multimodel-round1"
)
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
REQUIRED_FIELDS = RATING_FIELDS + BOOLEAN_FIELDS
OPTIONAL_FIELDS = ("flag_for_follow_up", "notes", "updated_at")
ALLOWED_FIELDS = set(("sample_id",) + REQUIRED_FIELDS + OPTIONAL_FIELDS)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalized_row(raw: dict[str, Any]) -> dict[str, Any]:
    unknown = set(raw) - ALLOWED_FIELDS
    if unknown:
        # Old UI exports may contain the removed field. It is deliberately ignored.
        unknown.discard("round2_disposition")
    if unknown:
        raise ValueError(f"Unsupported review fields: {sorted(unknown)}")
    sample_id = str(raw.get("sample_id") or "").strip()
    if not sample_id:
        raise ValueError("Review row has no sample_id.")
    row: dict[str, Any] = {"sample_id": sample_id}
    for field in RATING_FIELDS:
        if field not in raw:
            continue
        value = raw[field]
        if value in (None, ""):
            row[field] = None
            continue
        value = int(value)
        if value < 1 or value > 5:
            raise ValueError(f"{field} must be from 1 to 5 for {sample_id}.")
        row[field] = value
    for field in BOOLEAN_FIELDS + ("flag_for_follow_up",):
        if field not in raw:
            continue
        value = raw[field]
        if value in (None, ""):
            row[field] = None
        elif isinstance(value, bool):
            row[field] = value
        else:
            raise ValueError(f"{field} must be true, false, or null for {sample_id}.")
    if "notes" in raw:
        row["notes"] = str(raw.get("notes") or "").strip()
    if "updated_at" in raw:
        row["updated_at"] = str(raw.get("updated_at") or "").strip() or None
    return row


def is_complete(row: dict[str, Any]) -> bool:
    return all(row.get(field) not in (None, "") for field in REQUIRED_FIELDS)


def preliminary_disposition(row: dict[str, Any]) -> str:
    if not is_complete(row):
        return "pending"
    if (
        row["spoken_text_matches_expected"] is not True
        or row["requested_mode_is_clear"] is not True
        or row["approve_for_comparison"] is not True
    ):
        return "reject"
    if row.get("flag_for_follow_up") is True:
        return "targeted_follow_up"
    strong = (
        int(row["identity_1_to_5"]) >= 4
        and int(row["delivery_1_to_5"]) >= 4
        and int(row["naturalness_1_to_5"]) >= 4
        and int(row["artifact_severity_1_to_5"]) <= 2
    )
    if strong:
        return "strong_round2_candidate"
    comparison = (
        int(row["identity_1_to_5"]) >= 3
        and int(row["delivery_1_to_5"]) >= 3
        and int(row["naturalness_1_to_5"]) >= 3
        and int(row["artifact_severity_1_to_5"]) <= 3
    )
    return "comparison_candidate" if comparison else "targeted_follow_up"


def mean_or_none(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def aggregate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    complete = [row for row in rows if row["review_complete"]]
    return {
        "sample_count": len(rows),
        "reviewed_count": len(complete),
        "pending_count": len(rows) - len(complete),
        "mean_identity": mean_or_none(
            [float(row["identity_1_to_5"]) for row in complete]
        ),
        "mean_delivery": mean_or_none(
            [float(row["delivery_1_to_5"]) for row in complete]
        ),
        "mean_naturalness": mean_or_none(
            [float(row["naturalness_1_to_5"]) for row in complete]
        ),
        "mean_artifact_severity": mean_or_none(
            [float(row["artifact_severity_1_to_5"]) for row in complete]
        ),
        "text_match_rate": mean_or_none(
            [1.0 if row["spoken_text_matches_expected"] else 0.0 for row in complete]
        ),
        "mode_clear_rate": mean_or_none(
            [1.0 if row["requested_mode_is_clear"] else 0.0 for row in complete]
        ),
        "keep_rate": mean_or_none(
            [1.0 if row["approve_for_comparison"] else 0.0 for row in complete]
        ),
        "follow_up_flag_count": sum(
            row.get("flag_for_follow_up") is True for row in complete
        ),
        "dispositions": {
            key: sum(row["preliminary_disposition"] == key for row in rows)
            for key in (
                "strong_round2_candidate",
                "comparison_candidate",
                "targeted_follow_up",
                "reject",
                "pending",
            )
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("exports", nargs="*", help="Style, group, or cumulative JSON exports")
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    evidence_root = Path(args.evidence_root).expanduser().resolve()
    internal = read_json(evidence_root / "round1_internal_manifest.json")
    if internal["round_id"] != "alexandria_multimodel_expressive_clone_round1_v1":
        raise RuntimeError("Unexpected Round 1 identity.")
    by_blind_id = {item["blind_id"]: item for item in internal["sample_specs"]}

    review_root = evidence_root / "human-review"
    review_root.mkdir(parents=True, exist_ok=True)
    cumulative_path = review_root / "cumulative_results.json"
    cumulative: dict[str, dict[str, Any]] = {}
    prior_sources: list[dict[str, Any]] = []
    if cumulative_path.is_file() and not args.reset:
        prior = read_json(cumulative_path)
        cumulative = {
            item["sample_id"]: item for item in prior.get("rows", [])
        }
        prior_sources = list(prior.get("source_files") or [])

    new_sources: list[dict[str, Any]] = []
    import_counts = {
        "input_file_count": 0,
        "input_row_count": 0,
        "merged_row_count": 0,
        "unknown_sample_count": 0,
    }
    for value in args.exports:
        path = Path(value).expanduser().resolve()
        payload = read_json(path)
        if isinstance(payload, list):
            rows = payload
            round_id = internal["round_id"]
        else:
            round_id = payload.get("round_id") or internal["round_id"]
            rows = payload.get("rows") or []
        if round_id != internal["round_id"]:
            raise ValueError(f"Export belongs to a different round: {path}")
        import_counts["input_file_count"] += 1
        import_counts["input_row_count"] += len(rows)
        source = {
            "path": str(path),
            "sha256": sha256_file(path),
            "row_count": len(rows),
            "merged_at": utc_now(),
        }
        new_sources.append(source)
        for raw in rows:
            row = normalized_row(dict(raw))
            sample_id = row["sample_id"]
            if sample_id not in by_blind_id:
                import_counts["unknown_sample_count"] += 1
                continue
            cumulative[sample_id] = {
                **cumulative.get(sample_id, {"sample_id": sample_id}),
                **row,
            }
            import_counts["merged_row_count"] += 1

    cumulative_rows = sorted(cumulative.values(), key=lambda item: item["sample_id"])
    cumulative_payload = {
        "schema_version": 1,
        "round_id": internal["round_id"],
        "updated_at": utc_now(),
        "row_count": len(cumulative_rows),
        "complete_row_count": sum(is_complete(row) for row in cumulative_rows),
        "source_files": prior_sources + new_sources,
        "latest_import": import_counts,
        "rows": cumulative_rows,
        "production_promotion_allowed": False,
    }
    cumulative_path.write_text(
        json.dumps(cumulative_payload, indent=2) + "\n", encoding="utf-8"
    )

    unblinded_rows: list[dict[str, Any]] = []
    for blind_id, sample in by_blind_id.items():
        review = cumulative.get(blind_id, {"sample_id": blind_id})
        complete = is_complete(review)
        row = {
            "sample_id": blind_id,
            "source_sample_id": sample["sample_id"],
            "model_key": sample["model_key"],
            "model_label": sample["model_label"],
            "identity_key": sample["identity_key"],
            "expected_identity": sample["identity_review_name"],
            "style": sample["style"],
            "style_label": sample["style_label"],
            "group": sample["group"],
            "review_complete": complete,
            "preliminary_disposition": preliminary_disposition(review),
            **{field: review.get(field) for field in REQUIRED_FIELDS + OPTIONAL_FIELDS},
        }
        unblinded_rows.append(row)

    by_model: dict[str, list[dict[str, Any]]] = {}
    by_model_identity: dict[str, list[dict[str, Any]]] = {}
    by_model_style: dict[str, list[dict[str, Any]]] = {}
    by_cell: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in unblinded_rows:
        by_model.setdefault(row["model_key"], []).append(row)
        by_model_identity.setdefault(
            f"{row['model_key']}::{row['identity_key']}", []
        ).append(row)
        by_model_style.setdefault(f"{row['model_key']}::{row['style']}", []).append(row)
        by_cell.setdefault((row["identity_key"], row["style"]), []).append(row)

    eligible = {
        "strong_round2_candidate",
        "comparison_candidate",
    }
    proposed_pairs: list[dict[str, Any]] = []
    for (identity_key, style), rows in sorted(by_cell.items()):
        candidates = [
            row for row in rows if row["preliminary_disposition"] in eligible
        ]
        candidates.sort(key=lambda item: item["sample_id"])
        for left, right in itertools.combinations(candidates, 2):
            pair_members = sorted([left["sample_id"], right["sample_id"]])
            pair_id = "r2pair_" + sha256_text(
                "\0".join(
                    [internal["round_id"], identity_key, style, *pair_members]
                )
            )[:20]
            proposed_pairs.append(
                {
                    "pair_id": pair_id,
                    "identity_key": identity_key,
                    "expected_identity": left["expected_identity"],
                    "style": style,
                    "group": left["group"],
                    "candidate_sample_ids": pair_members,
                    "candidate_models": sorted(
                        [left["model_key"], right["model_key"]]
                    ),
                    "status": "preliminary_pair_candidate",
                    "left_right_randomization_pending": True,
                }
            )

    prep_payload = {
        "schema_version": 1,
        "round_id": internal["round_id"],
        "updated_at": utc_now(),
        "reviewed_sample_count": sum(
            row["review_complete"] for row in unblinded_rows
        ),
        "pending_sample_count": sum(
            not row["review_complete"] for row in unblinded_rows
        ),
        "disposition_counts": {
            key: sum(row["preliminary_disposition"] == key for row in unblinded_rows)
            for key in (
                "strong_round2_candidate",
                "comparison_candidate",
                "targeted_follow_up",
                "reject",
                "pending",
            )
        },
        "by_model": {
            key: aggregate_rows(rows) for key, rows in sorted(by_model.items())
        },
        "by_model_and_identity": {
            key: aggregate_rows(rows)
            for key, rows in sorted(by_model_identity.items())
        },
        "by_model_and_style": {
            key: aggregate_rows(rows)
            for key, rows in sorted(by_model_style.items())
        },
        "proposed_pair_count": len(proposed_pairs),
        "proposed_pairs": proposed_pairs,
        "pairwise_review_not_generated_yet": True,
        "pairwise_decision_options": [
            "A is better",
            "No meaningful preference",
            "B is better",
        ],
        "manual_round2_disposition_field_used": False,
        "manual_blinded_review_required": True,
        "production_promotion_allowed": False,
    }
    prep_path = review_root / "round2_preparation.json"
    prep_path.write_text(
        json.dumps(prep_payload, indent=2) + "\n", encoding="utf-8"
    )
    unblinded_path = review_root / "unblinded_results.json"
    unblinded_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "round_id": internal["round_id"],
                "updated_at": utc_now(),
                "rows": unblinded_rows,
                "production_promotion_allowed": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "cumulative_results": str(cumulative_path),
                "unblinded_results": str(unblinded_path),
                "round2_preparation": str(prep_path),
                "merged_review_row_count": len(cumulative_rows),
                "complete_review_row_count": cumulative_payload[
                    "complete_row_count"
                ],
                "proposed_pair_count": len(proposed_pairs),
                "latest_import": import_counts,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
