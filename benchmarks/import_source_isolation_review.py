#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROUND_ID = "alexandria_new_source_isolation_v1"

FAMILY_MAP = {
    "Conversational / reflective": "neutral_conversation",
    "Warm / affectionate / relieved": "warm_affectionate_relief",
    "Playful / comic / curious": "playful_comic_curiosity",
    "Surprised / awed": "surprise_wonder",
    "Vulnerable / pleading": "vulnerable_pleading",
    "Sad / grief-stricken": "sad_grief",
    "Fearful / anxious": "fear_anxiety",
    "Panic / urgent danger": "panic_emergency",
    "Controlled anger": "controlled_anger",
    "Explosive anger": "explosive_anger",
    "Firm / authoritative": "firm_authority",
    "Menacing / cold": "cold_menace",
    "Exhausted / resigned": "exhausted_resigned",
    "Whisper / low voice": "whisper_intimate",
    "Other / mixed": "other_mixed",
}

ACCEPTED_SPEAKER_MATCH = {
    "Definitely target speaker",
    "Probably target speaker",
}
TARGET_CHARACTER_ROLE = "Target character performance"


class IsolationImportError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    if not path.is_file():
        raise IsolationImportError(f"JSON file is missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IsolationImportError(f"Invalid JSON in {path}: {exc}") from exc


def shortlist_rows(payload: Any) -> list[dict[str, Any]]:
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise IsolationImportError("Shortlist JSON must contain a non-empty rows list.")
    return rows


def review_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise IsolationImportError("Review JSON must contain an object.")
    if payload.get("round_id") != ROUND_ID:
        raise IsolationImportError(
            f"Unexpected review round_id: {payload.get('round_id')!r}; expected {ROUND_ID!r}."
        )
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise IsolationImportError("Review JSON must contain a non-empty rows list.")
    return rows


def canonical_id(row: dict[str, Any]) -> str:
    value = row.get("candidate_id")
    if not isinstance(value, str) or not value.strip():
        raise IsolationImportError("Every row requires candidate_id.")
    return value.strip()


def build_bank(shortlist_payload: Any, review_payload: Any) -> dict[str, Any]:
    shortlist = {canonical_id(row): row for row in shortlist_rows(shortlist_payload)}
    reviewed = review_rows(review_payload)
    seen: set[str] = set()
    approved: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for review in reviewed:
        candidate_id = canonical_id(review)
        if candidate_id in seen:
            raise IsolationImportError(f"Duplicate review row: {candidate_id}")
        seen.add(candidate_id)
        source = shortlist.get(candidate_id)
        if source is None:
            raise IsolationImportError(f"Review references an unknown candidate: {candidate_id}")

        family_label = review.get("dramatic_family")
        family_key = FAMILY_MAP.get(family_label)
        intensity = review.get("intensity_1_to_5")
        try:
            intensity_value = int(intensity)
        except (TypeError, ValueError):
            intensity_value = 0

        reasons: list[str] = []
        if review.get("speaker_match") not in ACCEPTED_SPEAKER_MATCH:
            reasons.append("speaker_not_confirmed")
        if review.get("performance_role") != TARGET_CHARACTER_ROLE:
            reasons.append("not_target_character_performance")
        if review.get("clean_reference_audio") is not True:
            reasons.append("audio_not_approved_clean")
        if family_key is None:
            reasons.append("dramatic_family_missing_or_unknown")
        if not 1 <= intensity_value <= 5:
            reasons.append("intensity_missing_or_invalid")

        common = {
            "candidate_id": candidate_id,
            "target": source.get("target"),
            "source_title": source.get("source_title"),
            "source_file": source.get("source"),
            "candidate_audio": source.get("file"),
            "candidate_audio_sha256": source.get("sha256"),
            "start_seconds": source.get("start_seconds"),
            "end_seconds": source.get("end_seconds"),
            "speaker_probability": source.get("speaker_probability"),
            "speaker_match": review.get("speaker_match"),
            "performance_role": review.get("performance_role"),
            "dramatic_family_label": family_label,
            "dramatic_family": family_key,
            "intensity_1_to_5": intensity_value or None,
            "mine_nearby_audio": review.get("mine_nearby_audio") is True,
            "notes": review.get("notes") or None,
        }
        if reasons:
            rejected.append({**common, "rejection_reasons": reasons})
        else:
            approved.append(
                {
                    **common,
                    "reference_status": "approved_source_reference",
                    "production_promotion_allowed": False,
                }
            )

    missing_reviews = sorted(set(shortlist) - seen)
    by_target = Counter(str(row.get("target")) for row in approved)
    by_family = Counter(str(row.get("dramatic_family")) for row in approved)
    return {
        "schema_version": 1,
        "source_round_id": ROUND_ID,
        "created_at": now_iso(),
        "reviewed_candidate_count": len(reviewed),
        "shortlist_candidate_count": len(shortlist),
        "approved_reference_count": len(approved),
        "rejected_reference_count": len(rejected),
        "missing_review_candidate_ids": missing_reviews,
        "coverage": {
            "by_target": dict(sorted(by_target.items())),
            "by_dramatic_family": dict(sorted(by_family.items())),
        },
        "approved_references": approved,
        "rejected_candidates": rejected,
        "production_promotion_allowed": False,
    }


def validate_bank(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise IsolationImportError("Bank must be a JSON object.")
    approved = payload.get("approved_references")
    rejected = payload.get("rejected_candidates")
    if not isinstance(approved, list) or not isinstance(rejected, list):
        raise IsolationImportError("Bank requires approved_references and rejected_candidates lists.")
    ids = [canonical_id(row) for row in approved + rejected]
    if len(ids) != len(set(ids)):
        raise IsolationImportError("Bank contains duplicate candidate IDs.")
    for row in approved:
        if row.get("dramatic_family") not in set(FAMILY_MAP.values()):
            raise IsolationImportError(f"Approved row has invalid dramatic_family: {row}")
        if row.get("reference_status") != "approved_source_reference":
            raise IsolationImportError("Approved row has an invalid reference_status.")
        if row.get("production_promotion_allowed") is not False:
            raise IsolationImportError("Source references may not auto-promote to production.")
    if payload.get("production_promotion_allowed") is not False:
        raise IsolationImportError("Bank may not auto-promote to production.")
    return {
        "approved_reference_count": len(approved),
        "rejected_reference_count": len(rejected),
        "candidate_count": len(ids),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import a reviewed source-isolation shortlist into a versioned performance bank.")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--shortlist", required=True)
    build.add_argument("--review", required=True)
    build.add_argument("--output", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--bank", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "build":
            payload = build_bank(
                load_json(Path(args.shortlist).expanduser().resolve()),
                load_json(Path(args.review).expanduser().resolve()),
            )
            output = Path(args.output).expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            result = {**validate_bank(payload), "output": str(output)}
        else:
            result = validate_bank(load_json(Path(args.bank).expanduser().resolve()))
    except IsolationImportError as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
