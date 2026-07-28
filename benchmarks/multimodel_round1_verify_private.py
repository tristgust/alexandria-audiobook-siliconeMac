"""Verify private Round 1 keys and source/public audio equivalence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from multimodel_round1_chatterbox_cache_policy import (
    legacy_cache_revalidation_status,
)
from multimodel_round1_paths import (
    SafeIdentifier,
)
from multimodel_round1_review_output import (
    EXPECTED_REVALIDATION_COUNT,
    EXPECTED_ROUND1_SAMPLE_COUNT,
)
from multimodel_round1_verify_contract import (
    PublicVerification,
    VerificationState,
    add_issue,
    read_json,
    read_json_rows,
)
from multimodel_round1_verify_private_audio import PrivateAudioVerifier


def _model_counts(
    state: VerificationState,
) -> tuple[dict[str, int], dict[str, int]]:
    anomalies = {
        entry["sample_id"]: entry for entry in state.anomaly_manifest["entries"]
    }
    generated: dict[str, int] = {}
    eligible: dict[str, int] = {}
    for model in state.internal["model_contract"]["models"]:
        key = str(model["key"])
        matching = [
            sample
            for sample in state.internal["sample_specs"]
            if sample["model_key"] == key
        ]
        generated[key] = sum(
            sample["sample_id"] in state.generated for sample in matching
        )
        eligible[key] = sum(
            sample["sample_id"] in state.generated
            and (
                sample["sample_id"] not in anomalies
                or anomalies[sample["sample_id"]]["review_eligible"]
            )
            for sample in matching
        )
    return generated, eligible


def verify_answer_keys(
    answer_root: Path,
    state: VerificationState,
    public: PublicVerification,
) -> None:
    rows: list[dict[str, Any]] = []
    for group in state.internal["groups"]:
        group_id = SafeIdentifier(str(group))
        try:
            rows.extend(read_json_rows(answer_root, f"{group_id}.json"))
        except (OSError, json.JSONDecodeError):
            add_issue(state.issues, "invalid_answer_key", str(group))
    by_source = {row.get("source_sample_id"): row for row in rows}
    expected_ids = {
        sample["sample_id"] for sample in state.internal["sample_specs"]
    }
    if set(by_source) != expected_ids or len(by_source) != len(rows):
        add_issue(state.issues, "answer_key_ids", "all")
    try:
        private_manifest = read_json(answer_root, "manifest.json")
    except (OSError, json.JSONDecodeError):
        add_issue(state.issues, "invalid_answer_manifest", str(answer_root))
        return
    generated_counts, eligible_counts = _model_counts(state)
    if private_manifest.get("generated_counts_by_model") != generated_counts:
        add_issue(state.issues, "private_model_counts_stale", "generated_counts_by_model")
    if (
        private_manifest.get("review_eligible_counts_by_model") != eligible_counts
        or private_manifest.get("long_output_anomaly_count")
        != state.anomaly_manifest["over_30_seconds_count"]
        or private_manifest.get("ceiling_hit_count")
        != state.anomaly_manifest["ceiling_hit_count"]
    ):
        add_issue(state.issues, "private_eligibility_counts_stale", "review_eligibility")
    revalidation_count = sum(
        legacy_cache_revalidation_status(
            state.receipts.get(str(sample["sample_id"]), {}).get(
                "conditionals_cache_hit"
            )
        )
        == "requires_revalidation"
        for sample in state.internal["sample_specs"]
    )
    expected_cache_counts = {
        "requires_revalidation": revalidation_count,
        "not_flagged": len(state.internal["sample_specs"]) - revalidation_count,
    }
    if (
        private_manifest.get("requires_revalidation_count") != revalidation_count
        or private_manifest.get("cache_revalidation_counts")
        != expected_cache_counts
        or (
            len(state.internal["sample_specs"]) == EXPECTED_ROUND1_SAMPLE_COUNT
            and revalidation_count != EXPECTED_REVALIDATION_COUNT
        )
    ):
        add_issue(state.issues, "private_cache_revalidation", "manifest")
    public_rows = {
        str(row.get("sample_id")): row
        for row in (public.data.get("samples") or [])
    }
    verifier = PrivateAudioVerifier(state, public, public_rows)
    for sample in state.internal["sample_specs"]:
        row = by_source.get(sample["sample_id"])
        if row is not None:
            verifier.candidate(sample, row)
    verifier.references(private_manifest)
