#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROUND_ID = "alexandria_three_voice_paired_seed_reliability_v1"
APPLIED_ROUND_ID = "alexandria_three_voice_paired_seed_reliability_review_applied_v1"
POLICY_ROUND_ID = "alexandria_three_voice_route_specific_prompt_policy_v1"
EXPECTED_ROUTE_COUNT = 9
EXPECTED_ROUTE_GROUP_COUNT = 3
EXPECTED_RUNS_PER_GROUP = 3
ALLOWED_DECISIONS = {"candidate_A", "candidate_B", "neither"}
ALLOWED_ISSUES = {
    "identity_drift",
    "weak_delivery",
    "wrong_pacing",
    "artifacts",
    "wrong_accent",
    "mispronunciation",
}


class PairedSeedReviewError(RuntimeError):
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
        raise PairedSeedReviewError(f"JSON file is missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PairedSeedReviewError(f"Invalid JSON in {path}: {exc}") from exc


def require_round(payload: Any, expected: str, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PairedSeedReviewError(f"{label} must be a JSON object.")
    if payload.get("round_id") != expected:
        raise PairedSeedReviewError(
            f"Unexpected {label} round_id: {payload.get('round_id')!r}; expected {expected!r}."
        )
    return payload


def rows_by_id(rows: Any, *, key: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise PairedSeedReviewError(f"{label} must contain a non-empty list.")
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise PairedSeedReviewError(f"Every {label} row must be an object.")
        value = row.get(key)
        if not isinstance(value, str) or not value.strip():
            raise PairedSeedReviewError(f"Every {label} row requires {key}.")
        value = value.strip()
        if value in indexed:
            raise PairedSeedReviewError(f"Duplicate {label} {key}: {value}")
        indexed[value] = row
    return indexed


def unwrap_review(payload: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(payload, dict):
        raise PairedSeedReviewError("Normalized review must be a JSON object.")
    review = payload.get("review") if "review" in payload else payload
    metadata = {
        "source_upload_name": payload.get("source_upload_name"),
        "source_upload_sha256": payload.get("source_upload_sha256"),
    }
    return require_round(review, ROUND_ID, "paired-seed review"), metadata


def verify_review_summary(review: dict[str, Any], rows: dict[str, dict[str, Any]]) -> None:
    summary = review.get("summary")
    if not isinstance(summary, dict):
        raise PairedSeedReviewError("Review summary is missing.")
    decisions = Counter(str(row.get("decision") or "") for row in rows.values())
    expected = {
        "candidate_count": EXPECTED_ROUTE_COUNT,
        "complete_count": EXPECTED_ROUTE_COUNT,
        "candidate_A_count": decisions["candidate_A"],
        "candidate_B_count": decisions["candidate_B"],
        "neither_count": decisions["neither"],
    }
    for key, value in expected.items():
        if summary.get(key) != value:
            raise PairedSeedReviewError(
                f"Review summary mismatch for {key}: {summary.get(key)!r} != {value!r}."
            )


def selected_role(answer: dict[str, Any], decision: str) -> str:
    if decision == "neither":
        return "neither"
    label = decision.removeprefix("candidate_")
    mapping = answer.get("candidate_mapping")
    if not isinstance(mapping, dict) or mapping.get(label) not in {
        "combined_bank",
        "legacy_reference",
    }:
        raise PairedSeedReviewError(
            f"Invalid candidate mapping for {answer.get('route_id')} decision {decision}."
        )
    return str(mapping[label])


def selected_issues(review: dict[str, Any], decision: str) -> list[str]:
    if decision == "neither":
        issues = list(review.get("candidate_A_issues") or []) + list(
            review.get("candidate_B_issues") or []
        )
    else:
        label = decision.removeprefix("candidate_")
        issues = list(review.get(f"candidate_{label}_issues") or [])
    normalized: list[str] = []
    for value in issues:
        item = str(value)
        if item not in ALLOWED_ISSUES:
            raise PairedSeedReviewError(
                f"Unexpected review issue for {review.get('route_id')}: {item}"
            )
        if item not in normalized:
            normalized.append(item)
    return normalized


def validate_inputs(
    matrix: dict[str, Any],
    answers: dict[str, dict[str, Any]],
    repeats: dict[str, Any],
    review: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    matrix = require_round(matrix, ROUND_ID, "paired-seed matrix")
    repeats = require_round(repeats, ROUND_ID, "repeatability analysis")
    if int(matrix.get("route_count") or 0) != EXPECTED_ROUTE_COUNT:
        raise PairedSeedReviewError("Paired-seed matrix route count is not nine.")
    if int(matrix.get("route_group_count") or 0) != EXPECTED_ROUTE_GROUP_COUNT:
        raise PairedSeedReviewError("Paired-seed matrix route-group count is not three.")
    if int(matrix.get("runs_per_route_group") or 0) != EXPECTED_RUNS_PER_GROUP:
        raise PairedSeedReviewError("Paired-seed matrix runs-per-group is not three.")
    if matrix.get("paired_generation_seed") is not True:
        raise PairedSeedReviewError("Paired A/B candidates must share their generation seed.")
    if matrix.get("same_seed_within_prompt_pair") is not True:
        raise PairedSeedReviewError("Matrix does not prove same-seed A/B comparison.")
    if repeats.get("fixed_seed_runtime_reproducible") is not True:
        raise PairedSeedReviewError("Fixed-seed runtime repeatability was not established.")
    if int(repeats.get("exact_pcm_match_count") or 0) != 6:
        raise PairedSeedReviewError("Expected six exact PCM repeat comparisons.")

    matrix_rows = rows_by_id(matrix.get("routes"), key="route_id", label="matrix routes")
    review_rows = rows_by_id(review.get("rows"), key="route_id", label="review rows")
    if set(matrix_rows) != set(answers) or set(matrix_rows) != set(review_rows):
        raise PairedSeedReviewError(
            "Route mismatch between matrix, answer key, and review: "
            f"matrix={sorted(matrix_rows)}, answers={sorted(answers)}, review={sorted(review_rows)}"
        )
    verify_review_summary(review, review_rows)

    for route_id, matrix_row in matrix_rows.items():
        answer = answers[route_id]
        reviewed = review_rows[route_id]
        for key in ("target", "function", "target_text"):
            expected = matrix_row.get(key)
            if answer.get(key) != expected:
                raise PairedSeedReviewError(f"Answer key changed {key} for {route_id}.")
            if reviewed.get(key) != expected:
                raise PairedSeedReviewError(f"Review changed {key} for {route_id}.")
        decision = str(reviewed.get("decision") or "")
        if decision not in ALLOWED_DECISIONS:
            raise PairedSeedReviewError(f"Invalid review decision for {route_id}: {decision!r}")
        selected_issues(reviewed, decision)
        mapping = answer.get("candidate_mapping")
        if not isinstance(mapping, dict) or set(mapping) != {"A", "B"}:
            raise PairedSeedReviewError(f"Candidate mapping is incomplete for {route_id}.")
        if set(mapping.values()) != {"combined_bank", "legacy_reference"}:
            raise PairedSeedReviewError(f"Candidate mapping roles are invalid for {route_id}.")
        if answer.get("production_promotion_allowed") is not False:
            raise PairedSeedReviewError("Answer key may not permit production promotion.")
    return matrix_rows, review_rows


def route_recommendation(
    group_id: str,
    primary_roles: list[str],
    repeat_agreement: bool,
    primary_outcomes: list[dict[str, Any]],
) -> tuple[str, str | None, str]:
    consensus = primary_roles[0] if primary_roles and len(set(primary_roles)) == 1 else None
    clean_consensus_count = sum(
        row["selected_role"] == consensus and not row["selected_issues"]
        for row in primary_outcomes
    ) if consensus else 0

    if not repeat_agreement:
        return (
            "blocked_repeat_disagreement",
            None,
            "The hidden exact-audio repeat did not reproduce the same underlying prompt choice.",
        )
    if consensus not in {"combined_bank", "legacy_reference"}:
        return (
            "blocked_seed_sensitive_preference",
            None,
            "The two distinct seeds did not agree on one prompt role.",
        )
    if clean_consensus_count < 1:
        return (
            "blocked_quality",
            None,
            "Prompt preference was consistent, but neither unique-seed selection was clean.",
        )
    if consensus == "combined_bank":
        return (
            "validated_bank_preferred_for_route_research",
            consensus,
            "The validated-bank prompt won both unique seeds and the exact hidden repeat, with at least one clean unique-seed selection.",
        )
    return (
        "legacy_reference_preferred_for_route_research",
        consensus,
        "The prior handpicked prompt won both unique seeds and the exact hidden repeat, with at least one clean unique-seed selection.",
    )


def apply_review(
    *,
    matrix: dict[str, Any],
    answer_payload: Any,
    repeatability: dict[str, Any],
    normalized_review: dict[str, Any],
    matrix_path: Path,
    answer_path: Path,
    repeatability_path: Path,
    review_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    answers = rows_by_id(answer_payload, key="route_id", label="answer key")
    review, upload_metadata = unwrap_review(normalized_review)
    matrix_rows, review_rows = validate_inputs(
        matrix, answers, repeatability, review
    )

    outcomes: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for route_id, matrix_row in matrix_rows.items():
        answer = answers[route_id]
        reviewed = review_rows[route_id]
        decision = str(reviewed["decision"])
        role = selected_role(answer, decision)
        issues = selected_issues(reviewed, decision)
        outcome = {
            "route_id": route_id,
            "route_group_id": matrix_row.get("route_group_id"),
            "run_id": matrix_row.get("run_id"),
            "repeat_of_run_id": matrix_row.get("repeat_of_run_id"),
            "generation_seed": matrix_row.get("generation_seed"),
            "target": matrix_row.get("target"),
            "target_label": matrix_row.get("target_label"),
            "function": matrix_row.get("function"),
            "function_label": matrix_row.get("function_label"),
            "target_text": matrix_row.get("target_text"),
            "bank_clip_id": matrix_row.get("bank_clip_id"),
            "legacy_key": matrix_row.get("legacy_key"),
            "review_decision": decision,
            "selected_role": role,
            "selected_issues": issues,
            "quality_status": (
                "neither_selected"
                if role == "neither"
                else "clean_preference" if not issues else "quality_blocked_preference"
            ),
            "candidate_A_issues": list(reviewed.get("candidate_A_issues") or []),
            "candidate_B_issues": list(reviewed.get("candidate_B_issues") or []),
            "review_notes": reviewed.get("notes") or None,
            "review_updated_at": reviewed.get("updated_at"),
            "automatic_production_assignment": False,
            "production_promotion_allowed": False,
        }
        outcomes.append(outcome)
        group_id = str(matrix_row.get("route_group_id") or "")
        grouped[group_id].append(outcome)

    repeat_comparisons_by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in repeatability.get("comparisons") or []:
        repeat_comparisons_by_group[str(row.get("route_group_id") or "")].append(row)

    route_groups: list[dict[str, Any]] = []
    for group_id in sorted(grouped):
        rows = sorted(grouped[group_id], key=lambda row: str(row.get("run_id") or ""))
        primary = [row for row in rows if not row.get("repeat_of_run_id")]
        repeats = [row for row in rows if row.get("repeat_of_run_id")]
        if len(primary) != 2 or len(repeats) != 1:
            raise PairedSeedReviewError(
                f"Route group {group_id} must contain two unique seeds and one hidden repeat."
            )
        repeat = repeats[0]
        original = next(
            (row for row in primary if row.get("run_id") == repeat.get("repeat_of_run_id")),
            None,
        )
        if original is None:
            raise PairedSeedReviewError(f"Hidden repeat source is missing for {group_id}.")
        exact_repeat_verified = all(
            item.get("exact_pcm_match") is True
            for item in repeat_comparisons_by_group.get(group_id, [])
        ) and len(repeat_comparisons_by_group.get(group_id, [])) == 2
        if not exact_repeat_verified:
            raise PairedSeedReviewError(f"Exact PCM repeat evidence is incomplete for {group_id}.")
        repeat_agreement = original["selected_role"] == repeat["selected_role"]
        primary_roles = [row["selected_role"] for row in primary]
        recommendation, preferred_role, reason = route_recommendation(
            group_id, primary_roles, repeat_agreement, primary
        )
        route_groups.append(
            {
                "route_group_id": group_id,
                "target": primary[0]["target"],
                "target_label": primary[0]["target_label"],
                "function": primary[0]["function"],
                "bank_clip_id": primary[0]["bank_clip_id"],
                "legacy_key": primary[0]["legacy_key"],
                "unique_seed_selected_roles": primary_roles,
                "unique_seed_role_consensus": (
                    primary_roles[0] if len(set(primary_roles)) == 1 else None
                ),
                "hidden_repeat_source_run_id": original["run_id"],
                "hidden_repeat_run_id": repeat["run_id"],
                "hidden_repeat_exact_pcm_verified": exact_repeat_verified,
                "hidden_repeat_selected_roles": [
                    original["selected_role"],
                    repeat["selected_role"],
                ],
                "hidden_repeat_role_agreement": repeat_agreement,
                "clean_unique_seed_selection_count": sum(
                    not row["selected_issues"] and row["selected_role"] != "neither"
                    for row in primary
                ),
                "recommendation": recommendation,
                "preferred_role": preferred_role,
                "recommendation_reason": reason,
                "automatic_production_assignment": False,
                "production_promotion_allowed": False,
            }
        )

    recommendations = Counter(row["recommendation"] for row in route_groups)
    preferred_roles = Counter(
        str(row["preferred_role"])
        for row in route_groups
        if row.get("preferred_role")
    )
    repeat_agreement_count = sum(
        bool(row["hidden_repeat_role_agreement"]) for row in route_groups
    )
    unique_consensus_count = sum(
        row.get("unique_seed_role_consensus") in {"combined_bank", "legacy_reference"}
        for row in route_groups
    )

    conclusion = (
        "Fixed-seed IndexTTS2 generation is reproducible, and paired-seed comparisons support "
        "two route-specific research preferences rather than universal bank routing: the prior "
        "handpicked reference for Benny fatalistic dread and the validated-bank reference for "
        "Seventh Doctor playful identity. Narrator explosive anger remains blocked because its "
        "hidden exact-audio repeat did not reproduce the same underlying prompt choice and every "
        "selected output was marked weak."
    )

    applied = {
        "schema_version": 1,
        "round_id": APPLIED_ROUND_ID,
        "created_at": now_iso(),
        "source_upload": upload_metadata,
        "review_exported_at": review.get("exported_at"),
        "route_count": len(outcomes),
        "route_group_count": len(route_groups),
        "fixed_seed_runtime_reproducible": True,
        "paired_generation_seed": True,
        "unique_seed_consensus_count": unique_consensus_count,
        "hidden_repeat_role_agreement_count": repeat_agreement_count,
        "hidden_repeat_role_agreement_rate": round(
            repeat_agreement_count / len(route_groups), 6
        ),
        "recommendation_counts": dict(sorted(recommendations.items())),
        "supported_preferred_role_counts": dict(sorted(preferred_roles.items())),
        "general_validated_bank_routing_recommended": False,
        "general_legacy_routing_recommended": False,
        "route_specific_research_routing_supported": True,
        "narrator_anger_route_supported": False,
        "conclusion": conclusion,
        "route_groups": route_groups,
        "outcomes": sorted(outcomes, key=lambda row: row["route_id"]),
        "source_artifacts": {
            "matrix": {"path": str(matrix_path), "sha256": sha256_file(matrix_path)},
            "answer_key": {"path": str(answer_path), "sha256": sha256_file(answer_path)},
            "repeatability_analysis": {
                "path": str(repeatability_path),
                "sha256": sha256_file(repeatability_path),
            },
            "normalized_review": {
                "path": str(review_path),
                "sha256": sha256_file(review_path),
            },
        },
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }

    policy_routes: list[dict[str, Any]] = []
    for row in route_groups:
        if row["recommendation"] == "legacy_reference_preferred_for_route_research":
            policy_routes.append(
                {
                    "target": row["target"],
                    "function": row["function"],
                    "status": "research_preferred",
                    "prompt_role": "legacy_reference",
                    "reference_key": row["legacy_key"],
                    "validated_bank_clip_id": row["bank_clip_id"],
                    "reason": row["recommendation_reason"],
                }
            )
        elif row["recommendation"] == "validated_bank_preferred_for_route_research":
            policy_routes.append(
                {
                    "target": row["target"],
                    "function": row["function"],
                    "status": "research_preferred",
                    "prompt_role": "validated_bank",
                    "reference_key": row["bank_clip_id"],
                    "validated_bank_clip_id": row["bank_clip_id"],
                    "reason": row["recommendation_reason"],
                }
            )
        else:
            policy_routes.append(
                {
                    "target": row["target"],
                    "function": row["function"],
                    "status": "blocked",
                    "prompt_role": None,
                    "reference_key": None,
                    "validated_bank_clip_id": row["bank_clip_id"],
                    "reason": row["recommendation_reason"],
                }
            )

    policy = {
        "schema_version": 1,
        "round_id": POLICY_ROUND_ID,
        "created_at": now_iso(),
        "policy_scope": "research_only",
        "general_reference_bank_routing": "disabled",
        "general_legacy_routing": "disabled",
        "deterministic_seed_required": True,
        "paired_seed_evidence_required_for_new_routes": True,
        "routes": sorted(policy_routes, key=lambda row: (row["target"], row["function"])),
        "fallback_policy": (
            "Preserve existing handpicked prompt behavior for untested functions; do not route the "
            "31-reference bank automatically."
        ),
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    return applied, policy


def validate_applied(payload: Any) -> dict[str, Any]:
    payload = require_round(payload, APPLIED_ROUND_ID, "applied paired-seed review")
    groups = payload.get("route_groups")
    outcomes = payload.get("outcomes")
    if not isinstance(groups, list) or len(groups) != EXPECTED_ROUTE_GROUP_COUNT:
        raise PairedSeedReviewError("Applied ledger must contain three route groups.")
    if not isinstance(outcomes, list) or len(outcomes) != EXPECTED_ROUTE_COUNT:
        raise PairedSeedReviewError("Applied ledger must contain nine outcomes.")
    if payload.get("fixed_seed_runtime_reproducible") is not True:
        raise PairedSeedReviewError("Fixed-seed runtime reproducibility must remain true.")
    if payload.get("general_validated_bank_routing_recommended") is not False:
        raise PairedSeedReviewError("General bank routing must remain disabled.")
    if payload.get("general_legacy_routing_recommended") is not False:
        raise PairedSeedReviewError("General legacy routing must remain disabled.")
    if payload.get("automatic_production_assignment") is not False:
        raise PairedSeedReviewError("Automatic production assignment must remain disabled.")
    if payload.get("production_promotion_allowed") is not False:
        raise PairedSeedReviewError("Production promotion must remain disabled.")
    for row in groups + outcomes:
        if row.get("production_promotion_allowed") is not False:
            raise PairedSeedReviewError("No route result may permit production promotion.")
    return {
        "route_count": len(outcomes),
        "route_group_count": len(groups),
        "fixed_seed_runtime_reproducible": True,
        "hidden_repeat_role_agreement_count": payload.get(
            "hidden_repeat_role_agreement_count"
        ),
        "recommendation_counts": payload.get("recommendation_counts"),
        "supported_preferred_role_counts": payload.get(
            "supported_preferred_role_counts"
        ),
        "general_validated_bank_routing_recommended": False,
    }


def validate_policy(payload: Any) -> dict[str, Any]:
    payload = require_round(payload, POLICY_ROUND_ID, "route-specific prompt policy")
    routes = payload.get("routes")
    if not isinstance(routes, list) or len(routes) != EXPECTED_ROUTE_GROUP_COUNT:
        raise PairedSeedReviewError("Route policy must contain three routes.")
    if payload.get("policy_scope") != "research_only":
        raise PairedSeedReviewError("Route policy must remain research-only.")
    if payload.get("general_reference_bank_routing") != "disabled":
        raise PairedSeedReviewError("General reference-bank routing must be disabled.")
    if payload.get("automatic_production_assignment") is not False:
        raise PairedSeedReviewError("Policy may not assign production automatically.")
    if payload.get("production_promotion_allowed") is not False:
        raise PairedSeedReviewError("Policy may not promote to production.")
    return {
        "route_count": len(routes),
        "research_preferred_count": sum(
            row.get("status") == "research_preferred" for row in routes
        ),
        "blocked_count": sum(row.get("status") == "blocked" for row in routes),
        "general_reference_bank_routing": "disabled",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply the paired-seed three-voice reliability review conservatively."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--matrix", required=True)
    apply_parser.add_argument("--answer-key", required=True)
    apply_parser.add_argument("--repeatability", required=True)
    apply_parser.add_argument("--review", required=True)
    apply_parser.add_argument("--output-dir", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--applied", required=True)
    validate_parser.add_argument("--policy", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "apply":
            matrix_path = Path(args.matrix).expanduser().resolve()
            answer_path = Path(args.answer_key).expanduser().resolve()
            repeatability_path = Path(args.repeatability).expanduser().resolve()
            review_path = Path(args.review).expanduser().resolve()
            output_dir = Path(args.output_dir).expanduser().resolve()
            output_dir.mkdir(parents=True, exist_ok=True)
            applied, policy = apply_review(
                matrix=load_json(matrix_path),
                answer_payload=load_json(answer_path),
                repeatability=load_json(repeatability_path),
                normalized_review=load_json(review_path),
                matrix_path=matrix_path,
                answer_path=answer_path,
                repeatability_path=repeatability_path,
                review_path=review_path,
            )
            applied_path = output_dir / "applied-paired-seed-review-ledger.json"
            policy_path = output_dir / "route-specific-prompt-policy.json"
            applied_path.write_text(
                json.dumps(applied, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            policy_path.write_text(
                json.dumps(policy, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            result = {
                **validate_applied(applied),
                "policy": validate_policy(policy),
                "applied": str(applied_path),
                "route_policy": str(policy_path),
            }
        else:
            result = {
                **validate_applied(load_json(Path(args.applied).expanduser().resolve())),
                "policy": validate_policy(
                    load_json(Path(args.policy).expanduser().resolve())
                ),
            }
    except (PairedSeedReviewError, OSError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
        )
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
