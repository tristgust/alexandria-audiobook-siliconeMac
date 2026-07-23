#!/usr/bin/env python3
"""Build the focused Alexandria Narrator Rescue Round 2 blind review.

Round 2 answers one narrow question raised by Round 1 v2: does the existing
Qwen narrator LoRA pilot improve identity-plus-delivery enough to justify a
proper reviewed training dataset? The package reuses valid Round 1 Narrator
outputs for the viable clone baselines and generates only six new LoRA samples.

The original Round 1 evidence and review results are never modified.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

ROUND_ID = "alexandria_narrator_rescue_round2_v1"
DEFAULT_ROUND1 = Path(
    "/Users/tristan/.devspace/worktrees/alexandria-audiobook.git-78fc5814/"
    ".omo/evidence/b17-t05-multimodel-round1-v2-usable"
)
DEFAULT_RESULTS = Path(
    "/Users/tristan/Downloads/alexandria_round1_cumulative_all(4).json"
)
DEFAULT_DESTINATION = Path(
    "/Users/tristan/.devspace/worktrees/alexandria-audiobook.git-78fc5814/"
    ".omo/evidence/b17-t06-narrator-rescue-round2"
)
DEFAULT_LORA_MODEL = Path(
    "/Users/tristan/pinokio/api/alexandria-audiobook.git/"
    "lora_models/narrator_attention_r8_pilot/mlx_model"
)
ASSET_ROOT = ROOT / "benchmarks" / "narrator_rescue_review_assets"

STYLE_KEYS = (
    "neutral",
    "grief",
    "panic",
    "angry",
    "whisper",
    "laughing",
)
BASELINE_MODEL_KEYS = (
    "indextts2",
    "voxcpm2",
    "fish_s2_pro",
)
LORA_CANDIDATE_KEY = "qwen_lora_narrator_attention_r8"
QWEN_BASE_CANDIDATE_KEY = "qwen3_tts"
CANDIDATE_LABELS = {
    "indextts2": "IndexTTS2",
    "voxcpm2": "VoxCPM2",
    "fish_s2_pro": "Fish Audio S2 Pro",
    QWEN_BASE_CANDIDATE_KEY: "Qwen3-TTS Base clone",
    LORA_CANDIDATE_KEY: "Qwen3-TTS Narrator Attention R8 pilot",
}
CANDIDATE_STRATEGIES = {
    "indextts2": "controlled_clone",
    "voxcpm2": "controlled_clone",
    "fish_s2_pro": "controlled_clone",
    QWEN_BASE_CANDIDATE_KEY: "zero_shot_clone",
    LORA_CANDIDATE_KEY: "trained_adapter_clone",
}
REQUIRED_REVIEW_FIELDS = (
    "identity_1_to_5",
    "delivery_1_to_5",
    "naturalness_1_to_5",
    "artifact_severity_1_to_5",
    "spoken_text_matches_expected",
    "requested_mode_is_clear",
    "approve_for_comparison",
)
PUBLIC_ASSET_FILES = ("index.html", "styles.css", "app.js")
MODEL_LEAK_TERMS = (
    "IndexTTS",
    "VoxCPM",
    "Qwen3",
    "Fish Audio",
    "MOSS-TTS",
    "Chatterbox",
    "LoRA",
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_data_js(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "window.ALEXANDRIA_NARRATOR_RESCUE_DATA = "
        + json.dumps(payload, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )


def link_or_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        target.unlink()
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def stable_ids(style: str, candidate_key: str) -> tuple[str, str]:
    token = sha256_text(f"{ROUND_ID}|{style}|{candidate_key}")
    return f"r2_{token[:20]}", token[20:36]


def wav_metrics(path: Path) -> dict[str, Any]:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = audio.mean(axis=1)
    duration = len(mono) / int(sample_rate) if sample_rate else 0.0
    rms = float(np.sqrt(np.mean(mono * mono))) if len(mono) else 0.0
    peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
    return {
        "sample_rate": int(sample_rate),
        "channels": int(audio.shape[1]),
        "frames": int(audio.shape[0]),
        "duration_seconds": duration,
        "rms": rms,
        "peak": peak,
    }


def complete_review_row(row: dict[str, Any]) -> bool:
    return all(field in row for field in REQUIRED_REVIEW_FIELDS)


def mean(rows: Iterable[dict[str, Any]], field: str) -> float | None:
    values = [float(row[field]) for row in rows if row.get(field) is not None]
    return round(sum(values) / len(values), 3) if values else None


def percent(rows: list[dict[str, Any]], predicate) -> float | None:
    return round(100.0 * sum(1 for row in rows if predicate(row)) / len(rows), 1) if rows else None


def aggregate_review_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "n": len(rows),
        "identity_mean": mean(rows, "identity_1_to_5"),
        "delivery_mean": mean(rows, "delivery_1_to_5"),
        "naturalness_mean": mean(rows, "naturalness_1_to_5"),
        "artifact_severity_mean": mean(rows, "artifact_severity_1_to_5"),
        "text_match_percent": percent(
            rows, lambda row: row.get("spoken_text_matches_expected") is True
        ),
        "mode_clear_percent": percent(
            rows, lambda row: row.get("requested_mode_is_clear") is True
        ),
        "approved_percent": percent(
            rows, lambda row: row.get("approve_for_comparison") is True
        ),
        "strict_success_percent": percent(
            rows,
            lambda row: (
                row.get("identity_1_to_5", 0) >= 4
                and row.get("delivery_1_to_5", 0) >= 4
                and row.get("naturalness_1_to_5", 0) >= 4
                and row.get("artifact_severity_1_to_5", 6) <= 2
                and row.get("spoken_text_matches_expected") is True
                and row.get("requested_mode_is_clear") is True
                and row.get("approve_for_comparison") is True
            ),
        ),
    }


def analyze_round1(round1_root: Path, results_path: Path) -> dict[str, Any]:
    results = read_json(results_path)
    answer_by_id: dict[str, dict[str, Any]] = {}
    for answer_path in sorted((round1_root / "review" / "answer-keys").glob("*.json")):
        for answer in json.loads(answer_path.read_text(encoding="utf-8")):
            answer_by_id[answer["sample_id"]] = answer

    mapped: list[dict[str, Any]] = []
    for source_row in results.get("rows", []):
        answer = answer_by_id.get(source_row.get("sample_id"))
        if answer is None:
            continue
        row = {**source_row, **answer}
        if complete_review_row(row):
            mapped.append(row)

    core = [
        row
        for row in mapped
        if row["identity_key"] in {"narrator", "benny", "doctor"}
    ]
    narrator = [row for row in mapped if row["identity_key"] == "narrator"]

    by_model = {
        key: aggregate_review_rows([row for row in core if row["model_key"] == key])
        for key in sorted({row["model_key"] for row in core})
    }
    narrator_by_model = {
        key: aggregate_review_rows(
            [row for row in narrator if row["model_key"] == key]
        )
        for key in sorted({row["model_key"] for row in narrator})
    }
    by_style = {
        key: aggregate_review_rows([row for row in core if row["style"] == key])
        for key in STYLE_KEYS
    }
    coverage = collections.Counter(row["identity_key"] for row in mapped)
    return {
        "schema_version": 1,
        "round_id": results.get("round_id"),
        "source_results": str(results_path),
        "source_results_sha256": sha256_file(results_path),
        "source_summary": results.get("summary"),
        "mapped_complete_row_count": len(mapped),
        "core_identity_complete_row_count": len(core),
        "coverage_by_identity": dict(sorted(coverage.items())),
        "overall_core": aggregate_review_rows(core),
        "narrator": aggregate_review_rows(narrator),
        "by_model_core": by_model,
        "narrator_by_model": narrator_by_model,
        "by_style_core": by_style,
        "decision": {
            "round1_validated_current_clone_architecture": False,
            "primary_failure": (
                "Clean natural audio did not reliably preserve Narrator identity "
                "and requested delivery simultaneously."
            ),
            "round2_question": (
                "Does the existing narrator adapter beat the viable clone "
                "baselines on the same six discriminative lines?"
            ),
        },
    }


def resolve_round1_reference(round1_root: Path, sample: dict[str, Any]) -> dict[str, Any]:
    reference = sample["reference"]
    result: dict[str, Any] = {
        "review_name": sample["identity_review_name"],
        "kind": sample["identity_kind"],
        "conditioning_transcript": reference.get("conditioning_transcript"),
        "conditioning_transcript_sha256": reference.get(
            "conditioning_transcript_sha256"
        ),
    }
    for public_key, source_key, hash_key in (
        ("original_audio", "source_file", "source_sha256"),
        ("conditioning_audio", "conditioning_file", "conditioning_sha256"),
    ):
        relative = reference.get(source_key)
        if not relative:
            continue
        source = (round1_root / "references" / relative).resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        expected = reference.get(hash_key)
        actual = sha256_file(source)
        if expected and expected != actual:
            raise RuntimeError(f"Reference hash mismatch: {source}")
        result[public_key] = {
            "source": str(source),
            "sha256": actual,
        }
    return result


def baseline_spec(
    round1_root: Path,
    source_sample: dict[str, Any],
    destination: Path,
) -> dict[str, Any]:
    candidate_key = source_sample["model_key"]
    style = source_sample["style"]
    sample_id, blind_id = stable_ids(style, candidate_key)
    source_audio = round1_root / source_sample["output_file"]
    source_receipt = round1_root / source_sample["result_file"]
    if not source_audio.is_file() or not source_receipt.is_file():
        raise FileNotFoundError(source_audio if not source_audio.is_file() else source_receipt)
    source_payload = read_json(source_receipt)
    source_audio_sha = sha256_file(source_audio)
    if source_payload.get("audio_sha256") != source_audio_sha:
        raise RuntimeError(f"Round 1 source receipt mismatch: {source_sample['sample_id']}")

    output_rel = Path("outputs") / candidate_key / style / f"{sample_id}.wav"
    receipt_rel = output_rel.with_suffix(".json")
    target_audio = destination / output_rel
    link_or_copy(source_audio, target_audio)
    receipt = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "sample_id": sample_id,
        "blind_id": blind_id,
        "candidate_key": candidate_key,
        "candidate_label": CANDIDATE_LABELS[candidate_key],
        "strategy": CANDIDATE_STRATEGIES[candidate_key],
        "identity_key": "narrator",
        "style": style,
        "audio_file": output_rel.as_posix(),
        "audio_sha256": source_audio_sha,
        "audio": wav_metrics(target_audio),
        "source_round_id": source_payload.get("round_id"),
        "source_sample_id": source_sample["sample_id"],
        "source_blind_id": source_sample["blind_id"],
        "source_audio_file": source_sample["output_file"],
        "source_audio_sha256": source_audio_sha,
        "source_receipt_file": source_sample["result_file"],
        "source_receipt_sha256": sha256_file(source_receipt),
        "generated_for_round2": False,
        "production_promotion_allowed": False,
    }
    write_json(destination / receipt_rel, receipt)
    return {
        "sample_id": sample_id,
        "blind_id": blind_id,
        "candidate_key": candidate_key,
        "candidate_label": CANDIDATE_LABELS[candidate_key],
        "strategy": CANDIDATE_STRATEGIES[candidate_key],
        "identity_key": "narrator",
        "identity_review_name": source_sample["identity_review_name"],
        "style": style,
        "style_label": source_sample["style_label"],
        "target_text": source_sample["target_text"],
        "instruction": source_sample["control"]["requested_instruction"],
        "seed": source_sample["seed"],
        "output_file": output_rel.as_posix(),
        "result_file": receipt_rel.as_posix(),
        "status": "ready",
        "source_round1_sample_id": source_sample["sample_id"],
    }


def lora_artifact_contract(model_path: Path) -> dict[str, Any]:
    required = {
        "model": model_path / "model.safetensors",
        "manifest": model_path / "mlx_export_manifest.json",
        "reference_audio": model_path / "ref_sample.wav",
        "reference_text": model_path / "ref_sample.txt",
    }
    for path in required.values():
        if not path.is_file():
            raise FileNotFoundError(path)
    ref_text = required["reference_text"].read_text(encoding="utf-8").strip()
    if not ref_text:
        raise RuntimeError("The LoRA pilot reference transcript is empty.")
    return {
        "model_path": str(model_path),
        "model_sha256": sha256_file(required["model"]),
        "manifest_sha256": sha256_file(required["manifest"]),
        "reference_audio": str(required["reference_audio"]),
        "reference_audio_sha256": sha256_file(required["reference_audio"]),
        "reference_text": ref_text,
        "reference_text_sha256": sha256_text(ref_text),
    }


def lora_fingerprint(sample: dict[str, Any], artifact: dict[str, Any]) -> str:
    return sha256_text(
        canonical_json(
            {
                "round_id": ROUND_ID,
                "candidate_key": sample["candidate_key"],
                "style": sample["style"],
                "target_text": sample["target_text"],
                "instruction": sample["instruction"],
                "seed": sample["seed"],
                "artifact": {
                    "model_sha256": artifact["model_sha256"],
                    "manifest_sha256": artifact["manifest_sha256"],
                    "reference_audio_sha256": artifact["reference_audio_sha256"],
                    "reference_text_sha256": artifact["reference_text_sha256"],
                },
                "generation": {
                    "temperature": 0.75,
                    "top_k": 50,
                    "top_p": 0.95,
                    "repetition_penalty": 1.5,
                    "max_tokens": 1200,
                },
            }
        )
    )


def valid_lora_result(
    destination: Path,
    sample: dict[str, Any],
    artifact: dict[str, Any],
) -> bool:
    audio = destination / sample["output_file"]
    receipt = destination / sample["result_file"]
    if not audio.is_file() or not receipt.is_file():
        return False
    payload = read_json(receipt)
    return (
        payload.get("sample_fingerprint") == lora_fingerprint(sample, artifact)
        and payload.get("audio_sha256") == sha256_file(audio)
        and payload.get("blind_id") == sample["blind_id"]
    )


def prepare(
    round1_root: Path,
    results_path: Path,
    destination: Path,
    lora_model: Path,
    force: bool,
) -> dict[str, Any]:
    if destination.exists() and force:
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)

    internal = read_json(round1_root / "round1_internal_manifest.json")
    if internal.get("round_id") != "alexandria_multimodel_expressive_clone_round1_v2_usable":
        raise RuntimeError("The selected source is not the corrected Round 1 v2 evidence.")
    if not results_path.is_file():
        raise FileNotFoundError(results_path)

    style_by_key = {style["key"]: style for style in internal["styles"]}
    missing_styles = [key for key in STYLE_KEYS if key not in style_by_key]
    if missing_styles:
        raise RuntimeError(f"Round 1 styles missing: {missing_styles}")

    source_by_model_style: dict[tuple[str, str], dict[str, Any]] = {}
    for sample in internal["sample_specs"]:
        if sample["identity_key"] != "narrator" or sample["style"] not in STYLE_KEYS:
            continue
        key = (sample["model_key"], sample["style"])
        if key in source_by_model_style:
            raise RuntimeError(f"Duplicate Round 1 Narrator cell: {key}")
        source_by_model_style[key] = sample

    expected_source_cells = [
        (model_key, style)
        for model_key in BASELINE_MODEL_KEYS
        for style in STYLE_KEYS
    ] + [(QWEN_BASE_CANDIDATE_KEY, "neutral")]
    missing_cells = [key for key in expected_source_cells if key not in source_by_model_style]
    if missing_cells:
        raise RuntimeError(f"Round 1 source cells missing: {missing_cells}")

    narrator_neutral = source_by_model_style[("indextts2", "neutral")]
    reference = resolve_round1_reference(round1_root, narrator_neutral)
    public_reference: dict[str, Any] = {
        "review_name": reference["review_name"],
        "kind": reference["kind"],
        "conditioning_transcript": reference.get("conditioning_transcript"),
        "conditioning_transcript_sha256": reference.get(
            "conditioning_transcript_sha256"
        ),
    }
    for key in ("original_audio", "conditioning_audio"):
        item = reference.get(key)
        if not item:
            continue
        suffix = Path(item["source"]).suffix.lower() or ".wav"
        target = destination / "reference" / f"narrator-{key.replace('_audio', '')}{suffix}"
        link_or_copy(Path(item["source"]), target)
        public_reference[key] = {
            "file": target.relative_to(destination).as_posix(),
            "sha256": item["sha256"],
        }
    write_json(destination / "reference.json", public_reference)

    input_copy = destination / "inputs" / results_path.name
    link_or_copy(results_path, input_copy)
    analysis = analyze_round1(round1_root, results_path)
    write_json(destination / "round1-v2-results-analysis.json", analysis)

    sample_specs: list[dict[str, Any]] = []
    for model_key, style in expected_source_cells:
        sample_specs.append(
            baseline_spec(
                round1_root,
                source_by_model_style[(model_key, style)],
                destination,
            )
        )

    artifact = lora_artifact_contract(lora_model)
    for index, style_key in enumerate(STYLE_KEYS):
        style = style_by_key[style_key]
        sample_id, blind_id = stable_ids(style_key, LORA_CANDIDATE_KEY)
        output_rel = Path("outputs") / LORA_CANDIDATE_KEY / style_key / f"{sample_id}.wav"
        receipt_rel = output_rel.with_suffix(".json")
        sample = {
            "sample_id": sample_id,
            "blind_id": blind_id,
            "candidate_key": LORA_CANDIDATE_KEY,
            "candidate_label": CANDIDATE_LABELS[LORA_CANDIDATE_KEY],
            "strategy": CANDIDATE_STRATEGIES[LORA_CANDIDATE_KEY],
            "identity_key": "narrator",
            "identity_review_name": narrator_neutral["identity_review_name"],
            "style": style_key,
            "style_label": style["label"],
            "target_text": style["target_text"],
            "instruction": style["instruction"],
            "seed": 2026072300 + index,
            "output_file": output_rel.as_posix(),
            "result_file": receipt_rel.as_posix(),
            "status": "pending_generation",
        }
        if valid_lora_result(destination, sample, artifact):
            sample["status"] = "ready"
        sample_specs.append(sample)

    style_payloads = [
        {
            "key": key,
            "label": style_by_key[key]["label"],
            "group": style_by_key[key]["group"],
            "target_text": style_by_key[key]["target_text"],
            "instruction": style_by_key[key]["instruction"],
        }
        for key in STYLE_KEYS
    ]
    manifest = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "purpose": "narrator_training_rescue_gate",
        "created_at": utc_now(),
        "source_round1_root": str(round1_root),
        "source_round1_id": internal["round_id"],
        "source_results": str(results_path),
        "source_results_sha256": sha256_file(results_path),
        "destination": str(destination),
        "identity_key": "narrator",
        "identity_review_name": narrator_neutral["identity_review_name"],
        "styles": style_payloads,
        "style_order": list(STYLE_KEYS),
        "candidate_contract": {
            "controlled_clone_models": list(BASELINE_MODEL_KEYS),
            "zero_shot_clone_models": [QWEN_BASE_CANDIDATE_KEY],
            "trained_adapter_models": [LORA_CANDIDATE_KEY],
            "qwen_base_limited_to_neutral": True,
            "native_voices_included": False,
        },
        "lora_artifact": artifact,
        "sample_spec_count": len(sample_specs),
        "sample_specs": sample_specs,
        "production_promotion_allowed": False,
        "decision_gate": {
            "strict_success_definition": (
                "identity>=4, delivery>=4, naturalness>=4, artifacts<=2, "
                "text correct, mode clear, and approved"
            ),
            "minimum_adapter_success_to_justify_reviewed_training": (
                "Adapter must materially outperform the controlled clone baselines "
                "on Narrator identity without losing delivery."
            ),
        },
    }
    write_json(destination / "round2_internal_manifest.json", manifest)
    return {
        "destination": str(destination),
        "sample_spec_count": len(sample_specs),
        "ready_count": sum(sample["status"] == "ready" for sample in sample_specs),
        "pending_lora_count": sum(
            sample["status"] != "ready"
            and sample["candidate_key"] == LORA_CANDIDATE_KEY
            for sample in sample_specs
        ),
        "style_count": len(STYLE_KEYS),
    }


def generate_lora(destination: Path) -> dict[str, Any]:
    from mlx_backend import MLXBackend
    import mlx.core as mx

    manifest_path = destination / "round2_internal_manifest.json"
    manifest = read_json(manifest_path)
    artifact = manifest["lora_artifact"]
    model_path = Path(artifact["model_path"])
    backend = MLXBackend(language="English")
    generated = 0
    reused = 0
    failures: list[dict[str, Any]] = []
    try:
        for sample in manifest["sample_specs"]:
            if sample["candidate_key"] != LORA_CANDIDATE_KEY:
                continue
            if valid_lora_result(destination, sample, artifact):
                sample["status"] = "ready"
                reused += 1
                continue
            output = destination / sample["output_file"]
            receipt = destination / sample["result_file"]
            output.parent.mkdir(parents=True, exist_ok=True)
            receipt.parent.mkdir(parents=True, exist_ok=True)
            lock = output.with_suffix(output.suffix + ".lock")
            partial = output.with_name(output.stem + f".{os.getpid()}.partial.wav")
            if lock.exists():
                raise RuntimeError(f"A generation lock already exists: {lock}")
            lock.write_text(
                json.dumps({"pid": os.getpid(), "started_at": utc_now()}) + "\n",
                encoding="utf-8",
            )
            try:
                partial.unlink(missing_ok=True)
                mx.random.seed(int(sample["seed"]))
                started = time.perf_counter()
                ok = backend.generate_merged_lora_clone(
                    text=sample["target_text"],
                    ref_audio=artifact["reference_audio"],
                    ref_text=artifact["reference_text"],
                    instruct=sample["instruction"],
                    model_path=str(model_path),
                    output_path=str(partial),
                    temperature=0.75,
                    top_k=50,
                    top_p=0.95,
                    repetition_penalty=1.5,
                    max_tokens=1200,
                )
                elapsed = time.perf_counter() - started
                if not ok or not partial.is_file():
                    raise RuntimeError("The merged LoRA runtime returned no audio.")
                metrics = wav_metrics(partial)
                if metrics["channels"] != 1:
                    raise RuntimeError(f"LoRA output is not mono: {metrics}")
                if metrics["sample_rate"] != 24000:
                    raise RuntimeError(f"Unexpected LoRA sample rate: {metrics}")
                if not 1.0 <= metrics["duration_seconds"] <= 20.0:
                    raise RuntimeError(f"Implausible LoRA duration: {metrics}")
                audio_sha = sha256_file(partial)
                os.replace(partial, output)
                payload = {
                    "schema_version": 1,
                    "round_id": ROUND_ID,
                    "sample_id": sample["sample_id"],
                    "blind_id": sample["blind_id"],
                    "candidate_key": sample["candidate_key"],
                    "candidate_label": sample["candidate_label"],
                    "strategy": sample["strategy"],
                    "identity_key": sample["identity_key"],
                    "style": sample["style"],
                    "target_text_sha256": sha256_text(sample["target_text"]),
                    "instruction_sha256": sha256_text(sample["instruction"]),
                    "seed": sample["seed"],
                    "sample_fingerprint": lora_fingerprint(sample, artifact),
                    "model_path": artifact["model_path"],
                    "model_sha256": artifact["model_sha256"],
                    "model_manifest_sha256": artifact["manifest_sha256"],
                    "reference_audio_sha256": artifact["reference_audio_sha256"],
                    "reference_text_sha256": artifact["reference_text_sha256"],
                    "generation": {
                        "temperature": 0.75,
                        "top_k": 50,
                        "top_p": 0.95,
                        "repetition_penalty": 1.5,
                        "max_tokens": 1200,
                    },
                    "generation_seconds": elapsed,
                    "real_time_factor": elapsed / metrics["duration_seconds"],
                    "audio_file": sample["output_file"],
                    "audio_sha256": audio_sha,
                    "audio": metrics,
                    "generated_at": utc_now(),
                    "generated_for_round2": True,
                    "production_promotion_allowed": False,
                }
                write_json(receipt, payload)
                sample["status"] = "ready"
                write_json(manifest_path, manifest)
                generated += 1
                print(
                    json.dumps(
                        {
                            "sample": sample["blind_id"],
                            "style": sample["style"],
                            "duration": round(metrics["duration_seconds"], 3),
                            "rtf": round(payload["real_time_factor"], 3),
                        }
                    ),
                    flush=True,
                )
            except BaseException as exc:
                failures.append(
                    {
                        "sample_id": sample["sample_id"],
                        "style": sample["style"],
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:3000],
                    }
                )
                sample["status"] = "generation_failed"
                write_json(manifest_path, manifest)
                raise
            finally:
                partial.unlink(missing_ok=True)
                lock.unlink(missing_ok=True)
    finally:
        backend.release_models_manually()
    return {
        "generated_count": generated,
        "reused_count": reused,
        "failure_count": len(failures),
        "failures": failures,
    }


def package(destination: Path) -> dict[str, Any]:
    manifest = read_json(destination / "round2_internal_manifest.json")
    pending = [
        sample["sample_id"]
        for sample in manifest["sample_specs"]
        if sample["status"] != "ready"
    ]
    if pending:
        raise RuntimeError(f"Round 2 still has pending samples: {pending}")

    review_root = destination / "review"
    if review_root.exists():
        shutil.rmtree(review_root)
    review_root.mkdir(parents=True)
    audio_root = review_root / "audio"
    audio_root.mkdir(parents=True)

    reference = read_json(destination / "reference.json")
    public_reference = {
        "review_name": reference["review_name"],
        "kind": reference["kind"],
        "conditioning_transcript": reference.get("conditioning_transcript"),
    }
    for key in ("original_audio", "conditioning_audio"):
        item = reference.get(key)
        if not item:
            continue
        source = destination / item["file"]
        target = review_root / "reference-audio" / source.name
        link_or_copy(source, target)
        public_reference[key] = target.relative_to(review_root).as_posix()

    public_samples: list[dict[str, Any]] = []
    answer_rows: list[dict[str, Any]] = []
    for sample in manifest["sample_specs"]:
        source = destination / sample["output_file"]
        receipt = read_json(destination / sample["result_file"])
        current_hash = sha256_file(source)
        if receipt.get("audio_sha256") != current_hash:
            raise RuntimeError(f"Receipt mismatch while packaging: {sample['sample_id']}")
        target = audio_root / f"{sample['blind_id']}.wav"
        link_or_copy(source, target)
        public_samples.append(
            {
                "sample_id": sample["blind_id"],
                "style": sample["style"],
                "style_label": sample["style_label"],
                "expected_identity": sample["identity_review_name"],
                "target_text": sample["target_text"],
                "requested_instruction": sample["instruction"],
                "audio": target.relative_to(review_root).as_posix(),
                "audio_sha256": current_hash,
                "status": "ready",
            }
        )
        answer_rows.append(
            {
                "sample_id": sample["blind_id"],
                "source_sample_id": sample["sample_id"],
                "candidate_key": sample["candidate_key"],
                "candidate_label": sample["candidate_label"],
                "strategy": sample["strategy"],
                "style": sample["style"],
                "identity_key": sample["identity_key"],
                "audio_sha256": current_hash,
                "result_file": sample["result_file"],
                "source_round1_sample_id": sample.get("source_round1_sample_id"),
            }
        )

    order = {key: index for index, key in enumerate(manifest["style_order"])}
    public_samples.sort(
        key=lambda sample: (
            order[sample["style"]],
            sha256_text(
                f"{ROUND_ID}|candidate-order|{sample['style']}|{sample['sample_id']}"
            ),
        )
    )
    public = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "title": "Alexandria Narrator Rescue — Blind Round 2",
        "identity": public_reference,
        "styles": manifest["styles"],
        "style_order": manifest["style_order"],
        "samples": public_samples,
        "review_fields": list(REQUIRED_REVIEW_FIELDS)
        + ["flag_for_follow_up", "notes"],
        "candidate_count": len(public_samples),
        "production_promotion_allowed": False,
    }
    write_data_js(review_root / "data.js", public)
    write_json(destination / "answer-key.json", answer_rows)

    for filename in PUBLIC_ASSET_FILES:
        source = ASSET_ROOT / filename
        if not source.is_file():
            raise FileNotFoundError(source)
        shutil.copy2(source, review_root / filename)

    style_counts = collections.Counter(sample["style"] for sample in public_samples)
    review_manifest = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "review": "index.html",
        "identity_count": 1,
        "style_count": len(manifest["style_order"]),
        "candidate_count": len(public_samples),
        "candidate_counts_by_style": {
            key: style_counts[key] for key in manifest["style_order"]
        },
        "model_names_public": False,
        "answer_key_outside_review_root": True,
        "autosave": True,
        "partial_import_merge": True,
        "style_and_cumulative_exports": True,
        "keyboard_style_navigation": True,
        "production_promotion_allowed": False,
    }
    write_json(review_root / "manifest.json", review_manifest)
    (review_root / "START_HERE.txt").write_text(
        "ALEXANDRIA NARRATOR RESCUE — ROUND 2\n\n"
        "Serve this folder over localhost, then open index.html in a browser.\n"
        "Review one performance style at a time. Candidate identities are blinded.\n"
        "Export cumulative results when finished.\n",
        encoding="utf-8",
    )
    return {
        "review": str(review_root / "index.html"),
        "candidate_count": len(public_samples),
        "candidate_counts_by_style": review_manifest[
            "candidate_counts_by_style"
        ],
        "answer_key": str(destination / "answer-key.json"),
    }


def parse_public_data(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8").strip()
    prefix = "window.ALEXANDRIA_NARRATOR_RESCUE_DATA = "
    if not text.startswith(prefix) or not text.endswith(";"):
        raise ValueError(f"Unexpected data.js format: {path}")
    return json.loads(text[len(prefix) : -1])


def validate(destination: Path) -> dict[str, Any]:
    manifest = read_json(destination / "round2_internal_manifest.json")
    review_root = destination / "review"
    review_manifest = read_json(review_root / "manifest.json")
    public = parse_public_data(review_root / "data.js")
    answer_rows = read_json(destination / "answer-key.json")

    errors: list[str] = []
    sample_specs = manifest["sample_specs"]
    if len(sample_specs) != 25:
        errors.append(f"Expected 25 samples, found {len(sample_specs)}")
    if len(public["samples"]) != len(sample_specs):
        errors.append("Public sample count does not match internal manifest")
    if len(answer_rows) != len(sample_specs):
        errors.append("Answer key count does not match internal manifest")

    candidate_counts = collections.Counter(
        sample["candidate_key"] for sample in sample_specs
    )
    expected_candidate_counts = {
        "indextts2": 6,
        "voxcpm2": 6,
        "fish_s2_pro": 6,
        QWEN_BASE_CANDIDATE_KEY: 1,
        LORA_CANDIDATE_KEY: 6,
    }
    if dict(candidate_counts) != expected_candidate_counts:
        errors.append(f"Candidate counts mismatch: {dict(candidate_counts)}")

    style_counts = collections.Counter(sample["style"] for sample in sample_specs)
    expected_style_counts = {key: 4 for key in STYLE_KEYS}
    expected_style_counts["neutral"] = 5
    if dict(style_counts) != expected_style_counts:
        errors.append(f"Style counts mismatch: {dict(style_counts)}")

    sample_by_blind = {sample["blind_id"]: sample for sample in sample_specs}
    durations: list[float] = []
    sample_rates: set[int] = set()
    channels: set[int] = set()
    for sample in sample_specs:
        audio = destination / sample["output_file"]
        receipt = destination / sample["result_file"]
        if sample["status"] != "ready" or not audio.is_file() or not receipt.is_file():
            errors.append(f"Missing ready sample: {sample['sample_id']}")
            continue
        payload = read_json(receipt)
        if payload.get("audio_sha256") != sha256_file(audio):
            errors.append(f"Audio hash mismatch: {sample['sample_id']}")
        metrics = wav_metrics(audio)
        durations.append(metrics["duration_seconds"])
        sample_rates.add(metrics["sample_rate"])
        channels.add(metrics["channels"])
        if metrics["channels"] != 1:
            errors.append(f"Non-mono sample: {sample['sample_id']}")
        if not 1.0 <= metrics["duration_seconds"] <= 20.0:
            errors.append(f"Implausible duration: {sample['sample_id']}")

    public_text = "\n".join(
        (review_root / filename).read_text(encoding="utf-8", errors="ignore")
        for filename in (*PUBLIC_ASSET_FILES, "data.js", "manifest.json", "START_HERE.txt")
    )
    leaks = [term for term in MODEL_LEAK_TERMS if term.casefold() in public_text.casefold()]
    if leaks:
        errors.append(f"Model-name leakage in public package: {leaks}")
    if "answer-key.json" in public_text:
        errors.append("Public package references the answer key")

    public_order: dict[str, list[str]] = collections.defaultdict(list)
    for sample in public["samples"]:
        public_order[sample["style"]].append(sample["sample_id"])
        if sample["sample_id"] not in sample_by_blind:
            errors.append(f"Unknown public sample: {sample['sample_id']}")
    lora_ordinals = []
    for style in STYLE_KEYS:
        lora_blind = next(
            sample["blind_id"]
            for sample in sample_specs
            if sample["style"] == style
            and sample["candidate_key"] == LORA_CANDIDATE_KEY
        )
        lora_ordinals.append(public_order[style].index(lora_blind) + 1)
    if len(set(lora_ordinals)) < 2:
        errors.append("Trained candidate occupies the same ordinal in every style")

    stale = [
        str(path.relative_to(destination))
        for path in destination.rglob("*")
        if path.is_file()
        and (path.name.endswith(".lock") or ".partial" in path.name)
    ]
    if stale:
        errors.append(f"Stale generation files remain: {stale}")
    if review_manifest.get("candidate_count") != len(sample_specs):
        errors.append("Review manifest candidate count is incorrect")

    if errors:
        raise RuntimeError("Round 2 validation failed:\n- " + "\n- ".join(errors))
    return {
        "round_id": ROUND_ID,
        "sample_count": len(sample_specs),
        "style_count": len(STYLE_KEYS),
        "candidate_counts": expected_candidate_counts,
        "style_counts": expected_style_counts,
        "sample_rates": sorted(sample_rates),
        "channels": sorted(channels),
        "minimum_duration_seconds": min(durations),
        "maximum_duration_seconds": max(durations),
        "lora_candidate_ordinals": lora_ordinals,
        "model_name_leak_count": 0,
        "stale_file_count": 0,
        "review": str(review_root / "index.html"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=("prepare", "generate-lora", "package", "validate", "all"),
    )
    parser.add_argument("--round1-root", default=str(DEFAULT_ROUND1))
    parser.add_argument("--results", default=str(DEFAULT_RESULTS))
    parser.add_argument("--destination", default=str(DEFAULT_DESTINATION))
    parser.add_argument("--lora-model", default=str(DEFAULT_LORA_MODEL))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    round1_root = Path(args.round1_root).expanduser().resolve()
    results_path = Path(args.results).expanduser().resolve()
    destination = Path(args.destination).expanduser().resolve()
    lora_model = Path(args.lora_model).expanduser().resolve()

    if args.mode == "prepare":
        result = prepare(
            round1_root,
            results_path,
            destination,
            lora_model,
            args.force,
        )
    elif args.mode == "generate-lora":
        result = generate_lora(destination)
    elif args.mode == "package":
        result = package(destination)
    elif args.mode == "validate":
        result = validate(destination)
    else:
        result = {
            "prepare": prepare(
                round1_root,
                results_path,
                destination,
                lora_model,
                args.force,
            ),
            "generate_lora": generate_lora(destination),
            "package": package(destination),
            "validate": validate(destination),
        }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
