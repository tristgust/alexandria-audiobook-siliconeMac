#!/usr/bin/env python3
"""Prepare a 12-sample validation of the repaired Chris identity reference."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE_EVIDENCE = ROOT / ".omo/evidence/chris-roz-multimodel-round1-v1"
REPAIR_EVIDENCE = ROOT / ".omo/evidence/chris-canonical-reference-repair-v1"
DEFAULT_OUTPUT = ROOT / ".omo/evidence/chris-reference-repair-validation-v1"
ROUND_ID = "alexandria_chris_reference_repair_validation_v1"
VARIANT_KEY = "mossformer2_blend_70"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(*parts: Any, length: int = 20) -> str:
    return hashlib.sha256("\x1f".join(map(str, parts)).encode()).hexdigest()[:length]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    output = Path(args.output_root).expanduser().resolve()
    source_manifest = read_json(SOURCE_EVIDENCE / "private/internal-manifest.json")
    metrics = read_json(REPAIR_EVIDENCE / "metrics.json")
    variant = next(row for row in metrics["rows"] if row["key"] == VARIANT_KEY)
    if not variant["eligible"]:
        raise ValueError(f"Repair variant is not eligible: {VARIANT_KEY}")
    variant_path = Path(variant["path"])
    variant_sha = sha256_file(variant_path)
    expected = "And I can see a few aliens and a couple of robots, but not many."
    reference_target = output / "private/references/chris/repaired/reference.wav"
    reference_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(variant_path, reference_target)

    selected = [
        deepcopy(spec)
        for spec in source_manifest["sample_specs"]
        if spec["identity_key"] == "chris"
        and spec["reference_tier"] == "canonical_cleaned"
        and int(spec["repeat"]) == 1
    ]
    if len(selected) != 12:
        raise ValueError(f"Expected 12 validation cells, found {len(selected)}")

    specs = []
    for index, spec in enumerate(selected):
        model = str(spec["model_key"])
        style = str(spec["style"])
        cell = stable_id(ROUND_ID, model, style)
        new = deepcopy(spec)
        new.update(
            {
                "round_id": ROUND_ID,
                "sample_id": f"crrv_{cell}",
                "blind_id": stable_id("blind", ROUND_ID, cell, length=16),
                "reference_tier": "canonical_repaired",
                "repeat": 1,
                "seed": 12000 + index,
                "output_file": str(Path("outputs") / model / style / "repaired.wav"),
                "result_file": str(Path("outputs") / model / style / "repaired.json"),
                "status": "pending_generation",
            }
        )
        new["reference"] = {
            "reference_key": "chris:canonical_repaired",
            "identity_key": "chris",
            "identity_label": "Chris Cwej",
            "tier": "canonical_repaired",
            "candidate_id": f"chris-35-trial_time_machine-55:{VARIANT_KEY}",
            "audio_file": str(reference_target.relative_to(output)),
            "audio_sha256": variant_sha,
            "transcript": expected,
            "transcript_sha256": hashlib.sha256(expected.encode()).hexdigest(),
        }
        specs.append(new)

    internal = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "purpose": "validate_dereverberated_chris_canonical_identity_reference",
        "models": source_manifest["models"],
        "sample_specs": specs,
        "reference_variant": {
            "key": VARIANT_KEY,
            "path": str(reference_target),
            "sha256": variant_sha,
            "source_metrics": variant["metrics"],
        },
        "tnia_miller_included": False,
        "manual_blind_review_required": True,
        "production_promotion_allowed": False,
    }
    write_json(output / "private/internal-manifest.json", internal)

    index_specs = []
    for spec in specs:
        if spec["model_key"] != "indextts2_matched_control":
            continue
        emotion = spec["emotion_reference"]
        emotion_path = SOURCE_EVIDENCE / emotion["audio_file"]
        result = output / spec["result_file"]
        audio = output / spec["output_file"]
        index_specs.append(
            {
                "sample_id": spec["sample_id"],
                "blind_id": spec["blind_id"],
                "group": spec["group"],
                "identity_key": "chris",
                "identity_label": "Chris Cwej",
                "style": spec["style"],
                "selection_kind": "repaired_canonical_reference_validation",
                "source_selection_sample_id": new["reference"]["candidate_id"],
                "source_instruction_sha256": spec["instruction_sha256"],
                "source_seed": spec["seed"],
                "seed": spec["seed"],
                "reference_audio": str(reference_target),
                "reference_audio_sha256": variant_sha,
                "emotion_audio_prompt": str(emotion_path),
                "emotion_audio_sha256": emotion["audio_sha256"],
                "emotion_strength": float(spec["control"]["emotion_strength"]),
                "emotion_strength_origin": "repair_validation_config",
                "text": spec["target_text"],
                "output_file": str(audio),
                "result_file": str(result),
                "generation": {"max_mel_tokens": 600},
            }
        )
    write_json(
        output / "private/indextts2-manifest.json",
        {
            "schema_version": 1,
            "round_id": ROUND_ID,
            "purpose": "repaired_chris_reference_indextts2_validation",
            "runtime_profile": {
                "persistent_worker_count": 2,
                "use_fp16": False,
                "device": "mps",
                "greedy": True,
                "num_beams": 1,
                "diffusion_steps": 8,
            },
            "samples": index_specs,
            "tnia_miller_included": False,
            "production_promotion_allowed": False,
        },
    )
    write_json(
        output / "manifest.json",
        {
            "schema_version": 1,
            "round_id": ROUND_ID,
            "sample_count": len(specs),
            "model_counts": {model: sum(s["model_key"] == model for s in specs) for model in sorted({s["model_key"] for s in specs})},
            "reference_variant": VARIANT_KEY,
            "reference_sha256": variant_sha,
            "production_promotion_allowed": False,
        },
    )
    print(json.dumps({"output": str(output), "samples": len(specs), "index_samples": len(index_specs), "reference": VARIANT_KEY}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
