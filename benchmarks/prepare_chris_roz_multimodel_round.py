#!/usr/bin/env python3
"""Build the focused Chris/Roz three-model blind-round manifest.

The preparation step copies only the accepted cleaned reference bank into an
isolated evidence root, records exact hashes and transcripts, and emits 96
matched sample specifications plus the IndexTTS2 pool manifest. It performs no
model generation and never changes Alexandria production state.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "benchmarks/chris_roz_multimodel_round1.json"
DEFAULT_EVIDENCE = ROOT / ".omo/evidence/chris-roz-multimodel-round1-v1"


class PreparationError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    if not path.is_file():
        raise PreparationError(f"Required JSON file is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(*parts: Any, length: int = 20) -> str:
    payload = "\x1f".join(map(str, parts)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:length]


def copy_reference(source: Path, target: Path, expected_sha: str) -> None:
    if not source.is_file():
        raise PreparationError(f"Reference source is missing: {source}")
    if sha256_file(source) != expected_sha:
        raise PreparationError(f"Reference source hash changed: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or sha256_file(target) != expected_sha:
        shutil.copy2(source, target)
    if sha256_file(target) != expected_sha:
        raise PreparationError(f"Copied reference hash changed: {target}")


def identity_reference(bank: Mapping[str, Any], identity: str, tier: str) -> dict[str, Any]:
    row = bank["identity_references"][identity]
    if tier == "clean_actor":
        source = row["clean_actor"]
        return {
            "candidate_id": source["candidate_id"],
            "audio_path": source["audio_path"],
            "audio_sha256": source["audio_sha256"],
            "transcript": source["transcript"],
        }
    if tier == "canonical_cleaned":
        source = row["canonical_cleaned"]
        return {
            "candidate_id": source["candidate_id"],
            "audio_path": source["final_path"],
            "audio_sha256": source["final_sha256"],
            "transcript": source["transcript"],
        }
    raise PreparationError(f"Unknown reference tier: {tier}")


def performance_map(bank: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for rows in bank["performance_bank"].values():
        for row in rows:
            candidate_id = str(row["candidate_id"])
            if candidate_id in result:
                raise PreparationError(f"Duplicate performance reference: {candidate_id}")
            result[candidate_id] = dict(row)
    return result


def fish_prompt(style: Mapping[str, Any]) -> str:
    mode = str(style["fish_prompt_mode"])
    target = str(style["target_text"])
    tag = str(style["fish_tag"])
    if mode in {"simple_tag", "rich_tag", "full_alexandria_tag"}:
        return f"[{tag}] {target}"
    if mode == "untagged":
        return target
    raise PreparationError(f"Unknown Fish prompt mode: {mode}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE)
    args = parser.parse_args()

    config_path = args.config.expanduser().resolve()
    config = read_json(config_path)
    if config.get("schema_version") != 1:
        raise PreparationError("Unsupported focused-round schema.")
    if config.get("tnia_miller_included") is not False:
        raise PreparationError("T'Nia must remain absent from the focused round.")
    bank_path = Path(str(config["reference_bank"])).expanduser().resolve()
    bank = read_json(bank_path)
    if bank.get("tnia_miller_included") is not False:
        raise PreparationError("Reference bank unexpectedly contains T'Nia.")

    evidence = args.evidence_root.expanduser().resolve()
    reference_root = evidence / "private/references"
    models = {row["key"]: row for row in config["models"]}
    tiers = [str(row["key"]) for row in config["reference_tiers"]]
    if tiers != ["clean_actor", "canonical_cleaned"]:
        raise PreparationError(f"Reference tiers changed: {tiers}")
    performances = performance_map(bank)
    references: dict[str, dict[str, Any]] = {}

    for identity in config["identities"]:
        identity_key = str(identity["key"])
        for tier in tiers:
            source = identity_reference(bank, identity_key, tier)
            target = reference_root / "identity" / identity_key / tier / "reference.wav"
            copy_reference(Path(source["audio_path"]), target, str(source["audio_sha256"]))
            reference_key = f"{identity_key}:{tier}"
            references[reference_key] = {
                "reference_key": reference_key,
                "identity_key": identity_key,
                "identity_label": identity["label"],
                "tier": tier,
                "candidate_id": source["candidate_id"],
                "audio_file": str(target.relative_to(evidence)),
                "audio_sha256": source["audio_sha256"],
                "transcript": source["transcript"],
                "transcript_sha256": sha256_text(source["transcript"]),
            }

    copied_performance: dict[str, dict[str, Any]] = {}
    requested_performances = {
        str(style["emotion_reference_id"])
        for identity in config["identities"]
        for style in identity["styles"]
        if float(style["index_alpha"]) > 0.0
    }
    missing_performances = sorted(requested_performances - set(performances))
    if missing_performances:
        raise PreparationError(f"Missing performance references: {missing_performances}")
    permission_by_reference = {
        str(style["emotion_reference_id"]): bool(style.get("allow_gated_emotion_reference"))
        for identity in config["identities"]
        for style in identity["styles"]
        if float(style["index_alpha"]) > 0.0
    }
    gated_without_permission = sorted(
        candidate_id
        for candidate_id in requested_performances
        if performances[candidate_id].get("manual_review_required") is True
        and not permission_by_reference.get(candidate_id, False)
    )
    if gated_without_permission:
        raise PreparationError(
            "Gated emotion references require explicit permission: "
            f"{gated_without_permission}"
        )
    for candidate_id in sorted(requested_performances):
        source = performances[candidate_id]
        target = reference_root / "performance" / f"{candidate_id}.wav"
        copy_reference(Path(source["audio_path"]), target, str(source["audio_sha256"]))
        copied_performance[candidate_id] = {
            "candidate_id": candidate_id,
            "audio_file": str(target.relative_to(evidence)),
            "audio_sha256": source["audio_sha256"],
            "transcript": source["transcript"],
            "transcript_sha256": sha256_text(source["transcript"]),
            "roles": source["roles"],
            "manual_review_required": bool(source.get("manual_review_required")),
            "allowed_as_emotion_only": bool(permission_by_reference.get(candidate_id, False)),
        }

    specs: list[dict[str, Any]] = []
    repeats = int(config["generation"]["repeats"])
    for identity in config["identities"]:
        identity_key = str(identity["key"])
        for tier in tiers:
            reference = references[f"{identity_key}:{tier}"]
            for style in identity["styles"]:
                style_key = str(style["key"])
                emotion_reference = (
                    copied_performance[str(style["emotion_reference_id"])]
                    if float(style["index_alpha"]) > 0.0
                    else reference
                )
                for model in config["models"]:
                    model_key = str(model["key"])
                    for repeat in range(1, repeats + 1):
                        cell_id = stable_id(config["round_id"], identity_key, tier, style_key, model_key, repeat)
                        sample_id = f"cr1_{cell_id}"
                        blind_id = stable_id("blind", config["round_id"], cell_id, length=16)
                        output_rel = Path("outputs") / model_key / identity_key / tier / style_key / f"repeat-{repeat}.wav"
                        result_rel = output_rel.with_suffix(".json")
                        seed = 9300 + len(specs)
                        spec = {
                            "sample_id": sample_id,
                            "blind_id": blind_id,
                            "model_key": model_key,
                            "model_label": model["label"],
                            "identity_key": identity_key,
                            "identity_label": identity["label"],
                            "reference_tier": tier,
                            "reference": reference,
                            "style": style_key,
                            "style_label": style["label"],
                            "group": style["group"],
                            "target_text": style["target_text"],
                            "target_text_sha256": sha256_text(style["target_text"]),
                            "instruction": style["instruction"],
                            "instruction_sha256": sha256_text(style["instruction"]),
                            "fish_prompt_mode": style["fish_prompt_mode"],
                            "fish_prompt": fish_prompt(style),
                            "fish_prompt_sha256": sha256_text(fish_prompt(style)),
                            "emotion_reference": emotion_reference,
                            "index_alpha": float(style["index_alpha"]),
                            "repeat": repeat,
                            "seed": seed,
                            "output_file": str(output_rel),
                            "result_file": str(result_rel),
                            "status": "pending_generation",
                            "production_promotion_allowed": False,
                        }
                        if model_key == "voxcpm2_controllable_clone":
                            spec["control"] = {
                                "instruct": style["instruction"],
                                **config["generation"]["voxcpm2"],
                            }
                        elif model_key == "fish_s2_pro_cloud":
                            spec["control"] = {
                                "prompt_mode": style["fish_prompt_mode"],
                                **config["generation"]["fish"],
                            }
                        elif model_key == "indextts2_matched_control":
                            spec["control"] = {
                                "mechanism": "same_character_emotion_reference",
                                "emotion_strength": float(style["index_alpha"]),
                                **config["generation"]["indextts2"],
                            }
                        else:
                            raise PreparationError(f"Unknown model: {model_key}")
                        specs.append(spec)

    expected = len(config["identities"]) * len(tiers) * 4 * len(models) * repeats
    if len(specs) != expected:
        raise PreparationError(f"Coverage mismatch: expected {expected}, built {len(specs)}")
    manifest = {
        "schema_version": 1,
        "round_id": config["round_id"],
        "generated_at": utc_now(),
        "config_file": str(config_path),
        "config_sha256": sha256_file(config_path),
        "source_reference_bank": str(bank_path),
        "source_reference_bank_sha256": sha256_file(bank_path),
        "models": config["models"],
        "reference_tiers": config["reference_tiers"],
        "identities": config["identities"],
        "references": references,
        "performance_references": copied_performance,
        "expected_sample_count": expected,
        "sample_specs": specs,
        "tnia_miller_included": False,
        "manual_blind_review_required": True,
        "production_promotion_allowed": False,
    }
    evidence.mkdir(parents=True, exist_ok=True)
    write_json(evidence / "private/internal-manifest.json", manifest)

    index_specs = []
    for spec in specs:
        if spec["model_key"] != "indextts2_matched_control":
            continue
        reference = evidence / spec["reference"]["audio_file"]
        emotion = evidence / spec["emotion_reference"]["audio_file"]
        index_specs.append(
            {
                "sample_id": spec["sample_id"],
                "blind_id": spec["blind_id"],
                "group": spec["group"],
                "identity_key": spec["identity_key"],
                "identity_label": spec["identity_label"],
                "reference_tier": spec["reference_tier"],
                "style": spec["style"],
                "selection_kind": "cleaned_reference_bank",
                "source_selection_sample_id": spec["reference"]["candidate_id"],
                "source_instruction_sha256": spec["instruction_sha256"],
                "source_seed": spec["seed"],
                "seed": spec["seed"],
                "reference_audio": str(reference),
                "reference_audio_sha256": spec["reference"]["audio_sha256"],
                "emotion_audio_prompt": str(emotion),
                "emotion_audio_sha256": spec["emotion_reference"]["audio_sha256"],
                "emotion_strength": spec["index_alpha"],
                "emotion_strength_origin": "focused_round_config",
                "text": spec["target_text"],
                "output_file": str(evidence / spec["output_file"]),
                "result_file": str(evidence / spec["result_file"]),
                "generation": {"max_mel_tokens": config["generation"]["indextts2"]["max_mel_tokens"]},
            }
        )
    index_manifest = {
        "schema_version": 1,
        "round_id": config["round_id"],
        "runtime_profile": {
            "persistent_worker_count": 2,
            "device": "mps",
            "use_fp16": False,
            "num_beams": 1,
            "greedy": True,
            "diffusion_steps": 8
        },
        "samples": index_specs,
    }
    write_json(evidence / "private/indextts2-manifest.json", index_manifest)
    write_json(
        evidence / "manifest.json",
        {
            "schema_version": 1,
            "round_id": config["round_id"],
            "expected_sample_count": expected,
            "model_counts": {key: sum(spec["model_key"] == key for spec in specs) for key in models},
            "identity_counts": {row["key"]: sum(spec["identity_key"] == row["key"] for spec in specs) for row in config["identities"]},
            "reference_tier_counts": {tier: sum(spec["reference_tier"] == tier for spec in specs) for tier in tiers},
            "tnia_miller_included": False,
            "generation_complete": False,
            "production_mutation": False,
        },
    )
    print(json.dumps({"evidence_root": str(evidence), "samples": len(specs), "index_samples": len(index_specs), "references": len(references), "performance_references": len(copied_performance)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
