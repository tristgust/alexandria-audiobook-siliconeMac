"""Compose and safely write Round 1 public/private review documents."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Final

from multimodel_round1_handoff import Round1HandoffPaths
from multimodel_round1_paths import (
    SafeIdentifier,
    contained_path,
    safe_atomic_copy,
    safe_atomic_write_text,
)


ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
DATA_PREFIX: Final = "window.ALEXANDRIA_ROUND1_DATA = "
EXPECTED_ROUND1_SAMPLE_COUNT: Final = 1182
EXPECTED_REVALIDATION_COUNT: Final = 175
REVIEW_ASSET_FILES: Final = (
    "index.html",
    "styles.css",
    "review-core.js",
    "review-content.js",
    "review-navigation.js",
    "review-io.js",
    "app.js",
)
REVIEW_FIELDS: Final = (
    "identity_1_to_5",
    "delivery_1_to_5",
    "naturalness_1_to_5",
    "artifact_severity_1_to_5",
    "spoken_text_matches_expected",
    "requested_mode_is_clear",
    "approve_for_comparison",
    "flag_for_follow_up",
    "notes",
)


class ReviewOutputError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReviewPackageBuild:
    internal: dict[str, Any]
    public_identities: dict[str, Any]
    public_samples: list[dict[str, Any]]
    answer_keys: dict[str, list[dict[str, Any]]]
    native_aliases: dict[str, tuple[str, str]]
    group_counts: dict[str, int]
    structural_group_counts: dict[str, int]
    model_counts: dict[str, int]
    review_model_counts: dict[str, int]
    anomaly_manifest: dict[str, Any]
    reference_publications: list[dict[str, Any]]


def _write_json(handoff: Round1HandoffPaths, relative: str, value: Any) -> None:
    payload = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    safe_atomic_write_text(contained_path(handoff.evidence_root, relative), payload)


def _blocked_rows(build: ReviewPackageBuild) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in build.internal["blocked_cells"]:
        identity = str(item["identity_key"])
        if identity.startswith("native_"):
            identity = build.native_aliases.get(
                identity,
                ("native_voice_unavailable", ""),
            )[0]
        rows.append(
            {
                "group": item["group"],
                "style": item["style"],
                "identity_key": identity,
                "status": "blocked",
            }
        )
    return rows


def _public_documents(
    build: ReviewPackageBuild,
) -> tuple[dict[str, Any], dict[str, Any]]:
    internal = build.internal
    blocked = _blocked_rows(build)
    styles = [
        {
            key: row[key]
            for key in ("key", "label", "group", "target_text", "instruction")
        }
        for row in internal["styles"]
    ]
    public = {
        "schema_version": 1,
        "round_id": internal["round_id"],
        "title": "Alexandria multimodel expressive-clone blind review — Round 1",
        "groups": internal["groups"],
        "styles": styles,
        "identities": build.public_identities,
        "samples": build.public_samples,
        "blocked_coverage": blocked,
        "generated_counts": build.group_counts,
        "structurally_generated_counts": build.structural_group_counts,
        "long_output_anomaly_count": build.anomaly_manifest["over_30_seconds_count"],
        "ceiling_hit_count": build.anomaly_manifest["ceiling_hit_count"],
        "review_fields": list(REVIEW_FIELDS),
        "cumulative_partial_exports_supported": True,
        "production_promotion_allowed": False,
    }
    manifest = {
        "schema_version": 1,
        "round_id": internal["round_id"],
        "review": "index.html",
        "group_count": len(internal["groups"]),
        "style_count": len(internal["styles"]),
        "generated_sample_count": sum(build.group_counts.values()),
        "review_eligible_sample_count": sum(build.group_counts.values()),
        "structurally_generated_sample_count": sum(build.model_counts.values()),
        "diagnostic_hold_sample_count": build.anomaly_manifest["over_30_seconds_count"],
        "long_output_anomaly_count": build.anomaly_manifest["over_30_seconds_count"],
        "ceiling_hit_count": build.anomaly_manifest["ceiling_hit_count"],
        "sample_spec_count": len(build.public_samples),
        "blocked_coverage_count": len(blocked),
        "generated_counts": build.group_counts,
        "structurally_generated_counts": build.structural_group_counts,
        "all_audio_copied": True,
        "symlinks_used": False,
        "group_exports": True,
        "style_exports": True,
        "cumulative_export": True,
        "partial_import_merge": True,
        "models_mixed_within_identity_and_style": True,
        "native_voices_pooled_across_models": True,
        "candidate_order_changes_by_style": True,
        "automatic_round2_prep_from_scores": True,
        "optional_follow_up_flag": True,
        "answer_keys_separate": True,
        "production_promotion_allowed": False,
    }
    return public, manifest


def write_review_package(
    handoff: Round1HandoffPaths,
    build: ReviewPackageBuild,
) -> dict[str, Any]:
    answer_prefix = handoff.answer_key_root.name
    public_prefix = handoff.public_root.name
    all_answer_rows = [
        row for rows in build.answer_keys.values() for row in rows
    ]
    revalidation_count = sum(
        row["cache_revalidation_status"] == "requires_revalidation"
        for row in all_answer_rows
    )
    if (
        len(build.public_samples) == EXPECTED_ROUND1_SAMPLE_COUNT
        and revalidation_count != EXPECTED_REVALIDATION_COUNT
    ):
        raise ReviewOutputError("private cache revalidation count is not 175")
    for group, rows in build.answer_keys.items():
        group_id = SafeIdentifier(str(group))
        _write_json(handoff, f"{answer_prefix}/{group_id}.json", rows)
    private_manifest = {
        "round_id": build.internal["round_id"],
        "generated_counts_by_model": build.model_counts,
        "review_eligible_counts_by_model": build.review_model_counts,
        "long_output_anomaly_count": build.anomaly_manifest["over_30_seconds_count"],
        "ceiling_hit_count": build.anomaly_manifest["ceiling_hit_count"],
        "requires_revalidation_count": revalidation_count,
        "cache_revalidation_counts": {
            "requires_revalidation": revalidation_count,
            "not_flagged": len(all_answer_rows) - revalidation_count,
        },
        "reference_audio_publications": build.reference_publications,
    }
    _write_json(handoff, f"{answer_prefix}/manifest.json", private_manifest)
    public, manifest = _public_documents(build)
    data = DATA_PREFIX + json.dumps(public, ensure_ascii=False) + ";\n"
    safe_atomic_write_text(
        contained_path(handoff.evidence_root, f"{public_prefix}/data.js"),
        data,
    )
    _write_json(handoff, f"{public_prefix}/manifest.json", manifest)
    for filename in REVIEW_ASSET_FILES:
        safe_atomic_copy(
            contained_path(ROOT, f"benchmarks/multimodel_review_assets/{filename}"),
            contained_path(handoff.evidence_root, f"{public_prefix}/{filename}"),
        )
    return {
        "output": str(handoff.public_root),
        "answer_keys": str(handoff.answer_key_root),
        **manifest,
        "generated_counts_by_model": build.model_counts,
        "requires_revalidation_count": revalidation_count,
    }
