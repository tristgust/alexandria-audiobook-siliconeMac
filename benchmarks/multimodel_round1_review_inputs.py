"""Read and validate private inputs used by the Round 1 review packager."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from multimodel_round1_paths import (
    ContainedPath,
    SafeIdentifier,
    contained_path,
    parse_artifact_paths,
    safe_file_stat,
    safe_read_text,
)
from multimodel_round1_receipts import validate_round1_generation_pair
from multimodel_round1_review_audio import PublishedAudio
from multimodel_round1_runtime import GenerationIntegrityError


def _read_json(target: ContainedPath) -> dict[str, Any]:
    return json.loads(safe_read_text(target))


def _exists(target: ContainedPath) -> bool:
    try:
        safe_file_stat(target)
    except FileNotFoundError:
        return False
    return True


def validate_receipt(
    evidence: Path,
    sample: dict[str, Any],
    model: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    artifacts = parse_artifact_paths(
        evidence,
        str(sample["output_file"]),
        str(sample["result_file"]),
    )
    try:
        return validate_round1_generation_pair(evidence, sample, model)
    except GenerationIntegrityError as exc:
        both_missing = not _exists(artifacts.output) and not _exists(artifacts.result)
        if exc.code == "generation_pair_missing" and both_missing:
            return None, None
        raise


def objective_measurements(
    evidence: Path,
    internal: dict[str, Any],
) -> dict[str, Any]:
    measurements: dict[str, Any] = {}
    for group in internal["groups"]:
        group_id = SafeIdentifier(str(group))
        target = contained_path(evidence, f"objective/{group_id}.json")
        try:
            payload = _read_json(target)
        except (FileNotFoundError, json.JSONDecodeError):
            continue
        measurements.update(payload.get("measurements") or {})
    return measurements


def native_aliases(internal: dict[str, Any]) -> dict[str, tuple[str, str]]:
    keys = sorted(
        {
            str(sample["identity_key"])
            for sample in internal["sample_specs"]
            if str(sample["identity_key"]).startswith("native_")
        }
    )
    return {
        key: (f"native_voice_{index:02d}", f"Native voice {index:02d}")
        for index, key in enumerate(keys, start=1)
    }


def reference_record(
    source_file: str,
    publication: PublishedAudio,
) -> dict[str, str]:
    return {
        "source_file": source_file,
        "source_sha256": publication.source_sha256,
        "public_file": publication.relative_path,
        "public_sha256": publication.public_sha256,
        "source_decoded_sha256": publication.source_decoded_sha256,
        "public_decoded_sha256": publication.public_decoded_sha256,
    }
