#!/usr/bin/env python3
"""Build the complete Round 1 multimodel/identity/style manifest.

The output includes every requested coverage cell. Technically valid cells become
resumable sample specifications; unsupported cells remain explicit with a reason.
Model names and actual controls stay in this internal manifest/answer key and are
removed from the public listening manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from multimodel_blind_round1_contract import ROUND_ID, STYLE_GROUPS, STYLES

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = ROOT / ".omo" / "evidence" / "b17-t05-multimodel-round1"
DEFAULT_MODELS = ROOT / "benchmarks" / "multimodel_round1_models.json"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(*parts: str, length: int = 20) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:length]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def known_identity_lanes(reference_manifest: dict[str, Any], ryan: dict[str, Any]) -> dict[str, Any]:
    lanes: dict[str, Any] = {}
    for item in reference_manifest["identities"]:
        lanes[item["identity_key"]] = {
            "identity_key": item["identity_key"],
            "review_name": item["label"],
            "kind": "supplied_recording_clone",
            "source_file": item["source_file"],
            "source_sha256": item["source_sha256"],
            "conditioning_file": item["conditioning_file"],
            "conditioning_sha256": item["conditioning_sha256"],
            "conditioning_transcript": item["conditioning_transcript"],
            "conditioning_transcript_sha256": item[
                "conditioning_transcript_sha256"
            ],
            "reference_manifest": f"references/{item['identity_key']}/reference.json",
        }
    lanes["ryan_neutral"] = {
        "identity_key": "ryan_neutral",
        "review_name": "Ryan — neutral anchor",
        "kind": "fixed_neutral_clone_reference",
        "source_file": "ryan/" + ryan["neutral"]["audio_file"],
        "source_sha256": ryan["neutral"]["audio_sha256"],
        "conditioning_file": "ryan/" + ryan["neutral"]["audio_file"],
        "conditioning_sha256": ryan["neutral"]["audio_sha256"],
        "conditioning_transcript": ryan["neutral"]["text"],
        "conditioning_transcript_sha256": ryan["neutral"]["text_sha256"],
        "reference_manifest": "references/ryan/manifest.json",
    }
    lanes["ryan_acted"] = {
        "identity_key": "ryan_acted",
        "review_name": "Ryan — acted anchor",
        "kind": "style_matched_acted_clone_reference",
        "reference_manifest": "references/ryan/manifest.json",
        "style_specific": True,
    }
    return lanes


def acted_reference_for_style(ryan: dict[str, Any], style_key: str) -> dict[str, Any]:
    item = next(row for row in ryan["acted"] if row["style"] == style_key)
    return {
        "source_file": "ryan/" + item["audio_file"],
        "source_sha256": item["audio_sha256"],
        "conditioning_file": "ryan/" + item["audio_file"],
        "conditioning_sha256": item["audio_sha256"],
        "conditioning_transcript": item["text"],
        "conditioning_transcript_sha256": item["text_sha256"],
    }


def support_for(model: dict[str, Any], identity_key: str, style_key: str) -> tuple[bool, str | None]:
    model_key = model["key"]
    if model_key == "higgs_audio_v25":
        return False, "No distinct public Higgs Audio V2.5 checkpoint was identified; do not substitute Higgs TTS 2 invisibly."
    if model_key == "qwen3_tts":
        if identity_key == "native_qwen_aiden":
            return True, None
        if identity_key == "ryan_acted":
            return True, None
        if style_key == "neutral":
            return True, None
        return False, "Official Qwen3-TTS Base voice cloning does not accept style instructions for this identity lane."
    return True, None


def control_for(
    model: dict[str, Any],
    identity_key: str,
    style: dict[str, Any],
    reference: dict[str, Any],
) -> dict[str, Any]:
    model_key = model["key"]
    instruction = style["instruction"]
    base = {
        "requested_instruction": instruction,
        "requested_instruction_sha256": sha256_text(instruction),
        "target_text_sha256": sha256_text(style["target_text"]),
    }
    if model_key == "indextts2":
        return {
            **base,
            "mechanism": "separate_emotion_reference_audio",
            "emotion_reference_file": (
                None if style["key"] == "neutral" else reference["acted_emotion_reference_file"]
            ),
            "emotion_reference_sha256": (
                None if style["key"] == "neutral" else reference["acted_emotion_reference_sha256"]
            ),
            "emo_alpha": 0.0 if style["key"] == "neutral" else style["index_alpha"],
            "num_beams": 1,
            "greedy": True,
            "diffusion_steps": 8,
            "semantic_instruction_directly_consumed": False,
        }
    if model_key == "voxcpm2":
        return {
            **base,
            "mechanism": "reference_plus_instruct",
            "instruct": instruction,
            "cfg_value": 2.0,
            "inference_timesteps": 10,
            "warmup_patches": 1,
            "semantic_instruction_directly_consumed": True,
        }
    if model_key == "qwen3_tts":
        if identity_key == "native_qwen_aiden":
            return {
                **base,
                "mechanism": "built_in_custom_voice_instruct",
                "speaker": "Aiden",
                "instruct": instruction,
                "semantic_instruction_directly_consumed": True,
            }
        return {
            **base,
            "mechanism": (
                "style_matched_reference_prosody"
                if identity_key == "ryan_acted" and style["key"] != "neutral"
                else "base_icl_voice_clone"
            ),
            "instruct": None,
            "semantic_instruction_directly_consumed": False,
        }
    if model_key == "fish_s2_pro":
        return {
            **base,
            "mechanism": "reference_transcript_instruct_and_inline_tag",
            "instruct": instruction,
            "inline_tag": style.get("fish_tag"),
            "temperature": 0.7,
            "top_p": 0.7,
            "top_k": 30,
            "semantic_instruction_directly_consumed": True,
        }
    if model_key == "moss_tts_local_v15":
        return {
            **base,
            "mechanism": "reference_transcript_and_instruction_hint",
            "instruction": instruction,
            "language": "English",
            "audio_temperature": 1.7,
            "audio_top_p": 0.8,
            "audio_top_k": 25,
            "n_vq_for_inference": 12,
            "max_tokens": 768,
            "semantic_instruction_directly_consumed": True,
        }
    if model_key == "chatterbox_multilingual_v3":
        return {
            **base,
            "mechanism": "numeric_exaggeration_cfg_proxy",
            "language_id": "en",
            "exaggeration": style["chatterbox"]["exaggeration"],
            "cfg_weight": style["chatterbox"]["cfg_weight"],
            "semantic_instruction_directly_consumed": False,
        }
    raise ValueError(model_key)


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
                status = (
                    "pending_native_anchor"
                    if identity_key not in identities
                    and reference.get("reference_status") != "ready"
                    else "pending_generation"
                )
                sample_specs.append(
                    {
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
                )

    expected_cells = len(models) * len(STYLES) * len(identities) + sum(
        len(STYLES) for model in models if model.get("native_lane")
    )
    if expected_cells != len(sample_specs) + len(blocked_cells):
        raise RuntimeError("Coverage accounting mismatch.")

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
    output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
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
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
