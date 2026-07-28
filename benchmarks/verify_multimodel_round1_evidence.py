#!/usr/bin/env python3
"""Verify multimodel Round 1 generation and packaged-review evidence."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
from typing import Any

DEFAULT_EVIDENCE = Path(
    "/Users/tristan/.devspace/worktrees/"
    "alexandria-audiobook.git-78fc5814/.omo/evidence/"
    "b17-t05-multimodel-round1"
)
FORBIDDEN_PUBLIC_MODEL_LABELS = (
    "IndexTTS2",
    "VoxCPM2",
    "Qwen3-TTS",
    "Fish Audio",
    "MOSS-TTS",
    "Chatterbox Multilingual",
    "Higgs Audio",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_public_data(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    prefix = "window.ALEXANDRIA_ROUND1_DATA = "
    suffix = ";\n"
    if not text.startswith(prefix) or not text.endswith(suffix):
        raise RuntimeError("Review data.js does not use the expected envelope.")
    return json.loads(text[len(prefix) : -len(suffix)])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--require-objective", action="store_true")
    args = parser.parse_args()

    evidence_root = Path(args.evidence_root).expanduser().resolve()
    internal = read_json(evidence_root / "round1_internal_manifest.json")
    sample_specs = list(internal["sample_specs"])
    blocked = list(internal["blocked_cells"])
    errors: list[str] = []
    warnings: list[str] = []

    if len(sample_specs) != int(internal["sample_spec_count"]):
        errors.append("Internal sample_spec_count does not match sample_specs length.")
    if len(blocked) != int(internal["blocked_cell_count"]):
        errors.append("Internal blocked_cell_count does not match blocked_cells length.")
    if len(sample_specs) + len(blocked) != int(
        internal["expected_coverage_cell_count"]
    ):
        errors.append("Coverage accounting does not close.")

    sample_ids = [item["sample_id"] for item in sample_specs]
    blind_ids = [item["blind_id"] for item in sample_specs]
    if len(set(sample_ids)) != len(sample_ids):
        errors.append("Source sample IDs are not unique.")
    if len(set(blind_ids)) != len(blind_ids):
        errors.append("Blind sample IDs are not unique.")

    generated: dict[str, dict[str, Any]] = {}
    generated_by_model = collections.Counter()
    generated_by_group = collections.Counter()
    generated_by_identity = collections.Counter()
    sample_by_id = {item["sample_id"]: item for item in sample_specs}
    for sample in sample_specs:
        output = evidence_root / sample["output_file"]
        receipt_path = evidence_root / sample["result_file"]
        if not output.is_file() and not receipt_path.is_file():
            continue
        if not output.is_file() or not receipt_path.is_file():
            errors.append(f"Incomplete WAV/receipt pair: {sample['sample_id']}")
            continue
        try:
            receipt = read_json(receipt_path)
        except Exception as exc:
            errors.append(
                f"Unreadable receipt {sample['sample_id']}: {type(exc).__name__}: {exc}"
            )
            continue
        audio_sha = sha256_file(output)
        if receipt.get("sample_id") != sample["sample_id"]:
            errors.append(f"Receipt sample ID mismatch: {sample['sample_id']}")
        if receipt.get("blind_id") != sample["blind_id"]:
            errors.append(f"Receipt blind ID mismatch: {sample['sample_id']}")
        if receipt.get("model_key") != sample["model_key"]:
            errors.append(f"Receipt model mismatch: {sample['sample_id']}")
        if (
            sample["model_key"] != "indextts2"
            and receipt.get("control") != sample["control"]
        ):
            errors.append(f"Receipt control contract is stale: {sample['sample_id']}")
        if receipt.get("audio_sha256") != audio_sha:
            errors.append(f"Output hash mismatch: {sample['sample_id']}")
        if not receipt.get("sample_fingerprint"):
            errors.append(f"Missing sample fingerprint: {sample['sample_id']}")
        if sample["model_key"] == "moss_tts_local_v15":
            if int((receipt.get("control") or {}).get("max_tokens") or -1) != 768:
                errors.append(f"MOSS token cap is not bounded: {sample['sample_id']}")
            if receipt.get("reference_code_cache_status") not in {
                "encoded",
                "memory",
                "disk",
            }:
                errors.append(
                    f"MOSS reference-code cache status is missing: {sample['sample_id']}"
                )
        if sample["model_key"] == "chatterbox_multilingual_v3":
            if receipt.get("model_revision") != "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18":
                errors.append(f"Chatterbox model revision drift: {sample['sample_id']}")
            if receipt.get("source_commit") != "5de7a54aa4e5e2baadb0182dde554908b48b85c2":
                errors.append(f"Chatterbox source commit drift: {sample['sample_id']}")
            if receipt.get("t3_model") != "v3":
                errors.append(f"Chatterbox sample is not V3: {sample['sample_id']}")
            runtime_controls = receipt.get("runtime_controls") or {}
            if runtime_controls.get("watermark_applied") is not False:
                errors.append(f"Chatterbox watermark truth is missing: {sample['sample_id']}")
            if runtime_controls.get("numeric_control_proxy") is not True:
                errors.append(f"Chatterbox numeric proxy truth is missing: {sample['sample_id']}")
        generated[sample["sample_id"]] = receipt
        generated_by_model[sample["model_key"]] += 1
        generated_by_group[sample["group"]] += 1
        generated_by_identity[sample["identity_key"]] += 1

    moss_reference_cache_count = 0
    moss_reference_cache_errors: list[str] = []
    moss_cache_root = evidence_root / "moss-reference-codes"
    for metadata_path in sorted(moss_cache_root.glob("*.json")):
        cache_path = metadata_path.with_suffix(".npz")
        try:
            metadata_payload = read_json(metadata_path)
        except Exception as exc:
            moss_reference_cache_errors.append(
                f"Unreadable MOSS cache metadata {metadata_path.name}: {exc}"
            )
            continue
        if not cache_path.is_file():
            moss_reference_cache_errors.append(
                f"Missing MOSS cache file for {metadata_path.name}"
            )
            continue
        if metadata_payload.get("cache_file_sha256") != sha256_file(cache_path):
            moss_reference_cache_errors.append(
                f"MOSS cache hash mismatch for {cache_path.name}"
            )
            continue
        if metadata_payload.get("tokenizer_revision") != "f6e20e543b33d2c252a7ef71bdf8aa71e5ff9169":
            moss_reference_cache_errors.append(
                f"MOSS tokenizer revision drift for {cache_path.name}"
            )
            continue
        if int(metadata_payload.get("num_quantizers") or -1) != 12:
            moss_reference_cache_errors.append(
                f"MOSS quantizer count drift for {cache_path.name}"
            )
            continue
        moss_reference_cache_count += 1
    if moss_reference_cache_errors:
        errors.extend(moss_reference_cache_errors)

    native_manifest_path = evidence_root / "references" / "native" / "manifest.json"
    native_reference_count = 0
    if native_manifest_path.is_file():
        native_manifest = read_json(native_manifest_path)
        native_root = native_manifest_path.parent
        required_native_keys = {
            "native_qwen_aiden",
            "native_voxcpm2_rowan",
            "native_fish_marlow",
            "native_moss_alder",
            "native_chatterbox_linden",
        }
        present_native_keys = set()
        for record in native_manifest.get("records", []):
            key = record.get("identity_key")
            audio_file = record.get("audio_file")
            if not key or not audio_file:
                errors.append("Native reference record is incomplete.")
                continue
            audio_path = native_root / str(audio_file)
            if not audio_path.is_file():
                errors.append(f"Native reference audio is missing: {key}")
                continue
            if record.get("audio_sha256") != sha256_file(audio_path):
                errors.append(f"Native reference hash mismatch: {key}")
                continue
            present_native_keys.add(key)
            native_reference_count += 1
        missing_native = sorted(required_native_keys - present_native_keys)
        if missing_native:
            errors.append(f"Required native anchors are missing: {missing_native}")
    else:
        errors.append("Native reference manifest is missing.")

    lock_files = list(evidence_root.rglob("*.lock"))
    if lock_files:
        warnings.append(f"{len(lock_files)} active or orphaned sample locks remain.")

    if args.require_complete and len(generated) != len(sample_specs):
        errors.append(
            f"Round 1 is incomplete: {len(generated)}/{len(sample_specs)} generated."
        )

    objective_count = 0
    objective_missing_hashes: list[str] = []
    for group_key in internal["groups"]:
        path = evidence_root / "objective" / f"{group_key}.json"
        if not path.is_file():
            if args.require_objective:
                errors.append(f"Objective evidence is missing for {group_key}.")
            continue
        payload = read_json(path)
        measurements = payload.get("measurements") or {}
        for sample_id, measurement in measurements.items():
            sample = sample_by_id.get(sample_id)
            if not sample or sample_id not in generated:
                continue
            output = evidence_root / sample["output_file"]
            if measurement.get("audio_sha256") != sha256_file(output):
                objective_missing_hashes.append(sample_id)
            else:
                objective_count += 1
    if objective_missing_hashes:
        errors.append(
            f"{len(objective_missing_hashes)} objective rows have stale audio hashes."
        )
    if args.require_objective and objective_count != len(generated):
        errors.append(
            f"Objective evidence is incomplete: {objective_count}/{len(generated)} rows."
        )

    review_root = evidence_root / "review"
    public_ready = 0
    model_order_sequences: dict[str, set[tuple[str, ...]]] = collections.defaultdict(set)
    if (review_root / "manifest.json").is_file():
        review_manifest = read_json(review_root / "manifest.json")
        public = read_public_data(review_root / "data.js")
        public_text = (review_root / "data.js").read_text(encoding="utf-8")
        for label in FORBIDDEN_PUBLIC_MODEL_LABELS:
            if label.casefold() in public_text.casefold():
                errors.append(f"Public review data leaks model label: {label}")

        public_samples = list(public["samples"])
        public_by_blind = {item["sample_id"]: item for item in public_samples}
        if len(public_by_blind) != len(public_samples):
            errors.append("Public review sample IDs are not unique.")
        public_ready = sum(
            item.get("status") == "ready" and bool(item.get("audio"))
            for item in public_samples
        )
        if public_ready != int(review_manifest["generated_sample_count"]):
            errors.append("Review generated_sample_count does not match public ready rows.")
        if public_ready != len(generated):
            warnings.append(
                f"Review package is stale: {public_ready} ready rows vs {len(generated)} generated."
            )

        internal_by_blind = {item["blind_id"]: item for item in sample_specs}
        section_rows: dict[tuple[str, str], list[dict[str, Any]]] = collections.defaultdict(list)
        for public_sample in public_samples:
            blind_id = public_sample["sample_id"]
            source = internal_by_blind.get(blind_id)
            if source is None:
                errors.append(f"Unknown public blind ID: {blind_id}")
                continue
            if public_sample.get("status") != "ready":
                continue
            audio_rel = public_sample.get("audio")
            audio = review_root / str(audio_rel)
            if not audio.is_file():
                errors.append(f"Packaged audio is missing: {blind_id}")
            elif public_sample.get("audio_sha256") != sha256_file(audio):
                errors.append(f"Packaged audio hash mismatch: {blind_id}")
            if source["identity_key"].startswith("native_"):
                if public_sample.get("review_section_key") != "model_native_voices":
                    errors.append(f"Native voice is not pooled: {blind_id}")
            elif public_sample.get("review_section_key") != source["identity_key"]:
                errors.append(f"Clone identity section mismatch: {blind_id}")
            section_rows[
                (public_sample["style"], public_sample["review_section_key"])
            ].append(source)

        for (style, section), rows in section_rows.items():
            rows.sort(key=lambda item: item["blind_id"])
            sequence = tuple(item["model_key"] for item in rows)
            model_order_sequences[section].add(sequence)
            if section != "model_native_voices" and len(set(sequence)) < 2:
                warnings.append(
                    f"Only one model is ready for {style}/{section}; comparison remains partial."
                )
        for section, sequences in model_order_sequences.items():
            if len(sequences) == 1 and len(next(iter(sequences), ())) > 1:
                errors.append(
                    f"Candidate model order does not change across styles for {section}."
                )

        answer_keys = review_manifest.get("answer_keys_separate") is True
        if not answer_keys:
            errors.append("Review manifest does not assert separate answer keys.")
        for group_key in internal["groups"]:
            if not (review_root / "answer-keys" / f"{group_key}.json").is_file():
                errors.append(f"Answer key is missing for {group_key}.")
    elif args.require_complete:
        errors.append("Packaged review is missing.")

    payload = {
        "schema_version": 1,
        "round_id": internal["round_id"],
        "sample_spec_count": len(sample_specs),
        "blocked_cell_count": len(blocked),
        "generated_sample_count": len(generated),
        "generated_by_model": dict(sorted(generated_by_model.items())),
        "generated_by_group": dict(sorted(generated_by_group.items())),
        "generated_by_identity": dict(sorted(generated_by_identity.items())),
        "objective_measurement_count": objective_count,
        "public_ready_sample_count": public_ready,
        "remaining_lock_count": len(lock_files),
        "moss_reference_cache_count": moss_reference_cache_count,
        "native_reference_count": native_reference_count,
        "model_order_sequence_count_by_section": {
            key: len(value) for key, value in sorted(model_order_sequences.items())
        },
        "error_count": len(errors),
        "errors": errors,
        "warning_count": len(warnings),
        "warnings": warnings,
        "complete_generation": len(generated) == len(sample_specs),
        "complete_objective": objective_count == len(generated),
        "production_promotion_allowed": False,
    }
    output = evidence_root / "verification.json"
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), **payload}, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
