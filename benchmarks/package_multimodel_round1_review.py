#!/usr/bin/env python3
"""Package the cumulative, locally-openable Round 1 blind review application."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = ROOT / ".omo" / "evidence" / "b17-t05-multimodel-round1"
ASSET_ROOT = ROOT / "benchmarks" / "multimodel_review_assets"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_asset(source: Path, target_root: Path, expected_sha: str | None = None) -> str:
    if not source.is_file():
        raise FileNotFoundError(source)
    actual_sha = sha256_file(source)
    if expected_sha and actual_sha != expected_sha:
        raise RuntimeError(f"Hash mismatch for review asset: {source}")
    suffix = source.suffix.lower() or ".wav"
    target = target_root / f"{actual_sha}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or sha256_file(target) != actual_sha:
        shutil.copy2(source, target)
    return str(target.relative_to(target_root.parent))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--output-root")
    args = parser.parse_args()

    evidence_root = Path(args.evidence_root).expanduser().resolve()
    output_root = (
        Path(args.output_root).expanduser().resolve()
        if args.output_root
        else evidence_root / "review"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    audio_root = output_root / "audio"
    reference_root = output_root / "reference-audio"
    answer_root = output_root / "answer-keys"
    answer_root.mkdir(parents=True, exist_ok=True)

    internal = read_json(evidence_root / "round1_internal_manifest.json")
    objective_by_sample: dict[str, Any] = {}
    objective_root = evidence_root / "objective"
    for group_key in internal["groups"]:
        path = objective_root / f"{group_key}.json"
        if path.is_file():
            objective_by_sample.update(read_json(path).get("measurements") or {})

    public_identities: dict[str, Any] = {}
    reference_asset_cache: dict[str, str] = {}

    def package_reference(sample: dict[str, Any]) -> dict[str, Any]:
        reference = sample["reference"]
        public: dict[str, Any] = {
            "identity_key": sample["identity_key"],
            "review_name": sample["identity_review_name"],
            "kind": sample["identity_kind"],
            "conditioning_transcript": reference.get("conditioning_transcript"),
            "conditioning_transcript_sha256": reference.get(
                "conditioning_transcript_sha256"
            ),
        }
        for public_key, file_key, hash_key in (
            ("original_audio", "source_file", "source_sha256"),
            ("conditioning_audio", "conditioning_file", "conditioning_sha256"),
        ):
            value = reference.get(file_key)
            expected = reference.get(hash_key)
            if not value:
                continue
            source = (evidence_root / "references" / value).resolve()
            cache_key = expected or sha256_file(source)
            if cache_key not in reference_asset_cache:
                reference_asset_cache[cache_key] = copy_asset(
                    source, reference_root, expected
                )
            public[public_key] = reference_asset_cache[cache_key]
        return public

    public_samples: list[dict[str, Any]] = []
    answer_keys: dict[str, list[dict[str, Any]]] = {
        key: [] for key in internal["groups"]
    }
    generated_counts = {key: 0 for key in internal["groups"]}

    for sample in internal["sample_specs"]:
        output = evidence_root / sample["output_file"]
        receipt_path = evidence_root / sample["result_file"]
        objective = objective_by_sample.get(sample["sample_id"])
        generated = output.is_file() and receipt_path.is_file()
        audio_rel = None
        audio_sha = None
        receipt = None
        if generated:
            receipt = read_json(receipt_path)
            audio_sha = sha256_file(output)
            if receipt.get("audio_sha256") != audio_sha:
                raise RuntimeError(f"Invalid generation receipt for {sample['sample_id']}")
            target = audio_root / f"{sample['blind_id']}.wav"
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_file() or sha256_file(target) != audio_sha:
                shutil.copy2(output, target)
            audio_rel = str(target.relative_to(output_root))
            generated_counts[sample["group"]] += 1

        reference = package_reference(sample)
        identity_key = sample["identity_key"]
        identity_public_key = f"{identity_key}:{sample['style']}" if identity_key == "ryan_acted" else identity_key
        public_identities[identity_public_key] = reference

        is_native_identity = identity_key.startswith("native_")
        public_samples.append(
            {
                "sample_id": sample["blind_id"],
                "group": sample["group"],
                "style": sample["style"],
                "style_label": sample["style_label"],
                "identity_key": identity_key,
                "identity_reference_key": identity_public_key,
                "expected_identity": sample["identity_review_name"],
                "review_section_key": (
                    "model_native_voices" if is_native_identity else identity_key
                ),
                "review_section_label": (
                    "Model-native voices"
                    if is_native_identity
                    else sample["identity_review_name"]
                ),
                "target_text": sample["target_text"],
                "requested_instruction": sample["control"]["requested_instruction"],
                "status": "ready" if generated else sample["status"],
                "audio": audio_rel,
                "audio_sha256": audio_sha,
                "automatic_transcript": (
                    objective.get("automatic_transcript") if objective else None
                ),
                "word_error_rate": objective.get("word_error_rate") if objective else None,
                "speaker_cosine": (
                    objective.get("speaker_cosine_to_expected_identity")
                    if objective
                    else None
                ),
                "audio_diagnostics": objective.get("audio_diagnostics") if objective else None,
            }
        )
        answer_keys[sample["group"]].append(
            {
                "sample_id": sample["blind_id"],
                "source_sample_id": sample["sample_id"],
                "model_key": sample["model_key"],
                "model_label": sample["model_label"],
                "identity_key": identity_key,
                "expected_identity": sample["identity_review_name"],
                "style": sample["style"],
                "group": sample["group"],
                "control": sample["control"],
                "reference": sample["reference"],
                "seed": sample["seed"],
                "sample_fingerprint": receipt.get("sample_fingerprint") if receipt else None,
                "audio_sha256": audio_sha,
                "status": "ready" if generated else sample["status"],
            }
        )

    blocked_public = [
        {
            "group": item["group"],
            "style": item["style"],
            "identity_key": item["identity_key"],
            "status": "blocked",
        }
        for item in internal["blocked_cells"]
    ]

    for group_key, rows in answer_keys.items():
        path = answer_root / f"{group_key}.json"
        path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")

    public = {
        "schema_version": 1,
        "round_id": internal["round_id"],
        "title": "Alexandria multimodel expressive-clone blind review — Round 1",
        "groups": internal["groups"],
        "styles": [
            {
                "key": item["key"],
                "label": item["label"],
                "group": item["group"],
                "target_text": item["target_text"],
                "instruction": item["instruction"],
            }
            for item in internal["styles"]
        ],
        "identities": public_identities,
        "samples": public_samples,
        "blocked_coverage": blocked_public,
        "generated_counts": generated_counts,
        "review_fields": [
            "identity_1_to_5",
            "delivery_1_to_5",
            "naturalness_1_to_5",
            "artifact_severity_1_to_5",
            "spoken_text_matches_expected",
            "requested_mode_is_clear",
            "approve_for_comparison",
            "flag_for_follow_up",
            "notes",
        ],
        "answer_key_files": {
            key: f"answer-keys/{key}.json" for key in answer_keys
        },
        "cumulative_partial_exports_supported": True,
        "production_promotion_allowed": False,
    }
    data_path = output_root / "data.js"
    data_path.write_text(
        "window.ALEXANDRIA_ROUND1_DATA = "
        + json.dumps(public, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )

    for filename in ("index.html", "app.js", "styles.css"):
        source = ASSET_ROOT / filename
        if not source.is_file():
            raise FileNotFoundError(source)
        shutil.copy2(source, output_root / filename)

    manifest = {
        "schema_version": 1,
        "round_id": internal["round_id"],
        "review": "index.html",
        "group_count": len(internal["groups"]),
        "style_count": len(internal["styles"]),
        "generated_sample_count": sum(generated_counts.values()),
        "sample_spec_count": len(public_samples),
        "blocked_coverage_count": len(blocked_public),
        "generated_counts": generated_counts,
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
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(output_root), **manifest}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
