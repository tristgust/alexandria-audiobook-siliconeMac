#!/usr/bin/env python3
"""Reproducibly verify Round 1 sources, private keys, and blind package."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from multimodel_round1_handoff import resolve_round1_handoff_paths
from multimodel_round1_paths import (
    SafeRelativePath,
    contained_path,
    parse_artifact_paths,
    safe_file_stat,
    safe_read_text,
)
from multimodel_round1_receipts import validate_round1_generation_pair
from multimodel_round1_review_eligibility import (
    ANOMALY_RELATIVE_PATH,
    build_moss_long_output_manifest,
)
from multimodel_round1_runtime import (
    GenerationIntegrityError,
    ReferenceIntegrityError,
    sha256_text,
    validate_sample_references,
)
from multimodel_round1_verify_contract import (
    VerificationState,
    add_issue,
    read_json,
    relative_file_tree,
)
from multimodel_round1_verify_private import verify_answer_keys
from multimodel_round1_verify_public import verify_public


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = ROOT / ".omo/evidence/b17-t05-multimodel-round1"


def _read_internal(evidence: Path) -> dict[str, Any]:
    raw = safe_read_text(contained_path(evidence, "round1_internal_manifest.json"))
    return json.loads(raw)


def _exists_pair(state: VerificationState, sample: dict[str, Any]) -> bool:
    artifacts = parse_artifact_paths(
        state.evidence,
        str(sample["output_file"]),
        str(sample["result_file"]),
    )
    present = []
    for target in (artifacts.output, artifacts.result):
        try:
            safe_file_stat(target)
        except FileNotFoundError:
            present.append(False)
        else:
            present.append(True)
    return any(present)


def _verify_receipt(
    state: VerificationState,
    sample: dict[str, Any],
    model: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return validate_round1_generation_pair(state.evidence, sample, model)
    except ReferenceIntegrityError:
        add_issue(state.issues, "generation_path_invalid", str(sample["sample_id"]))
    except GenerationIntegrityError as exc:
        if exc.code == "generation_pair_missing" and not _exists_pair(state, sample):
            return None, None
        add_issue(state.issues, exc.code, exc.subject)
    return None, None


def _verify_generation_sources(
    state: VerificationState,
    models: dict[str, dict[str, Any]],
) -> None:
    expected_files: set[str] = set()
    for sample in state.internal["sample_specs"]:
        output = str(SafeRelativePath(str(sample["output_file"])))
        result = str(SafeRelativePath(str(sample["result_file"])))
        expected_files.update((output, result))
        sample_id = str(sample["sample_id"])
        if sha256_text(sample["target_text"]) != sample["target_text_sha256"]:
            add_issue(state.issues, "manifest_target_text_hash", sample_id)
        model = models.get(str(sample["model_key"]))
        if model is None:
            add_issue(state.issues, "unknown_model_key", sample_id)
            continue
        try:
            validate_sample_references(state.evidence, sample)
        except ReferenceIntegrityError:
            add_issue(state.issues, "reference_integrity", sample_id)
        receipt, audio_sha = _verify_receipt(state, sample, model)
        if receipt and audio_sha:
            state.generated[sample_id] = audio_sha
            state.fingerprints[sample_id] = str(receipt["sample_fingerprint"])
            state.receipts[sample_id] = receipt
    try:
        actual_files = {
            f"outputs/{relative}"
            for relative in relative_file_tree(state.evidence, "outputs")
            if Path(relative).suffix in {".wav", ".json"}
        }
    except OSError:
        add_issue(state.issues, "unsafe_generation_directory", "outputs")
        return
    for extra in sorted(actual_files - expected_files):
        add_issue(state.issues, "extra_generation_artifact", extra)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--review-root")
    parser.add_argument("--answer-key-root")
    args = parser.parse_args()
    handoff = resolve_round1_handoff_paths(
        Path(args.evidence_root),
        public_root=Path(args.review_root) if args.review_root else None,
        answer_key_root=(
            Path(args.answer_key_root) if args.answer_key_root else None
        ),
    )
    internal = _read_internal(handoff.evidence_root)
    models = {
        str(item["key"]): item for item in internal["model_contract"]["models"]
    }
    issues: list[dict[str, str]] = []
    generated: dict[str, str] = {}
    fingerprints: dict[str, str] = {}
    receipts: dict[str, dict[str, Any]] = {}
    anomaly_manifest = build_moss_long_output_manifest(
        handoff.evidence_root,
        internal,
    )
    try:
        recorded = read_json(
            handoff.evidence_root,
            ANOMALY_RELATIVE_PATH.as_posix(),
        )
    except (OSError, json.JSONDecodeError):
        add_issue(issues, "missing_anomaly_manifest", str(ANOMALY_RELATIVE_PATH))
    else:
        if recorded != anomaly_manifest:
            add_issue(issues, "stale_anomaly_manifest", str(ANOMALY_RELATIVE_PATH))
    state = VerificationState(
        handoff.evidence_root,
        internal,
        generated,
        fingerprints,
        receipts,
        anomaly_manifest,
        issues,
    )
    _verify_generation_sources(state, models)
    public = verify_public(handoff.public_root, state)
    verify_answer_keys(handoff.answer_key_root, state, public)
    anomalies = {
        entry["sample_id"]: entry for entry in anomaly_manifest["entries"]
    }
    eligible_count = sum(
        sample_id not in anomalies or anomalies[sample_id]["review_eligible"]
        for sample_id in generated
    )
    result = {
        "ok": not issues,
        "error_count": len(issues),
        "generated_sample_count": len(generated),
        "structurally_generated_sample_count": len(generated),
        "review_eligible_sample_count": eligible_count,
        "long_output_anomaly_count": anomaly_manifest["over_30_seconds_count"],
        "ceiling_hit_count": anomaly_manifest["ceiling_hit_count"],
        "errors": sorted(issues, key=lambda item: (item["code"], item["subject"])),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
