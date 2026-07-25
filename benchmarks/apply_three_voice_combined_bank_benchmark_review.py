#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

BENCHMARK_ROUND_ID = "alexandria_three_voice_combined_bank_generation_benchmark_v1"
FLAWED_BANK_ROUND_ID = "alexandria_three_voice_combined_reference_bank_v1"
VALIDATED_CORE_ROUND_ID = "alexandria_three_voice_validated_core_reference_bank_v1"
APPLIED_ROUND_ID = "alexandria_three_voice_combined_bank_benchmark_review_applied_v1"
ALLOWED_DECISIONS = {"candidate_A", "candidate_B", "neither"}
ISSUE_KEYS = {"identity_drift", "weak_delivery", "wrong_pacing", "artifacts"}


class BenchmarkReviewError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    if not path.is_file():
        raise BenchmarkReviewError(f"JSON file is missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BenchmarkReviewError(f"Invalid JSON in {path}: {exc}") from exc


def require_round(payload: Any, expected: str, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise BenchmarkReviewError(f"{label} must be a JSON object.")
    if payload.get("round_id") != expected:
        raise BenchmarkReviewError(
            f"Unexpected {label} round_id: {payload.get('round_id')!r}; expected {expected!r}."
        )
    return payload


def rows_by_id(rows: Any, *, key: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise BenchmarkReviewError(f"{label} must contain a non-empty list.")
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise BenchmarkReviewError(f"Every {label} row must be an object.")
        value = row.get(key)
        if not isinstance(value, str) or not value.strip():
            raise BenchmarkReviewError(f"Every {label} row requires {key}.")
        value = value.strip()
        if value in indexed:
            raise BenchmarkReviewError(f"Duplicate {label} {key}: {value}")
        indexed[value] = row
    return indexed


def answer_rows(payload: Any) -> dict[str, dict[str, Any]]:
    return rows_by_id(payload, key="route_id", label="answer key")


def reference_index(bank: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = bank.get("references")
    if not isinstance(rows, list) or len(rows) != int(bank.get("reference_count") or 0):
        raise BenchmarkReviewError("Reference bank is missing its complete references list.")
    return rows_by_id(rows, key="clip_id", label="reference bank")


def normalized_note(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def wrong_seventh_doctor_note(value: Any) -> bool:
    note = normalized_note(value)
    return (
        "not the 7th doctor" in note
        or "not the seventh doctor" in note
        or "isnt the 7th doctor" in note
        or "isnt the seventh doctor" in note
    )


def validate_audio(row: dict[str, Any], label: str) -> None:
    path = Path(str(row.get("audio_path") or ""))
    expected = row.get("audio_sha256")
    if not path.is_file():
        raise BenchmarkReviewError(f"{label} audio is missing: {path}")
    actual = sha256_file(path)
    if not isinstance(expected, str) or actual != expected:
        raise BenchmarkReviewError(
            f"{label} audio hash mismatch: expected={expected!r}, actual={actual!r}, path={path}"
        )


def selected_candidate_letter(decision: str) -> str | None:
    if decision == "candidate_A":
        return "A"
    if decision == "candidate_B":
        return "B"
    return None


def quality_status(reviewed: dict[str, Any], selected_letter: str | None) -> str:
    if selected_letter is None:
        return "neither_usable"
    issues = reviewed.get(f"candidate_{selected_letter}_issues") or []
    note = normalized_note(reviewed.get("notes"))
    if issues or "huge flaws" in note or "dont love either" in note:
        return "preference_only_quality_blocked"
    return "clean_preference"


def apply_review(
    *,
    answer_key_payload: Any,
    review_payload: dict[str, Any],
    flawed_bank_payload: dict[str, Any],
    validated_core_payload: dict[str, Any],
    source_paths: dict[str, Path],
    source_export_sha256: str | None,
) -> dict[str, Any]:
    review = require_round(review_payload, BENCHMARK_ROUND_ID, "benchmark review")
    flawed_bank = require_round(flawed_bank_payload, FLAWED_BANK_ROUND_ID, "superseded combined bank")
    validated_core = require_round(validated_core_payload, VALIDATED_CORE_ROUND_ID, "validated core bank")
    answers = answer_rows(answer_key_payload)
    reviews = rows_by_id(review.get("rows"), key="route_id", label="benchmark review")
    if set(answers) != set(reviews):
        raise BenchmarkReviewError(
            f"Answer/review route mismatch: answer={sorted(answers)}, review={sorted(reviews)}"
        )
    if int(review.get("summary", {}).get("complete_count") or 0) != len(reviews):
        raise BenchmarkReviewError("Benchmark review is not complete.")

    flawed_refs = reference_index(flawed_bank)
    core_refs = reference_index(validated_core)
    outcomes: list[dict[str, Any]] = []
    invalid_routes: list[dict[str, Any]] = []
    rejected_bank_clips: list[dict[str, Any]] = []

    for route_id, reviewed in reviews.items():
        answer = answers[route_id]
        for key in ("target", "function", "target_text"):
            if reviewed.get(key) != answer.get(key):
                raise BenchmarkReviewError(
                    f"Review changed stable field {key} for {route_id}: "
                    f"{reviewed.get(key)!r} != {answer.get(key)!r}"
                )
        decision = reviewed.get("decision")
        if decision not in ALLOWED_DECISIONS:
            raise BenchmarkReviewError(f"Invalid decision for {route_id}: {decision!r}")
        for candidate_letter in ("A", "B"):
            issues = reviewed.get(f"candidate_{candidate_letter}_issues") or []
            if not isinstance(issues, list) or any(issue not in ISSUE_KEYS for issue in issues):
                raise BenchmarkReviewError(
                    f"Invalid Candidate {candidate_letter} issue list for {route_id}: {issues!r}"
                )

        combined = answer.get("combined_bank_candidate")
        legacy = answer.get("legacy_candidate")
        performance_reference = answer.get("performance_reference")
        if not all(isinstance(item, dict) for item in (combined, legacy, performance_reference)):
            raise BenchmarkReviewError(f"Answer key is incomplete for {route_id}")
        validate_audio(combined, f"{route_id}:combined")
        validate_audio(legacy, f"{route_id}:legacy")
        validate_audio(performance_reference, f"{route_id}:performance_reference")

        bank_clip_id = str(performance_reference.get("clip_id") or "")
        flawed_reference = flawed_refs.get(bank_clip_id)
        if flawed_reference is None:
            raise BenchmarkReviewError(
                f"Superseded bank does not contain performance reference {bank_clip_id} for {route_id}."
            )
        explicitly_validated = bank_clip_id in core_refs
        wrong_speaker = wrong_seventh_doctor_note(reviewed.get("notes"))
        invalid_reasons: list[str] = []
        if not explicitly_validated:
            invalid_reasons.append("bank_reference_not_explicitly_human_approved")
        if wrong_speaker:
            invalid_reasons.append("authentic_reference_wrong_speaker")
            rejected_bank_clips.append(
                {
                    "clip_id": bank_clip_id,
                    "target": reviewed.get("target"),
                    "disposition": "rejected_wrong_speaker",
                    "evidence_route_id": route_id,
                    "review_notes": reviewed.get("notes"),
                    "review_updated_at": reviewed.get("updated_at"),
                }
            )

        selected_letter = selected_candidate_letter(str(decision))
        winner_role = None
        if selected_letter is not None:
            mapping = answer.get("candidate_mapping")
            if not isinstance(mapping, dict) or mapping.get(selected_letter) not in {
                "combined_bank",
                "legacy_reference",
            }:
                raise BenchmarkReviewError(f"Candidate mapping is invalid for {route_id}")
            winner_role = mapping[selected_letter]
        route_quality = quality_status(reviewed, selected_letter)
        outcome = {
            "route_id": route_id,
            "target": reviewed.get("target"),
            "target_label": reviewed.get("target_label"),
            "function": reviewed.get("function"),
            "function_label": reviewed.get("function_label"),
            "target_text": reviewed.get("target_text"),
            "bank_clip_id": bank_clip_id,
            "bank_reference_provenance": flawed_reference.get("provenance"),
            "bank_reference_explicitly_validated": explicitly_validated,
            "review_decision": decision,
            "selected_candidate": selected_letter,
            "selected_role": winner_role,
            "quality_status": route_quality,
            "candidate_A_issues": reviewed.get("candidate_A_issues") or [],
            "candidate_B_issues": reviewed.get("candidate_B_issues") or [],
            "review_notes": reviewed.get("notes"),
            "review_updated_at": reviewed.get("updated_at"),
            "benchmark_valid": not invalid_reasons,
            "invalid_reasons": invalid_reasons,
            "production_promotion_allowed": False,
        }
        outcomes.append(outcome)
        if invalid_reasons:
            invalid_routes.append(
                {
                    "route_id": route_id,
                    "bank_clip_id": bank_clip_id,
                    "reasons": invalid_reasons,
                }
            )

    valid = [row for row in outcomes if row["benchmark_valid"]]
    valid_combined_wins = [
        row for row in valid if row["selected_role"] == "combined_bank"
    ]
    valid_legacy_wins = [
        row for row in valid if row["selected_role"] == "legacy_reference"
    ]
    clean_valid_combined_wins = [
        row
        for row in valid_combined_wins
        if row["quality_status"] == "clean_preference"
    ]
    quality_blocked_combined_preferences = [
        row
        for row in valid_combined_wins
        if row["quality_status"] != "clean_preference"
    ]

    payload = {
        "schema_version": 1,
        "round_id": APPLIED_ROUND_ID,
        "created_at": now_iso(),
        "source_export_sha256": source_export_sha256,
        "review_exported_at": review.get("exported_at"),
        "route_count": len(outcomes),
        "valid_route_count": len(valid),
        "invalid_route_count": len(invalid_routes),
        "valid_combined_bank_win_count": len(valid_combined_wins),
        "valid_legacy_reference_win_count": len(valid_legacy_wins),
        "clean_valid_combined_bank_win_count": len(clean_valid_combined_wins),
        "quality_blocked_combined_preference_count": len(quality_blocked_combined_preferences),
        "broad_bank_improvement_claim_supported": False,
        "conclusion": (
            "Only the two Narrator routes used explicitly validated core-bank references. "
            "Both preferred the core-bank prompt, but only explosive anger was a clean win; "
            "ecstatic joy remained quality-blocked. Benny and Doctor results from this round "
            "cannot support bank conclusions because their bank references came from the "
            "unreviewed historical candidate set, and the Doctor urgency reference was the wrong speaker."
        ),
        "outcomes": sorted(outcomes, key=lambda row: row["route_id"]),
        "invalid_routes": sorted(invalid_routes, key=lambda row: row["route_id"]),
        "rejected_bank_clips": sorted(rejected_bank_clips, key=lambda row: row["clip_id"]),
        "source_artifacts": {
            name: {"path": str(path), "sha256": sha256_file(path)}
            for name, path in source_paths.items()
        },
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    return payload


def validate_applied(payload: Any) -> dict[str, Any]:
    applied = require_round(payload, APPLIED_ROUND_ID, "applied benchmark review")
    outcomes = applied.get("outcomes")
    invalid = applied.get("invalid_routes")
    if not isinstance(outcomes, list) or len(outcomes) != int(applied.get("route_count") or 0):
        raise BenchmarkReviewError("Applied outcomes are missing or incomplete.")
    if not isinstance(invalid, list) or len(invalid) != int(applied.get("invalid_route_count") or 0):
        raise BenchmarkReviewError("Applied invalid-route ledger is missing or incomplete.")
    valid = [row for row in outcomes if row.get("benchmark_valid") is True]
    if len(valid) != int(applied.get("valid_route_count") or 0):
        raise BenchmarkReviewError("Applied valid route count is inconsistent.")
    if applied.get("broad_bank_improvement_claim_supported") is not False:
        raise BenchmarkReviewError("This benchmark cannot support a broad bank-improvement claim.")
    if applied.get("automatic_production_assignment") is not False:
        raise BenchmarkReviewError("Automatic production assignment must remain disabled.")
    if applied.get("production_promotion_allowed") is not False:
        raise BenchmarkReviewError("Production promotion must remain disabled.")
    return {
        "route_count": len(outcomes),
        "valid_route_count": len(valid),
        "invalid_route_count": len(invalid),
        "valid_combined_bank_win_count": applied.get("valid_combined_bank_win_count"),
        "clean_valid_combined_bank_win_count": applied.get("clean_valid_combined_bank_win_count"),
        "broad_bank_improvement_claim_supported": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply the blinded combined-bank benchmark review and invalidate unsupported routes."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--answer-key", required=True)
    apply_parser.add_argument("--review", required=True)
    apply_parser.add_argument("--superseded-bank", required=True)
    apply_parser.add_argument("--validated-core-bank", required=True)
    apply_parser.add_argument("--output", required=True)
    apply_parser.add_argument("--source-export-sha256")
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--applied", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "apply":
            paths = {
                "benchmark_answer_key": Path(args.answer_key).expanduser().resolve(),
                "benchmark_review": Path(args.review).expanduser().resolve(),
                "superseded_combined_bank": Path(args.superseded_bank).expanduser().resolve(),
                "validated_core_bank": Path(args.validated_core_bank).expanduser().resolve(),
            }
            payload = apply_review(
                answer_key_payload=load_json(paths["benchmark_answer_key"]),
                review_payload=load_json(paths["benchmark_review"]),
                flawed_bank_payload=load_json(paths["superseded_combined_bank"]),
                validated_core_payload=load_json(paths["validated_core_bank"]),
                source_paths=paths,
                source_export_sha256=args.source_export_sha256,
            )
            output = Path(args.output).expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            result = {**validate_applied(payload), "output": str(output)}
        else:
            result = validate_applied(load_json(Path(args.applied).expanduser().resolve()))
    except (BenchmarkReviewError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
