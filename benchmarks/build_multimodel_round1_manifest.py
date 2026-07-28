#!/usr/bin/env python3
"""Build the complete Round 1 multimodel/identity/style manifest.

The output includes every requested coverage cell. Technically valid cells become
resumable sample specifications; unsupported cells remain explicit with a reason.
Model names and actual controls stay in this internal manifest/answer key and are
removed from the public listening manifest.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from multimodel_blind_round1_contract import ROUND_ID, STYLE_GROUPS, STYLES
from multimodel_round1_manifest_contract import (
    ManifestContractError,
    acted_reference_for_style,
    control_for,
    generation_failure_for,
    known_identity_lanes,
    sha256_text,
    stable_id,
    support_for,
)
from multimodel_round1_runtime import atomic_write_json

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = ROOT / ".omo" / "evidence" / "b17-t05-multimodel-round1"
DEFAULT_MODELS = ROOT / "benchmarks" / "multimodel_round1_models.json"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--models", default=str(DEFAULT_MODELS))
    args = parser.parse_args()

    evidence_root = Path(args.evidence_root).expanduser().resolve()
    references_root = evidence_root / "references"
    model_contract = read_json(Path(args.models).expanduser().resolve())
    references = read_json(references_root / "manifest.json")
    ryan = read_json(references_root / "ryan" / "manifest.json")
    native_manifest_path = references_root / "native" / "manifest.json"
    native_manifest = (
        read_json(native_manifest_path)
        if native_manifest_path.is_file()
        else {"records": []}
    )
    native_anchor_by_key = {
        item["identity_key"]: item for item in native_manifest.get("records", [])
    }
    identities = known_identity_lanes(references, ryan)

    models = list(model_contract["models"])
    sample_specs: list[dict[str, Any]] = []
    blocked_cells: list[dict[str, Any]] = []
    native_lanes: dict[str, dict[str, Any]] = {}

    for model in models:
        native = model.get("native_lane")
        model_identities = list(identities)
        if native:
            anchor = native_anchor_by_key.get(native["identity_key"])
            native_record = {
                **native,
                "model_key": model["key"],
                "style_specific": False,
                "reference_status": (
                    "ready" if anchor is not None else "pending_native_anchor"
                ),
            }
            if anchor is not None:
                native_record.update(
                    {
                        "source_file": "native/" + anchor["audio_file"],
                        "source_sha256": anchor["audio_sha256"],
                        "conditioning_file": "native/" + anchor["audio_file"],
                        "conditioning_sha256": anchor["audio_sha256"],
                        "conditioning_transcript": anchor["transcript"],
                        "conditioning_transcript_sha256": anchor[
                            "transcript_sha256"
                        ],
                        "reference_manifest": "references/native/manifest.json",
                    }
                )
            native_lanes[native["identity_key"]] = native_record
            model_identities.append(native["identity_key"])

        for identity_key in model_identities:
            for style in STYLES:
                supported, reason = support_for(model, identity_key, style["key"])
                cell_id = stable_id(ROUND_ID, model["key"], identity_key, style["key"])
                if not supported:
                    blocked_cells.append(
                        {
                            "cell_id": cell_id,
                            "model_key": model["key"],
                            "identity_key": identity_key,
                            "style": style["key"],
                            "group": style["group"],
                            "reason": reason,
                        }
                    )
                    continue

                if identity_key in identities:
                    reference = dict(identities[identity_key])
                    if identity_key == "ryan_acted":
                        reference.update(acted_reference_for_style(ryan, style["key"]))
                else:
                    reference = dict(native_lanes[identity_key])

                acted = acted_reference_for_style(ryan, style["key"])
                reference["acted_emotion_reference_file"] = acted["conditioning_file"]
                reference["acted_emotion_reference_sha256"] = acted[
                    "conditioning_sha256"
                ]
                sample_id = f"r1_{cell_id}"
                blind_id = stable_id("blind", ROUND_ID, cell_id, length=16)
                output_rel = (
                    Path("outputs")
                    / model["key"]
                    / identity_key
                    / style["key"]
                    / f"{sample_id}.wav"
                )
                result_rel = output_rel.with_suffix(".json")
                generation_failure = generation_failure_for(
                    model["key"], identity_key, style["key"]
                )
                if generation_failure is not None:
                    status = "generation_failed_safety_quarantine"
                elif (
                    identity_key not in identities
                    and reference.get("reference_status") != "ready"
                ):
                    status = "pending_native_anchor"
                else:
                    status = "pending_generation"
                sample_spec = {
                        "sample_id": sample_id,
                        "blind_id": blind_id,
                        "model_key": model["key"],
                        "model_label": model["label"],
                        "identity_key": identity_key,
                        "identity_review_name": reference["review_name"],
                        "identity_kind": reference["kind"],
                        "style": style["key"],
                        "style_label": style["label"],
                        "group": style["group"],
                        "target_text": style["target_text"],
                        "target_text_sha256": sha256_text(style["target_text"]),
                        "reference": reference,
                        "control": control_for(model, identity_key, style, reference),
                        "seed": 6200 + len(sample_specs),
                        "output_file": str(output_rel),
                        "result_file": str(result_rel),
                        "status": status,
                        "production_promotion_allowed": False,
                    }
                if generation_failure is not None:
                    sample_spec["generation_failure"] = generation_failure
                sample_specs.append(sample_spec)

    expected_cells = len(models) * len(STYLES) * len(identities) + sum(
        len(STYLES) for model in models if model.get("native_lane")
    )
    if expected_cells != len(sample_specs) + len(blocked_cells):
        raise ManifestContractError("Coverage accounting mismatch.")

    manifest = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "purpose": "large_scale_cumulative_multimodel_expressive_clone_blind_round1",
        "groups": STYLE_GROUPS,
        "styles": list(STYLES),
        "model_contract": model_contract,
        "identity_lanes": identities,
        "native_lanes": native_lanes,
        "expected_coverage_cell_count": expected_cells,
        "sample_spec_count": len(sample_specs),
        "blocked_cell_count": len(blocked_cells),
        "generation_failure_count": sum(
            sample["status"] == "generation_failed_safety_quarantine"
            for sample in sample_specs
        ),
        "sample_specs": sample_specs,
        "blocked_cells": blocked_cells,
        "review_contract": {
            "single_application": True,
            "group_export": True,
            "style_export": True,
            "cumulative_export": True,
            "partial_import_merge": True,
            "stable_sample_ids": True,
            "round2_cumulative_prep": True,
            "model_identity_hidden": True,
            "expected_identity_visible": True,
            "identity_source_and_conditioning_audio_available": True,
        },
        "manual_blinded_review_required": True,
        "production_promotion_allowed": False,
        "production_registry_changed": False,
        "voice_assignment_changed": False,
        "live_project_audio_changed": False,
    }
    evidence_root.mkdir(parents=True, exist_ok=True)
    output = evidence_root / "round1_internal_manifest.json"
    atomic_write_json(output, manifest)
    print(
        json.dumps(
            {
                "output": str(output),
                "style_count": len(STYLES),
                "model_count": len(models),
                "base_identity_count": len(identities),
                "native_lane_count": len(native_lanes),
                "expected_cells": expected_cells,
                "sample_specs": len(sample_specs),
                "blocked_cells": len(blocked_cells),
                "generation_failures": manifest["generation_failure_count"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
