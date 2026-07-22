#!/usr/bin/env python3
"""Build the arbitrary-size IndexTTS2 portion of multimodel Round 1."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = ROOT / ".omo" / "evidence" / "b17-t05-multimodel-round1"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--group", action="append")
    args = parser.parse_args()

    evidence_root = Path(args.evidence_root).expanduser().resolve()
    internal = read_json(evidence_root / "round1_internal_manifest.json")
    ryan = read_json(evidence_root / "references" / "ryan" / "manifest.json")
    acted_by_style = {item["style"]: item for item in ryan["acted"]}
    requested_groups = set(args.group or [])

    samples = []
    for source in internal["sample_specs"]:
        if source["model_key"] != "indextts2":
            continue
        if requested_groups and source["group"] not in requested_groups:
            continue
        reference = evidence_root / "references" / source["reference"]["conditioning_file"]
        emotion = evidence_root / "references" / source["reference"]["acted_emotion_reference_file"]
        output = evidence_root / source["output_file"]
        result = evidence_root / source["result_file"]
        if not reference.is_file():
            raise FileNotFoundError(reference)
        if not emotion.is_file():
            raise FileNotFoundError(emotion)
        expected_reference_sha = source["reference"]["conditioning_sha256"]
        expected_emotion_sha = source["reference"]["acted_emotion_reference_sha256"]
        if sha256_file(reference) != expected_reference_sha:
            raise RuntimeError(f"Reference hash mismatch: {source['sample_id']}")
        if sha256_file(emotion) != expected_emotion_sha:
            raise RuntimeError(f"Emotion hash mismatch: {source['sample_id']}")
        acted = acted_by_style[source["style"]]
        samples.append(
            {
                "sample_id": source["sample_id"],
                "blind_id": source["blind_id"],
                "model_key": "indextts2",
                "speaker": source["identity_key"],
                "identity_key": source["identity_key"],
                "identity_label": source["identity_review_name"],
                "style": source["style"],
                "group": source["group"],
                "text": source["target_text"],
                "reference_audio": str(reference),
                "reference_audio_sha256": expected_reference_sha,
                "emotion_audio_prompt": str(emotion),
                "emotion_audio_sha256": expected_emotion_sha,
                "emotion_strength": float(source["control"]["emo_alpha"]),
                "emotion_strength_origin": "round1_taxonomy_contract",
                "selection_kind": "style_matched_acted_reference",
                "source_selection_sample_id": f"ryan_acted:{source['style']}",
                "source_instruction_sha256": source["control"]["requested_instruction_sha256"],
                "source_seed": int(acted["seed"]),
                "seed": int(source["seed"]),
                "control": source["control"],
                "output_file": str(output),
                "result_file": str(result),
                "generation": {"max_mel_tokens": 600},
            }
        )

    manifest = {
        "schema_version": 1,
        "round_id": internal["round_id"],
        "purpose": "full_taxonomy_multimodel_round1_indextts2_generation",
        "sample_count": len(samples),
        "groups": sorted({item["group"] for item in samples}),
        "identities": sorted({item["identity_key"] for item in samples}),
        "styles": sorted({item["style"] for item in samples}),
        "runtime_profile": {
            "candidate": "IndexTTS2",
            "device": "mps",
            "use_fp16": False,
            "mps_fast_math": True,
            "mps_prefer_metal": True,
            "num_beams": 1,
            "greedy_generation": True,
            "diffusion_steps": 8,
            "persistent_worker_count": 2,
        },
        "samples": samples,
        "production_promotion_allowed": False,
    }
    suffix = "-".join(sorted(requested_groups)) if requested_groups else "all"
    output = evidence_root / f"indextts2_round1_manifest_{suffix}.json"
    output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(output),
        "sample_count": len(samples),
        "group_count": len(manifest["groups"]),
        "identity_count": len(manifest["identities"]),
        "style_count": len(manifest["styles"]),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
