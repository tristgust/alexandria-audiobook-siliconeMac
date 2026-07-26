#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REVIEW_ROUND_ID = "alexandria_three_voice_final_bank_generation_benchmark_v1"
APPLIED_ROUND_ID = "alexandria_three_voice_final_bank_benchmark_review_applied_v1"
FINAL_BANK_ROUND_ID = "alexandria_three_voice_validated_reference_bank_v3"
PRIOR_APPLIED_ROUND_ID = "alexandria_three_voice_combined_bank_benchmark_review_applied_v1"
PRIOR_MATRIX_ROUND_ID = "alexandria_three_voice_combined_bank_generation_benchmark_v1"
CURRENT_MATRIX_ROUND_ID = REVIEW_ROUND_ID

ALLOWED_DECISIONS = {"candidate_A", "candidate_B", "neither"}
ALLOWED_ISSUES = {"identity_drift", "weak_delivery", "wrong_pacing", "artifacts"}
EXPECTED_SUMMARY_KEYS = {
    "candidate_count",
    "complete_count",
    "candidate_A_count",
    "candidate_B_count",
    "neither_count",
}


class FinalBankBenchmarkApplyError(RuntimeError):
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
        raise FinalBankBenchmarkApplyError(f"JSON file is missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FinalBankBenchmarkApplyError(f"Invalid JSON in {path}: {exc}") from exc


def require_object(payload: Any, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise FinalBankBenchmarkApplyError(f"{label} must be a JSON object.")
    return payload


def require_round(payload: Any, expected: str, label: str) -> dict[str, Any]:
    value = require_object(payload, label)
    if value.get("round_id") != expected:
        raise FinalBankBenchmarkApplyError(
            f"Unexpected {label} round_id: {value.get('round_id')!r}; expected {expected!r}."
        )
    return value


def unwrap_review(payload: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized = require_object(payload, "normalized review")
    if "review" in normalized:
        review = normalized.get("review")
        metadata = {
            "source_upload_name": normalized.get("source_upload_name"),
            "source_upload_sha256": normalized.get("source_upload_sha256"),
        }
    else:
        review = normalized
        metadata = {"source_upload_name": None, "source_upload_sha256": None}
    return require_round(review, REVIEW_ROUND_ID, "benchmark review"), metadata


def rows_by_id(rows: Any, *, key: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise FinalBankBenchmarkApplyError(f"{label} must contain a non-empty list.")
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise FinalBankBenchmarkApplyError(f"Every {label} row must be an object.")
        value = row.get(key)
        if not isinstance(value, str) or not value.strip():
            raise FinalBankBenchmarkApplyError(f"Every {label} row requires {key}.")
        value = value.strip()
        if value in indexed:
            raise FinalBankBenchmarkApplyError(f"Duplicate {label} {key}: {value}")
        indexed[value] = row
    return indexed


def answer_rows(payload: Any) -> dict[str, dict[str, Any]]:
    return rows_by_id(payload, key="route_id", label="answer key")


def verify_review_summary(review: dict[str, Any], rows: dict[str, dict[str, Any]]) -> Counter[str]:
    summary = review.get("summary")
    if not isinstance(summary, dict) or not EXPECTED_SUMMARY_KEYS.issubset(summary):
        raise FinalBankBenchmarkApplyError("Benchmark review summary is missing required counts.")
    decisions = Counter(str(row.get("decision") or "") for row in rows.values())
    if decisions[""]:
        raise FinalBankBenchmarkApplyError("Every benchmark route requires a decision.")
    if set(decisions) - ALLOWED_DECISIONS:
        raise FinalBankBenchmarkApplyError(f"Unexpected decisions: {sorted(set(decisions) - ALLOWED_DECISIONS)}")
    expected = {
        "candidate_count": len(rows),
        "complete_count": len(rows),
        "candidate_A_count": decisions["candidate_A"],
        "candidate_B_count": decisions["candidate_B"],
        "neither_count": decisions["neither"],
    }
    for key, value in expected.items():
        if summary.get(key) != value:
            raise FinalBankBenchmarkApplyError(
                f"Review summary mismatch for {key}: {summary.get(key)!r} != {value!r}."
            )
    return decisions


def validate_issues(route_id: str, label: str, values: Any) -> list[str]:
    if not isinstance(values, list):
        raise FinalBankBenchmarkApplyError(f"{route_id} {label} issues must be a list.")
    normalized = [str(value) for value in values]
    unknown = sorted(set(normalized) - ALLOWED_ISSUES)
    if unknown:
        raise FinalBankBenchmarkApplyError(f"Unknown issues for {route_id} {label}: {unknown}")
    if len(normalized) != len(set(normalized)):
        raise FinalBankBenchmarkApplyError(f"Duplicate issues for {route_id} {label}.")
    return normalized


def stable_route_check(review: dict[str, Any], answer: dict[str, Any]) -> None:
    route_id = answer["route_id"]
    for key in ("route_id", "target", "function", "target_text"):
        if review.get(key) != answer.get(key):
            raise FinalBankBenchmarkApplyError(
                f"Review changed stable field {key} for {route_id}: "
                f"{review.get(key)!r} != {answer.get(key)!r}."
            )
    mapping = answer.get("candidate_mapping")
    if not isinstance(mapping, dict) or set(mapping) != {"A", "B"}:
        raise FinalBankBenchmarkApplyError(f"Candidate mapping is invalid for {route_id}.")
    if set(mapping.values()) != {"combined_bank", "legacy_reference"}:
        raise FinalBankBenchmarkApplyError(f"Candidate roles are invalid for {route_id}: {mapping}")


def selected_role(decision: str, answer: dict[str, Any]) -> str:
    if decision == "neither":
        return "neither"
    label = decision.removeprefix("candidate_")
    return str(answer["candidate_mapping"][label])


def selected_issues(decision: str, issues_a: list[str], issues_b: list[str]) -> list[str]:
    if decision == "candidate_A":
        return issues_a
    if decision == "candidate_B":
        return issues_b
    return sorted(set(issues_a + issues_b))


def bank_index(bank_payload: Any) -> dict[str, dict[str, Any]]:
    bank = require_round(bank_payload, FINAL_BANK_ROUND_ID, "final validated bank")
    rows = rows_by_id(bank.get("references"), key="clip_id", label="final bank references")
    if len(rows) != int(bank.get("reference_count") or 0):
        raise FinalBankBenchmarkApplyError("Final bank reference count is inconsistent.")
    if len(rows) != 31:
        raise FinalBankBenchmarkApplyError(f"Expected the final 31-reference bank; found {len(rows)}.")
    return rows


def route_outcome(
    reviewed: dict[str, Any],
    answer: dict[str, Any],
    bank_refs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    stable_route_check(reviewed, answer)
    route_id = answer["route_id"]
    issues_a = validate_issues(route_id, "candidate A", reviewed.get("candidate_A_issues"))
    issues_b = validate_issues(route_id, "candidate B", reviewed.get("candidate_B_issues"))
    decision = str(reviewed.get("decision") or "")
    if decision not in ALLOWED_DECISIONS:
        raise FinalBankBenchmarkApplyError(f"Invalid decision for {route_id}: {decision!r}")
    role = selected_role(decision, answer)
    chosen_issues = selected_issues(decision, issues_a, issues_b)
    performance = answer.get("performance_reference") or {}
    clip_id = str(performance.get("clip_id") or "")
    bank_row = bank_refs.get(clip_id)
    if bank_row is None:
        raise FinalBankBenchmarkApplyError(f"Performance reference {clip_id} is not in the final bank.")
    if performance.get("audio_sha256") != bank_row.get("audio_sha256"):
        raise FinalBankBenchmarkApplyError(f"Performance-reference hash mismatch for {route_id}.")
    if role == "neither":
        quality_status = "unusable_both"
    elif chosen_issues:
        quality_status = "preference_quality_blocked"
    else:
        quality_status = "clean_preference"
    return {
        "route_id": route_id,
        "target": answer.get("target"),
        "target_label": reviewed.get("target_label"),
        "function": answer.get("function"),
        "function_label": reviewed.get("function_label"),
        "target_text": answer.get("target_text"),
        "bank_clip_id": clip_id,
        "bank_reference_status": bank_row.get("reference_status"),
        "bank_reference_provenance": bank_row.get("provenance"),
        "review_decision": decision,
        "selected_role": role,
        "quality_status": quality_status,
        "candidate_A_role": answer["candidate_mapping"]["A"],
        "candidate_B_role": answer["candidate_mapping"]["B"],
        "candidate_A_issues": issues_a,
        "candidate_B_issues": issues_b,
        "selected_issues": chosen_issues,
        "review_notes": reviewed.get("notes") or None,
        "review_revision": reviewed.get("revision"),
        "review_updated_at": reviewed.get("updated_at"),
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }


def exact_repeat_control(
    current_matrix_payload: Any,
    prior_matrix_payload: Any,
    prior_applied_payload: Any,
    current_outcomes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    current_matrix = require_round(current_matrix_payload, CURRENT_MATRIX_ROUND_ID, "current benchmark matrix")
    prior_matrix = require_round(prior_matrix_payload, PRIOR_MATRIX_ROUND_ID, "prior benchmark matrix")
    prior_applied = require_round(prior_applied_payload, PRIOR_APPLIED_ROUND_ID, "prior applied benchmark")
    current_routes = rows_by_id(current_matrix.get("routes"), key="route_id", label="current matrix routes")
    prior_routes = rows_by_id(prior_matrix.get("routes"), key="route_id", label="prior matrix routes")
    current = current_routes.get("narrator_anger_control")
    prior = prior_routes.get("narrator_anger")
    if current is None or prior is None:
        raise FinalBankBenchmarkApplyError("Narrator anger repeat control is missing.")
    stable_keys = (
        "target_text",
        "alpha",
        "identity_audio_sha256",
        "bank_reference_audio_sha256",
        "legacy_reference_audio_sha256",
    )
    exact_match = all(current.get(key) == prior.get(key) for key in stable_keys)
    prior_outcomes = rows_by_id(prior_applied.get("outcomes"), key="route_id", label="prior applied outcomes")
    prior_outcome = prior_outcomes.get("narrator_anger")
    current_outcome = current_outcomes.get("narrator_anger_control")
    if prior_outcome is None or current_outcome is None:
        raise FinalBankBenchmarkApplyError("Narrator anger outcomes are missing.")
    current_samples = {
        str(row.get("prompt_role")): str(row.get("sample_id"))
        for row in current_matrix.get("samples") or []
        if row.get("route_id") == "narrator_anger_control"
    }
    prior_samples = {
        str(row.get("prompt_role")): str(row.get("sample_id"))
        for row in prior_matrix.get("samples") or []
        if row.get("route_id") == "narrator_anger"
    }
    outcome_changed = (
        prior_outcome.get("selected_role") != current_outcome.get("selected_role")
        or prior_outcome.get("quality_status") != current_outcome.get("quality_status")
    )
    return {
        "control": "narrator_explosive_anger",
        "exact_configuration_match": exact_match,
        "matching_fields": list(stable_keys),
        "prior_round_id": prior_matrix.get("round_id"),
        "current_round_id": current_matrix.get("round_id"),
        "prior_sample_ids": prior_samples,
        "current_sample_ids": current_samples,
        "seed_changed_because_round_id_changed": prior_samples != current_samples,
        "prior_selected_role": prior_outcome.get("selected_role"),
        "prior_quality_status": prior_outcome.get("quality_status"),
        "current_selected_role": current_outcome.get("selected_role"),
        "current_quality_status": current_outcome.get("quality_status"),
        "outcome_changed": outcome_changed,
        "single_seed_decision_reliable": bool(exact_match and not outcome_changed),
    }


def apply_review(
    *,
    normalized_review_payload: Any,
    answer_payload: Any,
    bank_payload: Any,
    current_matrix_payload: Any,
    prior_matrix_payload: Any,
    prior_applied_payload: Any,
    source_paths: dict[str, Path],
) -> dict[str, Any]:
    review, upload_metadata = unwrap_review(normalized_review_payload)
    reviews = rows_by_id(review.get("rows"), key="route_id", label="benchmark review")
    answers = answer_rows(answer_payload)
    if set(reviews) != set(answers):
        raise FinalBankBenchmarkApplyError(
            f"Review/answer route mismatch: review={sorted(reviews)}, answer={sorted(answers)}"
        )
    verify_review_summary(review, reviews)
    bank_refs = bank_index(bank_payload)
    outcomes = [route_outcome(reviews[route_id], answers[route_id], bank_refs) for route_id in sorted(reviews)]
    outcome_index = {row["route_id"]: row for row in outcomes}
    role_counts = Counter(row["selected_role"] for row in outcomes)
    quality_counts = Counter(row["quality_status"] for row in outcomes)
    clean_role_counts = Counter(
        row["selected_role"] for row in outcomes if row["quality_status"] == "clean_preference"
    )
    repeat_control = exact_repeat_control(
        current_matrix_payload,
        prior_matrix_payload,
        prior_applied_payload,
        outcome_index,
    )
    bank_clean_wins = clean_role_counts["combined_bank"]
    bank_quality_blocked = sum(
        row["selected_role"] == "combined_bank" and row["quality_status"] == "preference_quality_blocked"
        for row in outcomes
    )
    legacy_clean_wins = clean_role_counts["legacy_reference"]
    legacy_quality_blocked = sum(
        row["selected_role"] == "legacy_reference" and row["quality_status"] == "preference_quality_blocked"
        for row in outcomes
    )
    conclusion = (
        "The final 31-reference bank did not demonstrate a production-routing advantage. "
        "It received one preference, and that result remained quality-blocked by weak delivery. "
        "The prior handpicked references won four routes, including three clean Benny/Doctor wins, "
        "while the exact repeated Narrator anger control changed from a prior clean bank win to "
        "neither candidate being usable. Retain the bank as a validated research library, do not "
        "install it as a general performance-prompt router, and move the investigation to repeated-"
        "seed generation reliability and runtime configuration."
    )
    return {
        "schema_version": 1,
        "round_id": APPLIED_ROUND_ID,
        "created_at": now_iso(),
        "source_upload": upload_metadata,
        "review_exported_at": review.get("exported_at"),
        "route_count": len(outcomes),
        "selected_role_counts": dict(sorted(role_counts.items())),
        "quality_status_counts": dict(sorted(quality_counts.items())),
        "combined_bank_preference_count": role_counts["combined_bank"],
        "legacy_reference_preference_count": role_counts["legacy_reference"],
        "neither_count": role_counts["neither"],
        "clean_combined_bank_win_count": bank_clean_wins,
        "quality_blocked_combined_bank_preference_count": bank_quality_blocked,
        "clean_legacy_reference_win_count": legacy_clean_wins,
        "quality_blocked_legacy_reference_preference_count": legacy_quality_blocked,
        "broad_bank_improvement_claim_supported": False,
        "reference_bank_prompt_routing_recommended": False,
        "validated_bank_research_library_retained": True,
        "single_seed_benchmark_reliable": repeat_control["single_seed_decision_reliable"],
        "exact_repeat_control": repeat_control,
        "conclusion": conclusion,
        "next_action": {
            "action": "generation_reliability_diagnostic",
            "scope": "repeated seeds using preferred handpicked references and fixed runtime settings",
            "reason": (
                "The exact Narrator anger control changed outcome under a different deterministic seed, "
                "so reference selection cannot be separated from generation variance using one sample."
            ),
            "reference_bank_mining_should_continue": False,
            "production_routing_changes_allowed": False,
        },
        "outcomes": outcomes,
        "source_artifacts": {
            name: {"path": str(path), "sha256": sha256_file(path)}
            for name, path in source_paths.items()
        },
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }


def validate_applied(payload: Any) -> dict[str, Any]:
    applied = require_round(payload, APPLIED_ROUND_ID, "applied benchmark review")
    outcomes = applied.get("outcomes")
    if not isinstance(outcomes, list) or len(outcomes) != 6:
        raise FinalBankBenchmarkApplyError("Applied benchmark must contain six outcomes.")
    ids = [row.get("route_id") for row in outcomes]
    if len(ids) != len(set(ids)):
        raise FinalBankBenchmarkApplyError("Applied benchmark contains duplicate routes.")
    for row in outcomes:
        if row.get("automatic_production_assignment") is not False:
            raise FinalBankBenchmarkApplyError(f"Automatic assignment enabled for {row.get('route_id')}")
        if row.get("production_promotion_allowed") is not False:
            raise FinalBankBenchmarkApplyError(f"Production promotion enabled for {row.get('route_id')}")
    expected_role_counts = Counter(str(row.get("selected_role")) for row in outcomes)
    if dict(sorted(expected_role_counts.items())) != applied.get("selected_role_counts"):
        raise FinalBankBenchmarkApplyError("Selected-role counts do not match outcomes.")
    if applied.get("reference_bank_prompt_routing_recommended") is not False:
        raise FinalBankBenchmarkApplyError("Reference-bank routing must remain disabled.")
    if applied.get("broad_bank_improvement_claim_supported") is not False:
        raise FinalBankBenchmarkApplyError("Broad bank-improvement claim must remain false.")
    repeat = applied.get("exact_repeat_control") or {}
    if repeat.get("exact_configuration_match") is not True:
        raise FinalBankBenchmarkApplyError("Exact repeated Narrator control was not preserved.")
    if repeat.get("outcome_changed") is not True:
        raise FinalBankBenchmarkApplyError("Repeated Narrator control should record changed outcome.")
    if repeat.get("single_seed_decision_reliable") is not False:
        raise FinalBankBenchmarkApplyError("Single-seed reliability must be false.")
    if applied.get("automatic_production_assignment") is not False:
        raise FinalBankBenchmarkApplyError("Automatic production assignment must remain disabled.")
    if applied.get("production_promotion_allowed") is not False:
        raise FinalBankBenchmarkApplyError("Production promotion must remain disabled.")
    return {
        "route_count": len(outcomes),
        "selected_role_counts": dict(sorted(expected_role_counts.items())),
        "clean_combined_bank_win_count": applied.get("clean_combined_bank_win_count"),
        "clean_legacy_reference_win_count": applied.get("clean_legacy_reference_win_count"),
        "single_seed_benchmark_reliable": applied.get("single_seed_benchmark_reliable"),
        "reference_bank_prompt_routing_recommended": applied.get("reference_bank_prompt_routing_recommended"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply the final 31-reference-bank generation benchmark review without production promotion."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--review", required=True)
    apply_parser.add_argument("--answer-key", required=True)
    apply_parser.add_argument("--bank", required=True)
    apply_parser.add_argument("--current-matrix", required=True)
    apply_parser.add_argument("--prior-matrix", required=True)
    apply_parser.add_argument("--prior-applied", required=True)
    apply_parser.add_argument("--output", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--applied", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "apply":
            paths = {
                "normalized_review": Path(args.review).expanduser().resolve(),
                "answer_key": Path(args.answer_key).expanduser().resolve(),
                "final_validated_bank": Path(args.bank).expanduser().resolve(),
                "current_benchmark_matrix": Path(args.current_matrix).expanduser().resolve(),
                "prior_benchmark_matrix": Path(args.prior_matrix).expanduser().resolve(),
                "prior_applied_benchmark": Path(args.prior_applied).expanduser().resolve(),
            }
            payload = apply_review(
                normalized_review_payload=load_json(paths["normalized_review"]),
                answer_payload=load_json(paths["answer_key"]),
                bank_payload=load_json(paths["final_validated_bank"]),
                current_matrix_payload=load_json(paths["current_benchmark_matrix"]),
                prior_matrix_payload=load_json(paths["prior_benchmark_matrix"]),
                prior_applied_payload=load_json(paths["prior_applied_benchmark"]),
                source_paths=paths,
            )
            output = Path(args.output).expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            result = validate_applied(payload)
            result["output"] = str(output)
        else:
            result = validate_applied(load_json(Path(args.applied).expanduser().resolve()))
    except (FinalBankBenchmarkApplyError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
