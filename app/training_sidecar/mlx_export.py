#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import mlx.core as mx
import numpy as np
import soundfile as sf
from mlx_audio.tts.utils import convert

from audio_processing import decode_audio_mono
from generation_state import fingerprint_value
from instruction_propagation import (
    INSTRUCTION_PLACEMENT,
    InstructionPropagationError,
    build_instruction_propagation_contract,
    instruction_identity,
    validate_instruction_propagation_contract,
)
from mlx_backend import MLXBackend


SCHEMA_VERSION = 1
ARTIFACT_FORMAT = "merged_mlx_qwen_checkpoint"
INSTRUCTION_PATCH_VERSION = 1


class MLXExportError(RuntimeError):
    pass


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def file_inventory(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise MLXExportError(f"{label} is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MLXExportError(f"{label} could not be read: {exc}") from exc
    if not isinstance(value, dict):
        raise MLXExportError(f"{label} must be a JSON object.")
    return value


def _speaker_embedding(model, path: Path) -> np.ndarray:
    audio, _ = decode_audio_mono(path, sample_rate=24000)
    embedding = np.asarray(
        model.extract_speaker_embedding(mx.array(audio), sr=24000)
    ).reshape(-1)
    return embedding / (np.linalg.norm(embedding) + 1e-12)


def _cosine(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.dot(left, right))


def _strip_copied_pytorch_weights(output: Path) -> list[str]:
    removed = []
    for path in sorted(output.glob("model-*-of-*.safetensors")):
        removed.append(path.name)
        path.unlink()
    index = output / "model.safetensors.index.json"
    if index.is_file():
        removed.append(index.name)
        index.unlink()
    return removed


def _convert_checkpoint(
    *,
    merged: Path,
    output: Path,
    q_group_size: int,
    q_bits: int,
) -> dict[str, Any]:
    tokenizer = merged / "speech_tokenizer"
    if not tokenizer.is_dir():
        raise MLXExportError(
            "Merged checkpoint is missing speech_tokenizer/."
        )
    parked = merged.parent / (
        f".{merged.name}.speech-tokenizer.{uuid.uuid4().hex}"
    )
    started = time.perf_counter()
    shutil.move(str(tokenizer), str(parked))
    try:
        convert(
            hf_path=str(merged),
            mlx_path=str(output),
            quantize=True,
            q_group_size=q_group_size,
            q_bits=q_bits,
            dtype="bfloat16",
        )
        shutil.copytree(
            parked,
            output / "speech_tokenizer",
            dirs_exist_ok=True,
        )
    finally:
        if tokenizer.exists():
            shutil.rmtree(tokenizer)
        shutil.move(str(parked), str(tokenizer))
    removed = _strip_copied_pytorch_weights(output)
    model_weights = output / "model.safetensors"
    if not model_weights.is_file():
        raise MLXExportError(
            "MLX conversion did not produce model.safetensors."
        )
    return {
        "conversion_seconds": time.perf_counter() - started,
        "q_group_size": q_group_size,
        "q_bits": q_bits,
        "removed_copied_pytorch_files": removed,
    }


def _generate_validation(
    *,
    output: Path,
    reference_audio: Path,
    reference_text: str,
    validation_text: str,
    neutral_instruction: str,
    expressive_instruction: str,
    max_tokens: int,
) -> dict[str, Any]:
    backend = MLXBackend(language="English")
    rows: dict[str, dict[str, Any]] = {}
    for style, instruction in (
        ("neutral", neutral_instruction),
        ("expressive", expressive_instruction),
    ):
        target = output / f"validation_{style}.wav"
        started = time.perf_counter()
        generated = backend.generate_merged_lora_clone(
            text=validation_text,
            ref_audio=str(reference_audio),
            ref_text=reference_text,
            instruct=instruction,
            model_path=str(output),
            output_path=str(target),
            max_tokens=max_tokens,
        )
        if generated is not True or not target.is_file():
            raise MLXExportError(
                f"MLX validation returned no {style} audio."
            )
        elapsed = time.perf_counter() - started
        info = sf.info(str(target))
        duration = float(info.duration)
        if duration <= 0:
            raise MLXExportError(
                f"MLX validation produced empty {style} audio."
            )
        rows[style] = {
            **instruction_identity(instruction),
            "instruction_placement": INSTRUCTION_PLACEMENT,
            "audio_sha256": sha256_file(target),
            "elapsed_seconds": elapsed,
            "audio_duration_seconds": duration,
            "real_time_factor": elapsed / duration,
        }

    model = backend._external_qwen_model(str(output))
    reference_embedding = _speaker_embedding(model, reference_audio)
    for style in rows:
        output_embedding = _speaker_embedding(
            model,
            output / f"validation_{style}.wav",
        )
        rows[style]["speaker_cosine_to_reference"] = _cosine(
            reference_embedding,
            output_embedding,
        )
    outputs_differ = (
        rows["neutral"]["audio_sha256"]
        != rows["expressive"]["audio_sha256"]
    )
    return {
        "validation_text_sha256": sha256_text(validation_text),
        "measurements": rows,
        "outputs_differ": outputs_differ,
        "speaker_similarity_floor": 0.95,
        "identity_passed": all(
            row["speaker_cosine_to_reference"] >= 0.95
            for row in rows.values()
        ),
        "steady_state_faster_than_real_time": all(
            row["real_time_factor"] <= 1.05
            for row in rows.values()
        ),
        "instruction_channel_changed_output": outputs_differ,
        "manual_audio_review_required": True,
        "manual_audio_review_status": "pending",
    }


def export_mlx_checkpoint(
    *,
    merged_dir: str | Path,
    output_dir: str | Path,
    validation_text: str,
    neutral_instruction: str,
    expressive_instruction: str,
    q_group_size: int = 64,
    q_bits: int = 8,
    max_tokens: int = 1200,
    cleanup_merged: bool = False,
) -> dict[str, Any]:
    merged = Path(merged_dir).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    if not merged.is_dir():
        raise MLXExportError(
            "Merged checkpoint directory does not exist."
        )
    if output.exists():
        if any(output.iterdir()):
            raise MLXExportError(
                "MLX output directory already contains files."
            )
        output.rmdir()
    if q_group_size <= 0 or q_bits not in {4, 8}:
        raise MLXExportError(
            "MLX quantization requires a positive group size and 4 or 8 bits."
        )
    if max_tokens < 128:
        raise MLXExportError("max_tokens must be at least 128.")
    for value, label in (
        (validation_text, "Validation text"),
        (neutral_instruction, "Neutral instruction"),
        (expressive_instruction, "Expressive instruction"),
    ):
        if not isinstance(value, str) or not value.strip():
            raise MLXExportError(f"{label} must be non-empty text.")

    merge_metrics = _load_json(
        merged / "merge_metrics.json",
        "Merge metrics",
    )
    raw_propagation = merge_metrics.get("instruction_propagation")
    try:
        instruction_propagation = (
            validate_instruction_propagation_contract(raw_propagation)
            if raw_propagation is not None
            else build_instruction_propagation_contract(
                mode="identity_only",
                samples=[],
            )
        )
    except InstructionPropagationError as exc:
        raise MLXExportError(
            f"Merged checkpoint instruction propagation is invalid: {exc}"
        ) from exc
    reference_audio = merged / "ref_sample.wav"
    reference_text_path = merged / "ref_sample.txt"
    if not reference_audio.is_file() or not reference_text_path.is_file():
        raise MLXExportError(
            "Merged checkpoint is missing its reference audio or transcript."
        )
    reference_text = reference_text_path.read_text(
        encoding="utf-8"
    ).strip()
    if not reference_text:
        raise MLXExportError("Reference transcript is empty.")

    temporary = output.parent / (
        f".{output.name}.exporting.{uuid.uuid4().hex}"
    )
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True)
    started = time.perf_counter()
    try:
        conversion = _convert_checkpoint(
            merged=merged,
            output=temporary,
            q_group_size=q_group_size,
            q_bits=q_bits,
        )
        shutil.copy2(reference_audio, temporary / "ref_sample.wav")
        shutil.copy2(reference_text_path, temporary / "ref_sample.txt")
        validation = _generate_validation(
            output=temporary,
            reference_audio=reference_audio,
            reference_text=reference_text,
            validation_text=validation_text.strip(),
            neutral_instruction=neutral_instruction.strip(),
            expressive_instruction=expressive_instruction.strip(),
            max_tokens=max_tokens,
        )
        technical_passed = bool(
            validation["identity_passed"]
            and validation["steady_state_faster_than_real_time"]
            and validation["instruction_channel_changed_output"]
        )
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "artifact_format": ARTIFACT_FORMAT,
            "status": (
                "validated_experimental"
                if technical_passed
                else "validation_failed"
            ),
            "created_at_utc": utc_timestamp(),
            "base_model": merge_metrics.get("base_model"),
            "base_model_revision": merge_metrics.get("base_model_revision"),
            "source_adapter_manifest_sha256": merge_metrics.get(
                "adapter_manifest_sha256"
            ),
            "instruction_patch_version": INSTRUCTION_PATCH_VERSION,
            "instruction_propagation": instruction_propagation,
            "reference_audio_sha256": sha256_file(reference_audio),
            "reference_text_sha256": sha256_text(reference_text),
            "conversion": conversion,
            "validation": validation,
            "technical_validation_passed": technical_passed,
            "production_assignment_supported": False,
            "production_blockers": [
                "Listen to both validation samples.",
                "Record identity, pronunciation, noise, pace, and delivery review.",
                "Run a multi-sample, multi-epoch quality comparison before assignment.",
            ],
            "files": [],
            "export_fingerprint": "",
        }
        inventory_before_manifest = file_inventory(temporary)
        manifest["files"] = inventory_before_manifest
        manifest["export_fingerprint"] = fingerprint_value(
            {
                key: value
                for key, value in manifest.items()
                if key != "export_fingerprint"
            }
        )
        (temporary / "mlx_export_manifest.json").write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    if cleanup_merged:
        shutil.rmtree(merged)
    final_size = sum(
        path.stat().st_size
        for path in output.rglob("*")
        if path.is_file()
    )
    return {
        "status": manifest["status"],
        "output_dir": str(output),
        "export_fingerprint": manifest["export_fingerprint"],
        "technical_validation_passed": technical_passed,
        "production_assignment_supported": False,
        "total_seconds": time.perf_counter() - started,
        "size_bytes": final_size,
        "validation": validation,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--merged-dir", required=True)
    result.add_argument("--output-dir", required=True)
    result.add_argument(
        "--validation-text",
        default=(
            "You have exactly one chance to tell me the truth before this "
            "becomes considerably more unpleasant."
        ),
    )
    result.add_argument(
        "--neutral-instruction",
        default=(
            "Calm, measured, conversational delivery. Preserve the original "
            "speaker identity and accent."
        ),
    )
    result.add_argument(
        "--expressive-instruction",
        default=(
            "Controlled anger, hard consonants, restrained intensity, "
            "slightly faster pace. Preserve the original speaker identity "
            "and accent."
        ),
    )
    result.add_argument("--q-group-size", type=int, default=64)
    result.add_argument("--q-bits", type=int, default=8)
    result.add_argument("--max-tokens", type=int, default=1200)
    result.add_argument("--cleanup-merged", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        result = export_mlx_checkpoint(
            merged_dir=args.merged_dir,
            output_dir=args.output_dir,
            validation_text=args.validation_text,
            neutral_instruction=args.neutral_instruction,
            expressive_instruction=args.expressive_instruction,
            q_group_size=args.q_group_size,
            q_bits=args.q_bits,
            max_tokens=args.max_tokens,
            cleanup_merged=args.cleanup_merged,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
