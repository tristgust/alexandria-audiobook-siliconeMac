from __future__ import annotations

import gc
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import random
import shutil
import sys
import time
import types
from pathlib import Path
from typing import Any, Iterable, Mapping


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from audio_processing import AudioProcessingError, decode_audio_mono, write_canonical_wav
from hf_access import snapshot_download_with_public_fallback
from instruction_propagation import (
    INSTRUCTION_PLACEMENT,
    InstructionPropagationError,
    build_instruction_propagation_contract,
    format_instruction_prompt,
    instruction_identity,
    normalize_instruction,
    normalize_instruction_mode,
    validate_instruction_propagation_contract,
)
from model_registry import is_registered_model, model_spec, resolve_model_path


DEFAULT_MODEL = model_spec("pytorch_qwen_base").repo_id
TRAINING_CHECKPOINT_SCHEMA_VERSION = 1
LORA_TARGET_PROFILES = {
    "attention": (
        "q_proj",
        "k_proj",
        "v_proj",
        "o_proj",
    ),
    "attention_mlp": (
        "q_proj",
        "k_proj",
        "v_proj",
        "o_proj",
        "gate_proj",
        "up_proj",
        "down_proj",
    ),
}
SUPPORTED_ARTIFACT_FORMATS = {
    "official_sft_full_checkpoint",
    "peft_lora_adapter",
}


class SidecarTrainingError(RuntimeError):
    pass


def utc_timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def lora_target_suffixes(profile: str = "attention_mlp") -> tuple[str, ...]:
    value = str(profile or "").strip().lower()
    try:
        return LORA_TARGET_PROFILES[value]
    except KeyError as exc:
        choices = ", ".join(sorted(LORA_TARGET_PROFILES))
        raise SidecarTrainingError(
            f"LoRA target profile must be one of: {choices}."
        ) from exc


def split_prepared_samples(
    samples: list[dict[str, Any]],
    *,
    validation_fraction: float = 0.1,
    seed: int = 1337,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if not 0.0 <= validation_fraction < 1.0:
        raise SidecarTrainingError(
            "validation_fraction must be at least 0 and less than 1."
        )
    if not samples:
        raise SidecarTrainingError("No prepared samples are available.")
    explicit_splits = [sample.get("split") for sample in samples]
    if any(value is not None for value in explicit_splits):
        allowed = {"train", "validation", "test"}
        if any(value not in allowed for value in explicit_splits):
            raise SidecarTrainingError(
                "Explicit dataset splits must be present on every sample and use train, validation, or test."
            )
        train_samples = [
            sample for sample in samples if sample.get("split") == "train"
        ]
        validation_samples = [
            sample
            for sample in samples
            if sample.get("split") == "validation"
        ]
        test_samples = [
            sample for sample in samples if sample.get("split") == "test"
        ]
        if not train_samples:
            raise SidecarTrainingError(
                "Explicit dataset splits require at least one train sample."
            )
        return (
            train_samples,
            validation_samples,
            {
                "strategy": "reviewed_explicit",
                "seed": int(seed),
                "validation_fraction": float(validation_fraction),
                "train_count": len(train_samples),
                "validation_count": len(validation_samples),
                "test_count": len(test_samples),
                "train_source_indices": [
                    int(sample.get("source_index", index))
                    for index, sample in enumerate(train_samples)
                ],
                "validation_source_indices": [
                    int(sample.get("source_index", index))
                    for index, sample in enumerate(validation_samples)
                ],
                "test_source_indices": [
                    int(sample.get("source_index", index))
                    for index, sample in enumerate(test_samples)
                ],
            },
        )
    validation_count = 0
    if validation_fraction > 0 and len(samples) >= 2:
        validation_count = max(
            1,
            min(
                len(samples) - 1,
                int(round(len(samples) * validation_fraction)),
            ),
        )
    indices = list(range(len(samples)))
    random.Random(seed).shuffle(indices)
    validation_indices = set(indices[:validation_count])
    train_samples = [
        sample
        for index, sample in enumerate(samples)
        if index not in validation_indices
    ]
    validation_samples = [
        sample
        for index, sample in enumerate(samples)
        if index in validation_indices
    ]
    return (
        train_samples,
        validation_samples,
        {
            "strategy": "deterministic_fraction",
            "seed": int(seed),
            "validation_fraction": float(validation_fraction),
            "train_count": len(train_samples),
            "validation_count": len(validation_samples),
            "train_source_indices": [
                int(sample.get("source_index", index))
                for index, sample in enumerate(train_samples)
            ],
            "validation_source_indices": [
                int(sample.get("source_index", index))
                for index, sample in enumerate(validation_samples)
            ],
        },
    )


def dataset_fingerprint(
    samples: list[dict[str, Any]],
    *,
    reference_audio_path: str | Path,
    instruction_mode: str = "identity_only",
) -> str:
    selected_instruction_mode = normalize_instruction_mode(instruction_mode)
    return _fingerprint_json(
        {
            "reference_audio_sha256": sha256_file(reference_audio_path),
            "instruction_mode": selected_instruction_mode,
            "samples": [
                {
                    "source_index": sample.get("source_index"),
                    "text": sample.get("text"),
                    "audio_sha256": sample.get("audio_sha256"),
                    "duration": sample.get("duration"),
                    "review_status": sample.get("review_status"),
                    "split": sample.get("split"),
                    "instruction_sha256": (
                        sample.get("instruction_sha256")
                        if selected_instruction_mode == "per_record"
                        else None
                    ),
                    "instruction_token_ids_sha256": (
                        sample.get("instruction_token_ids_sha256")
                        if selected_instruction_mode == "per_record"
                        else None
                    ),
                }
                for sample in samples
            ],
        }
    )


def environment_identity() -> dict[str, Any]:
    import torch

    package_names = (
        "qwen-tts",
        "transformers",
        "torch",
        "torchaudio",
        "peft",
        "accelerate",
        "librosa",
        "soundfile",
        "huggingface-hub",
    )
    packages = {}
    for name in package_names:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": packages,
        "pytorch_mps_fallback": os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK"),
        "mps_high_watermark_ratio": os.environ.get(
            "PYTORCH_MPS_HIGH_WATERMARK_RATIO"
        ),
        "mps_built": bool(torch.backends.mps.is_built()),
        "mps_available": bool(torch.backends.mps.is_available()),
        "cuda_available": bool(torch.cuda.is_available()),
    }


def resolve_device(requested: str = "auto") -> str:
    import torch

    value = str(requested or "auto").strip().lower()
    if value not in {"auto", "mps", "cuda", "cpu"}:
        raise SidecarTrainingError(
            "Device must be auto, mps, cuda, or cpu."
        )
    if value == "auto":
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"
    if value == "mps" and not torch.backends.mps.is_available():
        raise SidecarTrainingError("PyTorch MPS is unavailable.")
    if value == "cuda" and not torch.cuda.is_available():
        raise SidecarTrainingError("CUDA is unavailable.")
    return value


def dtype_for_device(device: str):
    import torch

    if device == "cuda":
        return torch.bfloat16
    return torch.float32


def _resolved_model_source(
    model_name: str,
    *,
    local_files_only: bool,
) -> Path:
    candidate = Path(model_name).expanduser()
    if candidate.exists():
        return candidate.resolve()
    if is_registered_model(model_name):
        return resolve_model_path(
            model_name,
            local_files_only=local_files_only,
        )
    return snapshot_download_with_public_fallback(
        model_name,
        local_files_only=local_files_only,
    )


def _load_wrapper(
    *,
    model_name: str,
    device: str,
    dtype,
    local_files_only: bool,
):
    from qwen_tts import Qwen3TTSModel

    model_path = _resolved_model_source(
        model_name,
        local_files_only=local_files_only,
    )
    kwargs: dict[str, Any] = {
        "dtype": dtype,
        "attn_implementation": "eager",
        "local_files_only": True,
    }
    if device == "cuda":
        kwargs["device_map"] = "cuda"
    else:
        kwargs["device_map"] = None
    wrapper = Qwen3TTSModel.from_pretrained(str(model_path), **kwargs)
    hf_model = wrapper.model
    if device in {"mps", "cpu"}:
        hf_model.to(device)
        wrapper.device = next(hf_model.parameters()).device
    return wrapper, hf_model, model_path


def load_model_bundle(
    *,
    model_name: str = DEFAULT_MODEL,
    device: str = "auto",
    local_files_only: bool = False,
) -> dict[str, Any]:
    import torch

    selected_device = resolve_device(device)
    dtype = dtype_for_device(selected_device)
    started = time.perf_counter()
    wrapper, hf_model, model_path = _load_wrapper(
        model_name=model_name,
        device=selected_device,
        dtype=dtype,
        local_files_only=local_files_only,
    )
    elapsed = time.perf_counter() - started
    model_revision = (
        model_spec(model_name).revision
        if is_registered_model(model_name)
        else (model_path.name if len(model_path.name) == 40 else None)
    )
    return {
        "wrapper": wrapper,
        "hf_model": hf_model,
        "processor": wrapper.processor,
        "device": selected_device,
        "dtype": dtype,
        "load_seconds": elapsed,
        "model_path": str(model_path),
        "model_revision": model_revision,
        "mps_available": torch.backends.mps.is_available(),
        "cuda_available": torch.cuda.is_available(),
    }


def tokenize_instruction_ids(
    *,
    processor,
    instruction: str,
    device: str,
):
    normalized = normalize_instruction(instruction, required=True)
    value = processor(
        text=format_instruction_prompt(normalized),
        return_tensors="pt",
        padding=True,
    )["input_ids"].to(device)
    if value.dim() == 1:
        value = value.unsqueeze(0)
    return value


def enable_pytorch_icl_instruction(model) -> None:
    if getattr(model, "_alexandria_icl_instruction_enabled", False):
        return
    original = getattr(model, "generate", None)
    if original is None:
        raise SidecarTrainingError(
            "The PyTorch model does not expose instruction-capable generation."
        )

    def patched(self, *args, **kwargs):
        instruction_ids = getattr(
            self,
            "_alexandria_icl_instruction_ids",
            None,
        )
        if instruction_ids is not None:
            if kwargs.get("instruct_ids") is not None:
                raise SidecarTrainingError(
                    "Instruction IDs were supplied more than once."
                )
            input_ids = kwargs.get("input_ids")
            batch_size = len(input_ids) if input_ids is not None else 1
            kwargs["instruct_ids"] = [instruction_ids] * batch_size
        return original(*args, **kwargs)

    model.generate = types.MethodType(patched, model)
    model._alexandria_icl_instruction_ids = None
    model._alexandria_icl_instruction_enabled = True


def _metadata_entries(data_dir: str | Path) -> list[dict[str, Any]]:
    path = Path(data_dir) / "metadata.jsonl"
    if not path.is_file():
        raise SidecarTrainingError(f"metadata.jsonl not found: {path}")
    entries = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SidecarTrainingError(
                f"metadata.jsonl line {line_number} is invalid JSON: {exc}"
            ) from exc
        if not isinstance(item, dict):
            raise SidecarTrainingError(
                f"metadata.jsonl line {line_number} must be an object."
            )
        text_values = [
            value
            for value in (item.get("text"), item.get("transcript"))
            if value is not None
        ]
        if (
            not text_values
            or any(not isinstance(value, str) or not value.strip() for value in text_values)
        ):
            raise SidecarTrainingError(
                f"metadata.jsonl line {line_number} requires text or transcript."
            )
        if len({value.strip() for value in text_values}) != 1:
            raise SidecarTrainingError(
                f"metadata.jsonl line {line_number} has conflicting text and transcript fields."
            )
        text = text_values[0].strip()
        audio_values = [
            value
            for value in (
                item.get("audio_filepath"),
                item.get("audio"),
                item.get("audio_path"),
            )
            if value is not None
        ]
        if (
            not audio_values
            or any(not isinstance(value, str) or not value.strip() for value in audio_values)
        ):
            raise SidecarTrainingError(
                f"metadata.jsonl line {line_number} requires audio_filepath, audio, or audio_path."
            )
        if len({value.strip() for value in audio_values}) != 1:
            raise SidecarTrainingError(
                f"metadata.jsonl line {line_number} has conflicting audio path fields."
            )
        audio = audio_values[0].strip()
        instruction_value = item.get("instruction")
        legacy_instruction = item.get("instruct")
        if instruction_value is not None and legacy_instruction is not None:
            try:
                if normalize_instruction(
                    instruction_value,
                    required=False,
                ) != normalize_instruction(
                    legacy_instruction,
                    required=False,
                ):
                    raise SidecarTrainingError(
                        f"metadata.jsonl line {line_number} has conflicting instruction and instruct fields."
                    )
            except InstructionPropagationError as exc:
                raise SidecarTrainingError(
                    f"metadata.jsonl line {line_number} instruction is invalid: {exc}"
                ) from exc
        selected_instruction = (
            instruction_value
            if instruction_value is not None
            else legacy_instruction
        )
        try:
            normalized_instruction = normalize_instruction(
                selected_instruction,
                required=False,
            )
        except InstructionPropagationError as exc:
            raise SidecarTrainingError(
                f"metadata.jsonl line {line_number} instruction is invalid: {exc}"
            ) from exc
        review = item.get("review")
        review_status = item.get("review_status")
        if review_status is None and isinstance(review, Mapping):
            review_status = review.get("status")
        entries.append(
            {
                **item,
                "text": text,
                "audio_filepath": audio,
                "instruction": normalized_instruction,
                "review_status": str(review_status or "unspecified"),
            }
        )
    if not entries:
        raise SidecarTrainingError("metadata.jsonl contains no samples.")
    return entries


def _reference_audio_path(
    data_dir: Path,
    entries: list[dict[str, Any]],
) -> Path:
    explicit = entries[0].get("ref_audio")
    if isinstance(explicit, str) and explicit.strip():
        candidate = data_dir / explicit
    elif (data_dir / "ref.wav").is_file():
        candidate = data_dir / "ref.wav"
    else:
        first = (
            entries[0].get("audio_filepath")
            or entries[0].get("audio")
            or entries[0].get("audio_path")
        )
        candidate = data_dir / str(first)
    candidate = candidate.expanduser().resolve()
    try:
        candidate.relative_to(data_dir.expanduser().resolve())
    except ValueError as exc:
        raise SidecarTrainingError(
            "Reference audio must remain inside the dataset directory."
        ) from exc
    if not candidate.is_file():
        raise SidecarTrainingError(f"Reference audio not found: {candidate}")
    return candidate


def prepare_dataset(
    *,
    data_dir: str | Path,
    hf_model,
    processor,
    device: str,
    dtype,
    max_audio_seconds: float = 30.0,
    max_samples: int | None = None,
    instruction_mode: str = "identity_only",
) -> tuple[list[dict[str, Any]], Path, dict[str, Any]]:
    root = Path(data_dir).expanduser().resolve()
    try:
        selected_instruction_mode = normalize_instruction_mode(instruction_mode)
    except InstructionPropagationError as exc:
        raise SidecarTrainingError(str(exc)) from exc
    entries = _metadata_entries(root)
    if selected_instruction_mode == "per_record":
        for index, entry in enumerate(entries):
            try:
                normalize_instruction(
                    entry.get("instruction"),
                    required=True,
                )
            except InstructionPropagationError as exc:
                raise SidecarTrainingError(
                    f"Training sample {index} requires a reviewed instruction: {exc}"
                ) from exc
    if max_samples is not None:
        if max_samples <= 0:
            raise SidecarTrainingError("max_samples must be positive.")
        entries = entries[:max_samples]
    import numpy as np
    import torch
    from qwen_tts.core.models.modeling_qwen3_tts import mel_spectrogram

    ref_audio_path = _reference_audio_path(root, entries)
    try:
        ref_audio, _ = decode_audio_mono(
            ref_audio_path,
            sample_rate=24000,
        )
    except AudioProcessingError as exc:
        raise SidecarTrainingError(
            f"Reference audio could not be decoded: {exc}"
        ) from exc
    ref_audio = ref_audio.astype(np.float32, copy=False)
    with torch.no_grad():
        ref_mels = mel_spectrogram(
            torch.from_numpy(ref_audio).unsqueeze(0),
            n_fft=1024,
            num_mels=128,
            sampling_rate=24000,
            hop_size=256,
            win_size=1024,
            fmin=0,
            fmax=12000,
        ).transpose(1, 2).to(device).to(dtype)
        speaker_embedding = hf_model.speaker_encoder(ref_mels).detach()
    samples = []
    skipped = []
    started = time.perf_counter()
    for index, entry in enumerate(entries):
        relative = entry.get("audio_filepath") or entry.get("audio")
        path = (root / str(relative)).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise SidecarTrainingError(
                f"Sample {index} escaped the dataset directory."
            ) from exc
        if not path.is_file():
            skipped.append({"index": index, "reason": "missing_audio"})
            continue
        try:
            audio, sample_rate = decode_audio_mono(
                path,
                sample_rate=24000,
            )
        except AudioProcessingError:
            skipped.append({"index": index, "reason": "invalid_audio"})
            continue
        duration = len(audio) / sample_rate
        if duration > max_audio_seconds:
            skipped.append(
                {
                    "index": index,
                    "reason": "too_long",
                    "duration_seconds": duration,
                }
            )
            continue
        with torch.no_grad():
            encoded = hf_model.speech_tokenizer.encode(audio, sr=sample_rate)
            codec_ids = encoded.audio_codes[0]
        text = entry["text"].strip()
        assistant_text = (
            f"<|im_start|>assistant\n{text}<|im_end|>\n"
            "<|im_start|>assistant\n"
        )
        text_inputs = processor(
            text=assistant_text,
            return_tensors="pt",
            padding=True,
        )
        text_ids = text_inputs["input_ids"].to(device)
        if text_ids.dim() == 1:
            text_ids = text_ids.unsqueeze(0)
        instruction = normalize_instruction(
            entry.get("instruction"),
            required=selected_instruction_mode == "per_record",
        )
        instruction_ids = None
        instruction_metadata: dict[str, Any] = {}
        if selected_instruction_mode == "per_record":
            instruction_inputs = processor(
                text=format_instruction_prompt(instruction),
                return_tensors="pt",
                padding=True,
            )
            instruction_ids = instruction_inputs["input_ids"].to(device)
            if instruction_ids.dim() == 1:
                instruction_ids = instruction_ids.unsqueeze(0)
            instruction_metadata = instruction_identity(
                instruction,
                token_ids=instruction_ids,
            )
        samples.append(
            {
                "source_index": index,
                "codec_ids": codec_ids.to(device),
                "spk_embedding": speaker_embedding,
                "text_ids": text_ids,
                "instruction_ids": instruction_ids,
                "instruction": instruction,
                **instruction_metadata,
                "audio_path": str(path),
                "audio_sha256": sha256_file(path),
                "text": text,
                "duration": duration,
                "review_status": str(
                    entry.get("review_status") or "unspecified"
                ),
                "split": (
                    str(entry.get("split")).strip().lower()
                    if entry.get("split") is not None
                    else None
                ),
            }
        )
    if not samples:
        raise SidecarTrainingError("No valid training samples remain.")
    review_status_counts: dict[str, int] = {}
    for sample in samples:
        status = str(sample.get("review_status") or "unspecified")
        review_status_counts[status] = review_status_counts.get(status, 0) + 1
    total_duration = sum(float(sample["duration"]) for sample in samples)
    reviewed_statuses = {"approved", "accepted", "reviewed"}
    return (
        samples,
        ref_audio_path,
        {
            "entry_count": len(entries),
            "prepared_count": len(samples),
            "skipped": skipped,
            "duration_seconds": total_duration,
            "duration_minutes": total_duration / 60.0,
            "review_status_counts": review_status_counts,
            "all_samples_reviewed": all(
                str(sample.get("review_status") or "").casefold()
                in reviewed_statuses
                for sample in samples
            ),
            "instruction_propagation": build_instruction_propagation_contract(
                mode=selected_instruction_mode,
                samples=samples,
            ),
            "preparation_seconds": time.perf_counter() - started,
        },
    )


def build_teacher_forcing_input(
    sample: dict[str, Any],
    hf_model,
    device: str,
    dtype,
    *,
    language: str = "english",
):
    import torch

    talker = hf_model.talker
    config = hf_model.config
    talker_config = config.talker_config
    codec_ids = sample["codec_ids"]
    speaker_embedding = sample["spk_embedding"]
    text_ids = sample["text_ids"]
    frames = codec_ids.shape[0]
    num_groups = talker_config.num_code_groups

    special_ids = torch.tensor(
        [[config.tts_bos_token_id, config.tts_eos_token_id, config.tts_pad_token_id]],
        device=device,
        dtype=text_ids.dtype,
    )
    tts_bos, tts_eos, tts_pad = talker.text_projection(
        talker.get_text_embeddings()(special_ids)
    ).chunk(3, dim=1)
    role_embed = talker.text_projection(
        talker.get_text_embeddings()(text_ids[:, :3])
    )
    language_id = (
        talker_config.codec_language_id.get(language)
        if talker_config.codec_language_id
        else None
    )
    if language_id is not None:
        prefix_ids = [[
            talker_config.codec_think_id,
            talker_config.codec_think_bos_id,
            language_id,
            talker_config.codec_think_eos_id,
        ]]
    else:
        prefix_ids = [[
            talker_config.codec_nothink_id,
            talker_config.codec_think_bos_id,
            talker_config.codec_think_eos_id,
        ]]
    codec_prefix = talker.get_input_embeddings()(
        torch.tensor(prefix_ids, device=device, dtype=text_ids.dtype)
    )
    codec_suffix = talker.get_input_embeddings()(
        torch.tensor(
            [[talker_config.codec_pad_id, talker_config.codec_bos_id]],
            device=device,
            dtype=text_ids.dtype,
        )
    )
    codec_embed = torch.cat(
        [
            codec_prefix,
            speaker_embedding.view(1, 1, -1),
            codec_suffix,
        ],
        dim=1,
    )
    prefix_codec_len = codec_embed.shape[1]
    tts_prefix = torch.cat(
        [
            tts_pad.expand(-1, prefix_codec_len - 2, -1),
            tts_bos,
        ],
        dim=1,
    )
    role_prefix = torch.cat(
        [role_embed, tts_prefix + codec_embed[:, :-1]],
        dim=1,
    )
    text_content_ids = text_ids[:, 3:-5]
    text_content_embed = talker.text_projection(
        talker.get_text_embeddings()(text_content_ids)
    )
    text_with_eos = torch.cat([text_content_embed, tts_eos], dim=1)
    text_pad_ids = torch.full(
        (1, text_content_ids.shape[1] + 1),
        talker_config.codec_pad_id,
        device=device,
        dtype=text_ids.dtype,
    )
    text_portion = text_with_eos + talker.get_input_embeddings()(text_pad_ids)
    codec_bos_embed = talker.get_input_embeddings()(
        torch.tensor(
            [[talker_config.codec_bos_id]],
            device=device,
            dtype=text_ids.dtype,
        )
    )
    prefill = torch.cat(
        [role_prefix, text_portion, tts_pad + codec_bos_embed],
        dim=1,
    )
    instruction_ids = sample.get("instruction_ids")
    instruction_token_count = 0
    if instruction_ids is not None:
        if instruction_ids.dim() == 1:
            instruction_ids = instruction_ids.unsqueeze(0)
        instruction_embed = talker.text_projection(
            talker.get_text_embeddings()(instruction_ids)
        )
        instruction_token_count = int(instruction_embed.shape[1])
        prefill = torch.cat([instruction_embed, prefill], dim=1)
    prefill_len = prefill.shape[1]
    sample["instruction_prefill_token_count"] = instruction_token_count
    sample["instruction_placement"] = INSTRUCTION_PLACEMENT

    group_embeddings = [
        talker.get_input_embeddings()(codec_ids[:, :1])
    ]
    for group in range(1, num_groups):
        group_embeddings.append(
            talker.code_predictor.get_input_embeddings()[group - 1](
                codec_ids[:, group : group + 1]
            )
        )
    codec_sum = torch.cat(group_embeddings, dim=1).sum(dim=1)
    audio_embeds = (codec_sum + tts_pad.squeeze(0)).unsqueeze(0)
    full_input = torch.cat([prefill, audio_embeds], dim=1)
    labels = torch.full(
        (1, prefill_len + frames),
        -100,
        device=device,
        dtype=torch.long,
    )
    labels[0, prefill_len:] = codec_ids[:, 0]
    return full_input, labels, codec_ids, prefill_len


def _loss_for_sample(
    *,
    sample: dict[str, Any],
    hf_model,
    trainable_talker,
    device: str,
    dtype,
    language: str,
):
    import torch.nn.functional as functional

    full_input, labels, all_codec_ids, prefill_len = build_teacher_forcing_input(
        sample,
        hf_model,
        device,
        dtype,
        language=language,
    )
    frames = all_codec_ids.shape[0]
    base_talker = (
        trainable_talker.get_base_model()
        if hasattr(trainable_talker, "peft_config")
        and hasattr(trainable_talker, "get_base_model")
        else trainable_talker
    )
    transformer = base_talker.model
    output = transformer(inputs_embeds=full_input, use_cache=False)
    hidden_states = output.last_hidden_state
    logits = base_talker.codec_head(hidden_states)
    talker_loss = functional.cross_entropy(
        logits[:, :-1, :].contiguous().view(-1, logits.size(-1)),
        labels[:, 1:].contiguous().view(-1),
        ignore_index=-100,
    )
    audio_hidden = hidden_states[
        0,
        prefill_len - 1 : prefill_len + frames - 1,
        :,
    ]
    _, sub_loss = base_talker.forward_sub_talker_finetune(
        all_codec_ids,
        audio_hidden,
    )
    total_loss = talker_loss + 0.3 * sub_loss
    return total_loss, talker_loss, sub_loss


def enumerate_lora_targets(
    talker,
    *,
    profile: str = "attention_mlp",
) -> dict[str, Any]:
    import torch

    suffixes = lora_target_suffixes(profile)
    actual = []
    found_suffixes = set()
    for name, module in talker.named_modules():
        if not isinstance(module, torch.nn.Linear):
            continue
        suffix = name.rsplit(".", 1)[-1]
        if suffix in suffixes:
            actual.append(name)
            found_suffixes.add(suffix)
    if not actual:
        raise SidecarTrainingError(
            "No supported Talker attention or MLP projection modules were found."
        )
    return {
        "profile": str(profile).strip().lower(),
        "requested_suffixes": list(suffixes),
        "actual_module_names": sorted(actual),
        "target_suffixes": sorted(found_suffixes),
        "module_count": len(actual),
    }


def _memory_metrics(device: str) -> dict[str, Any]:
    import psutil

    result: dict[str, Any] = {
        "process_rss_gib": psutil.Process().memory_info().rss / (1024**3),
    }
    if device == "mps":
        try:
            import torch

            result["mps_current_allocated_gib"] = (
                torch.mps.current_allocated_memory() / (1024**3)
            )
            result["mps_driver_allocated_gib"] = (
                torch.mps.driver_allocated_memory() / (1024**3)
            )
        except Exception:
            pass
    elif device == "cuda":
        try:
            import torch

            result["cuda_peak_allocated_gib"] = (
                torch.cuda.max_memory_allocated() / (1024**3)
            )
        except Exception:
            pass
    return result


def _clear_device_cache(device: str) -> None:
    import torch

    if device == "mps":
        torch.mps.empty_cache()
    elif device == "cuda":
        torch.cuda.empty_cache()
    gc.collect()


def evaluate_samples(
    *,
    samples: list[dict[str, Any]],
    hf_model,
    trainable_talker,
    device: str,
    dtype,
    language: str,
) -> dict[str, Any] | None:
    if not samples:
        return None
    import torch

    was_training = bool(trainable_talker.training)
    trainable_talker.eval()
    totals = []
    talker_losses = []
    sub_losses = []
    started = time.perf_counter()
    try:
        with torch.no_grad():
            for sample in samples:
                total_loss, talker_loss, sub_loss = _loss_for_sample(
                    sample=sample,
                    hf_model=hf_model,
                    trainable_talker=trainable_talker,
                    device=device,
                    dtype=dtype,
                    language=language,
                )
                values = (
                    float(total_loss.detach().cpu()),
                    float(talker_loss.detach().cpu()),
                    float(sub_loss.detach().cpu()),
                )
                if not all(math.isfinite(value) for value in values):
                    raise SidecarTrainingError(
                        "Validation produced a non-finite loss."
                    )
                totals.append(values[0])
                talker_losses.append(values[1])
                sub_losses.append(values[2])
                del total_loss, talker_loss, sub_loss
                _clear_device_cache(device)
    finally:
        if was_training:
            trainable_talker.train()
    return {
        "sample_count": len(samples),
        "loss": sum(totals) / len(totals),
        "talker_loss": sum(talker_losses) / len(talker_losses),
        "sub_loss": sum(sub_losses) / len(sub_losses),
        "evaluation_seconds": time.perf_counter() - started,
    }


def build_training_contract(
    *,
    mode: str,
    model_name: str,
    model_revision: str | None,
    dataset_fingerprint_value: str,
    target_profile: str | None,
    lora_rank: int | None,
    lora_alpha: int | None,
    learning_rate: float,
    gradient_accumulation_steps: int,
    language: str,
    max_audio_seconds: float,
    max_samples: int | None,
    validation_fraction: float,
    seed: int,
    instruction_propagation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if instruction_propagation is None:
        propagation = build_instruction_propagation_contract(
            mode="identity_only",
            samples=[],
        )
    else:
        try:
            propagation = validate_instruction_propagation_contract(
                instruction_propagation
            )
        except InstructionPropagationError as exc:
            raise SidecarTrainingError(
                f"Instruction propagation contract is invalid: {exc}"
            ) from exc
    value = {
        "schema_version": TRAINING_CHECKPOINT_SCHEMA_VERSION,
        "mode": mode,
        "model_name": model_name,
        "model_revision": model_revision,
        "dataset_fingerprint": dataset_fingerprint_value,
        "target_profile": target_profile,
        "lora_rank": lora_rank,
        "lora_alpha": lora_alpha,
        "learning_rate": float(learning_rate),
        "gradient_accumulation_steps": int(gradient_accumulation_steps),
        "language": str(language),
        "max_audio_seconds": float(max_audio_seconds),
        "max_samples": max_samples,
        "validation_fraction": float(validation_fraction),
        "seed": int(seed),
        "instruction_propagation": propagation,
    }
    return {
        **value,
        "fingerprint": _fingerprint_json(value),
    }


def _optimizer_to_device(optimizer, device: str) -> None:
    import torch

    for state in optimizer.state.values():
        for key, value in state.items():
            if isinstance(value, torch.Tensor):
                state[key] = value.to(device)


def save_training_checkpoint(
    *,
    output_dir: str | Path,
    epoch: int,
    global_step: int,
    optimizer_steps: int,
    trainable_talker,
    optimizer,
    training_contract: dict[str, Any],
    step_metrics: list[dict[str, Any]],
    validation_metrics: list[dict[str, Any]],
    device: str,
) -> dict[str, Any]:
    import torch

    output = Path(output_dir).expanduser().resolve()
    checkpoints = output / "checkpoints"
    checkpoints.mkdir(parents=True, exist_ok=True)
    final = checkpoints / f"epoch_{epoch:04d}"
    partial = checkpoints / f".epoch_{epoch:04d}.partial"
    if final.exists():
        raise SidecarTrainingError(
            f"Training checkpoint already exists: {final}"
        )
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(parents=True)
    try:
        adapter_dir = partial / "adapter"
        trainable_talker.save_pretrained(adapter_dir)
        runtime_state: dict[str, Any] = {
            "optimizer_state_dict": optimizer.state_dict(),
            "python_random_state": random.getstate(),
            "torch_rng_state": torch.random.get_rng_state(),
        }
        if device == "mps" and hasattr(torch.mps, "get_rng_state"):
            runtime_state["mps_rng_state"] = torch.mps.get_rng_state()
        elif device == "cuda":
            runtime_state["cuda_rng_state_all"] = torch.cuda.get_rng_state_all()
        torch.save(runtime_state, partial / "trainer_state.pt")
        state = {
            "schema_version": TRAINING_CHECKPOINT_SCHEMA_VERSION,
            "status": "resumable_experimental",
            "created_at_utc": utc_timestamp(),
            "completed_epoch": int(epoch),
            "global_step": int(global_step),
            "optimizer_steps": int(optimizer_steps),
            "training_contract": training_contract,
            "step_metrics": step_metrics,
            "validation_metrics": validation_metrics,
            "adapter_files": sorted(
                path.relative_to(partial).as_posix()
                for path in adapter_dir.rglob("*")
                if path.is_file()
            ),
        }
        (partial / "checkpoint.json").write_text(
            json.dumps(state, indent=2),
            encoding="utf-8",
        )
        os.replace(partial, final)
    except Exception:
        if partial.exists():
            shutil.rmtree(partial, ignore_errors=True)
        raise
    return {
        "path": final.relative_to(output).as_posix(),
        "completed_epoch": int(epoch),
        "global_step": int(global_step),
        "optimizer_steps": int(optimizer_steps),
        "checkpoint_sha256": sha256_file(final / "checkpoint.json"),
    }


def load_training_checkpoint(
    *,
    checkpoint_dir: str | Path,
    expected_contract: dict[str, Any],
) -> dict[str, Any]:
    import torch

    checkpoint = Path(checkpoint_dir).expanduser().resolve()
    state_path = checkpoint / "checkpoint.json"
    runtime_path = checkpoint / "trainer_state.pt"
    adapter_dir = checkpoint / "adapter"
    if not state_path.is_file() or not runtime_path.is_file():
        raise SidecarTrainingError(
            "Training checkpoint is incomplete."
        )
    if not (adapter_dir / "adapter_config.json").is_file() or not (
        adapter_dir / "adapter_model.safetensors"
    ).is_file():
        raise SidecarTrainingError(
            "Training checkpoint adapter is incomplete."
        )
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SidecarTrainingError(
            f"Training checkpoint could not be read: {exc}"
        ) from exc
    if (
        state.get("schema_version") != TRAINING_CHECKPOINT_SCHEMA_VERSION
        or state.get("status") != "resumable_experimental"
    ):
        raise SidecarTrainingError(
            "Training checkpoint schema is unsupported."
        )
    recorded_contract = state.get("training_contract")
    if not isinstance(recorded_contract, dict) or (
        recorded_contract.get("fingerprint")
        != expected_contract.get("fingerprint")
    ):
        raise SidecarTrainingError(
            "Training checkpoint is incompatible with the requested run."
        )
    try:
        runtime_state = torch.load(
            runtime_path,
            map_location="cpu",
            weights_only=False,
        )
    except Exception as exc:
        raise SidecarTrainingError(
            f"Training checkpoint runtime state could not be read: {exc}"
        ) from exc
    return {
        "checkpoint_dir": checkpoint,
        "adapter_dir": adapter_dir,
        "state": state,
        "runtime_state": runtime_state,
    }


def restore_training_runtime_state(
    *,
    optimizer,
    runtime_state: dict[str, Any],
    device: str,
) -> None:
    import torch

    optimizer.load_state_dict(runtime_state["optimizer_state_dict"])
    _optimizer_to_device(optimizer, device)
    random.setstate(runtime_state["python_random_state"])
    torch.random.set_rng_state(runtime_state["torch_rng_state"])
    if device == "mps" and "mps_rng_state" in runtime_state and hasattr(
        torch.mps,
        "set_rng_state",
    ):
        torch.mps.set_rng_state(runtime_state["mps_rng_state"])
    elif device == "cuda" and "cuda_rng_state_all" in runtime_state:
        torch.cuda.set_rng_state_all(runtime_state["cuda_rng_state_all"])


def _save_manifest(
    *,
    output_dir: Path,
    artifact_format: str,
    model_name: str,
    model_revision: str | None,
    device: str,
    dataset_dir: Path,
    metrics: dict[str, Any],
    files: Iterable[Path],
) -> dict[str, Any]:
    artifacts = []
    for path in files:
        if path.is_file():
            artifacts.append(
                {
                    "path": path.relative_to(output_dir).as_posix(),
                    "sha256": sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
            )
    manifest = {
        "schema_version": 1,
        "artifact_format": artifact_format,
        "status": "experimental_unassigned",
        "base_model": model_name,
        "base_model_revision": model_revision,
        "training_device": device,
        "dataset_path": str(dataset_dir),
        "created_at_utc": utc_timestamp(),
        "instruction_propagation": metrics.get(
            "training_contract",
            {},
        ).get("instruction_propagation"),
        "metrics": metrics,
        "files": artifacts,
        "production_assignment_supported": False,
    }
    path = output_dir / "sidecar_artifact.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest["manifest_sha256"] = sha256_file(path)
    return manifest


def write_canonical_reference_wav(
    source_path: str | Path,
    target_path: str | Path,
    *,
    sample_rate: int = 24000,
) -> dict[str, Any]:
    import soundfile as sf

    source = Path(source_path).expanduser().resolve()
    target = Path(target_path).expanduser().resolve()
    if not source.is_file():
        raise SidecarTrainingError(
            f"Reference audio does not exist: {source}"
        )
    if source.stat().st_size == 0:
        raise SidecarTrainingError("Reference audio is empty.")
    try:
        write_canonical_wav(
            source,
            target,
            sample_rate=sample_rate,
            subtype="PCM_16",
        )
    except AudioProcessingError as exc:
        raise SidecarTrainingError(
            f"Reference audio could not be decoded: {exc}"
        ) from exc
    try:
        info = sf.info(str(target))
    except Exception as exc:
        raise SidecarTrainingError(
            f"Canonical reference WAV could not be verified: {exc}"
        ) from exc
    if info.format != "WAV" or info.frames <= 0:
        raise SidecarTrainingError(
            "Canonical reference audio is not a valid WAV file."
        )
    return {
        "source_path": str(source),
        "target_path": str(target),
        "sample_rate": int(info.samplerate),
        "channels": int(info.channels),
        "frames": int(info.frames),
        "duration_seconds": float(info.duration),
        "sha256": sha256_file(target),
    }


def infer_lora_adapter(
    *,
    adapter_dir: str | Path,
    output_path: str | Path,
    text: str,
    instruction: str | None = None,
    model_name: str = DEFAULT_MODEL,
    device: str = "auto",
    language: str = "English",
    max_new_tokens: int = 600,
    local_files_only: bool = False,
) -> dict[str, Any]:
    import soundfile as sf
    from peft import PeftModel

    adapter = Path(adapter_dir).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    if not (adapter / "adapter_model.safetensors").is_file():
        raise SidecarTrainingError(
            "LoRA adapter weights are missing."
        )
    manifest_path = adapter / "sidecar_artifact.json"
    if not manifest_path.is_file():
        raise SidecarTrainingError(
            "LoRA adapter requires sidecar_artifact.json."
        )
    try:
        artifact_manifest = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SidecarTrainingError(
            f"LoRA adapter manifest could not be read: {exc}"
        ) from exc
    raw_propagation = artifact_manifest.get("instruction_propagation")
    try:
        propagation = (
            validate_instruction_propagation_contract(raw_propagation)
            if raw_propagation is not None
            else build_instruction_propagation_contract(
                mode="identity_only",
                samples=[],
            )
        )
        normalized_instruction = normalize_instruction(
            instruction,
            required=propagation["instruction_required_at_inference"],
        )
    except InstructionPropagationError as exc:
        raise SidecarTrainingError(str(exc)) from exc
    reference_audio = adapter / "ref_sample.wav"
    reference_text_path = adapter / "ref_sample.txt"
    if not reference_audio.is_file() or not reference_text_path.is_file():
        raise SidecarTrainingError(
            "LoRA adapter requires canonical reference audio and text."
        )
    reference_text = reference_text_path.read_text(
        encoding="utf-8"
    ).strip()
    if not isinstance(text, str) or not text.strip():
        raise SidecarTrainingError("Inference text must be non-empty.")
    bundle = load_model_bundle(
        model_name=model_name,
        device=device,
        local_files_only=local_files_only,
    )
    wrapper = bundle["wrapper"]
    hf_model = bundle["hf_model"]
    peft_talker = PeftModel.from_pretrained(
        hf_model.talker,
        str(adapter),
    )
    peft_talker.to(bundle["device"])
    hf_model.talker = peft_talker
    wrapper.model = hf_model
    wrapper.device = next(hf_model.parameters()).device
    instruction_ids = None
    inference_instruction_identity = None
    if normalized_instruction:
        instruction_ids = tokenize_instruction_ids(
            processor=wrapper.processor,
            instruction=normalized_instruction,
            device=str(wrapper.device),
        )
        inference_instruction_identity = instruction_identity(
            normalized_instruction,
            token_ids=instruction_ids,
        )
        enable_pytorch_icl_instruction(hf_model)
        hf_model._alexandria_icl_instruction_ids = instruction_ids
    started = time.perf_counter()
    try:
        wavs, sample_rate = wrapper.generate_voice_clone(
            text=text.strip(),
            language=language,
            ref_audio=str(reference_audio),
            ref_text=reference_text,
            non_streaming_mode=True,
            max_new_tokens=max_new_tokens,
        )
    finally:
        if getattr(hf_model, "_alexandria_icl_instruction_enabled", False):
            hf_model._alexandria_icl_instruction_ids = None
    elapsed = time.perf_counter() - started
    if not wavs:
        raise SidecarTrainingError(
            "PyTorch adapter inference returned no audio."
        )
    audio = wavs[0]
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output), audio, sample_rate)
    duration = len(audio) / sample_rate
    return {
        "status": "generated_experimental",
        "model_name": model_name,
        "device": bundle["device"],
        "adapter_manifest_sha256": sha256_file(manifest_path),
        "training_instruction_propagation": propagation,
        "inference_instruction_applied": bool(normalized_instruction),
        "inference_instruction": inference_instruction_identity,
        "instruction_placement": INSTRUCTION_PLACEMENT,
        "text_sha256": hashlib.sha256(
            text.strip().encode("utf-8")
        ).hexdigest(),
        "elapsed_seconds": elapsed,
        "audio_duration_seconds": duration,
        "real_time_factor": elapsed / duration if duration else None,
        "output_path": str(output),
        "output_sha256": sha256_file(output),
        "production_assignment_supported": False,
    }


def merge_lora_adapter(
    *,
    adapter_dir: str | Path,
    output_dir: str | Path,
    model_name: str = DEFAULT_MODEL,
    device: str = "auto",
    local_files_only: bool = False,
) -> dict[str, Any]:
    from huggingface_hub import save_torch_model
    from peft import PeftModel

    adapter = Path(adapter_dir).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    manifest_path = adapter / "sidecar_artifact.json"
    if not manifest_path.is_file():
        raise SidecarTrainingError(
            "LoRA merge requires a sidecar_artifact.json manifest."
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SidecarTrainingError(
            f"LoRA artifact manifest could not be read: {exc}"
        ) from exc
    if manifest.get("artifact_format") != "peft_lora_adapter":
        raise SidecarTrainingError(
            "Only a PEFT LoRA sidecar artifact may be merged."
        )
    raw_propagation = manifest.get("instruction_propagation")
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
        raise SidecarTrainingError(
            f"LoRA instruction propagation is invalid: {exc}"
        ) from exc
    for required in (
        "adapter_config.json",
        "adapter_model.safetensors",
        "ref_sample.wav",
        "ref_sample.txt",
    ):
        if not (adapter / required).is_file():
            raise SidecarTrainingError(
                f"LoRA artifact is missing {required}."
            )
    if output.exists() and any(output.iterdir()):
        raise SidecarTrainingError(
            "Merged checkpoint output directory already contains files."
        )
    output.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    bundle = load_model_bundle(
        model_name=model_name,
        device=device,
        local_files_only=local_files_only,
    )
    hf_model = bundle["hf_model"]
    selected_device = bundle["device"]
    peft_talker = PeftModel.from_pretrained(
        hf_model.talker,
        str(adapter),
    )
    peft_talker.to(selected_device)
    hf_model.talker = peft_talker.merge_and_unload()
    hf_model.to("cpu")

    save_torch_model(
        hf_model,
        output,
        max_shard_size="4GB",
        safe_serialization=True,
    )
    hf_model.config.to_json_file(
        output / "config.json",
        use_diff=False,
    )
    bundle["processor"].save_pretrained(output)
    base_snapshot = Path(bundle["model_path"])
    for name in (
        "speech_tokenizer",
        "generate_config.json",
        "generation_config.json",
    ):
        source = base_snapshot / name
        destination = output / name
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        elif source.is_file():
            shutil.copy2(source, destination)
    shutil.copy2(adapter / "ref_sample.wav", output / "ref_sample.wav")
    shutil.copy2(adapter / "ref_sample.txt", output / "ref_sample.txt")

    files = [path for path in output.rglob("*") if path.is_file()]
    metrics = {
        "status": "merged_experimental",
        "base_model": model_name,
        "base_model_revision": bundle["model_revision"],
        "adapter_dir": str(adapter),
        "adapter_manifest_sha256": sha256_file(manifest_path),
        "instruction_propagation": instruction_propagation,
        "device": selected_device,
        "model_load_seconds": bundle["load_seconds"],
        "merge_total_seconds": time.perf_counter() - started,
        "file_count": len(files),
        "size_bytes": sum(path.stat().st_size for path in files),
        "production_assignment_supported": False,
    }
    (output / "merge_metrics.json").write_text(
        json.dumps(metrics, indent=2),
        encoding="utf-8",
    )
    return {
        **metrics,
        "output_dir": str(output),
    }


def run_training(
    *,
    mode: str,
    data_dir: str | Path,
    output_dir: str | Path,
    model_name: str = DEFAULT_MODEL,
    device: str = "auto",
    epochs: int = 1,
    max_steps: int | None = None,
    learning_rate: float = 5e-6,
    gradient_accumulation_steps: int = 1,
    language: str = "english",
    max_audio_seconds: float = 30.0,
    max_samples: int | None = None,
    local_files_only: bool = False,
    lora_rank: int = 32,
    lora_alpha: int = 128,
    lora_target_profile: str = "attention_mlp",
    validation_fraction: float = 0.1,
    seed: int = 1337,
    instruction_mode: str = "identity_only",
    resume_from: str | Path | None = None,
    checkpoint_every_epoch: bool = True,
) -> dict[str, Any]:
    import torch

    selected_mode = str(mode).strip().lower()
    try:
        selected_instruction_mode = normalize_instruction_mode(
            instruction_mode
        )
    except InstructionPropagationError as exc:
        raise SidecarTrainingError(str(exc)) from exc
    if selected_mode not in {"sft", "lora"}:
        raise SidecarTrainingError("Training mode must be sft or lora.")
    if epochs <= 0:
        raise SidecarTrainingError("epochs must be positive.")
    if max_steps is not None and max_steps <= 0:
        raise SidecarTrainingError("max_steps must be positive.")
    if gradient_accumulation_steps <= 0:
        raise SidecarTrainingError(
            "gradient_accumulation_steps must be positive."
        )
    if lora_rank <= 0 or lora_alpha <= 0:
        raise SidecarTrainingError(
            "LoRA rank and alpha must be positive."
        )
    if resume_from is not None and selected_mode != "lora":
        raise SidecarTrainingError(
            "Checkpoint resume is currently supported only for LoRA training."
        )

    output = Path(output_dir).expanduser().resolve()
    dataset = Path(data_dir).expanduser().resolve()
    checkpoint_path = (
        Path(resume_from).expanduser().resolve()
        if resume_from is not None
        else None
    )
    if checkpoint_path is None:
        if output.exists() and any(output.iterdir()):
            raise SidecarTrainingError(
                "Output directory already contains files."
            )
    else:
        if output.exists() and any(output.iterdir()):
            raise SidecarTrainingError(
                "Resume output directory already contains files."
            )
        if not checkpoint_path.is_dir():
            raise SidecarTrainingError(
                f"Resume checkpoint was not found: {checkpoint_path}"
            )
    output.mkdir(parents=True, exist_ok=True)

    random.seed(seed)
    torch.manual_seed(seed)
    if torch.backends.mps.is_available() and hasattr(torch.mps, "manual_seed"):
        torch.mps.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)

    bundle = load_model_bundle(
        model_name=model_name,
        device=device,
        local_files_only=local_files_only,
    )
    hf_model = bundle["hf_model"]
    selected_device = bundle["device"]
    dtype = bundle["dtype"]
    samples, ref_audio_path, data_metrics = prepare_dataset(
        data_dir=dataset,
        hf_model=hf_model,
        processor=bundle["processor"],
        device=selected_device,
        dtype=dtype,
        max_audio_seconds=max_audio_seconds,
        max_samples=max_samples,
        instruction_mode=selected_instruction_mode,
    )
    instruction_propagation = data_metrics["instruction_propagation"]
    dataset_fingerprint_value = dataset_fingerprint(
        samples,
        reference_audio_path=ref_audio_path,
        instruction_mode=selected_instruction_mode,
    )
    train_samples, validation_samples, split_metrics = split_prepared_samples(
        samples,
        validation_fraction=validation_fraction,
        seed=seed,
    )
    data_metrics = {
        **data_metrics,
        "fingerprint": dataset_fingerprint_value,
        "split": split_metrics,
    }

    talker = hf_model.talker
    target_metrics = None
    target_profile = None
    if selected_mode == "lora":
        from peft import LoraConfig, PeftModel, get_peft_model

        target_profile = str(lora_target_profile).strip().lower()
        target_metrics = enumerate_lora_targets(
            talker,
            profile=target_profile,
        )
    training_contract = build_training_contract(
        mode=selected_mode,
        model_name=model_name,
        model_revision=bundle["model_revision"],
        dataset_fingerprint_value=dataset_fingerprint_value,
        target_profile=target_profile,
        lora_rank=lora_rank if selected_mode == "lora" else None,
        lora_alpha=lora_alpha if selected_mode == "lora" else None,
        learning_rate=learning_rate,
        gradient_accumulation_steps=gradient_accumulation_steps,
        language=language,
        max_audio_seconds=max_audio_seconds,
        max_samples=max_samples,
        validation_fraction=validation_fraction,
        seed=seed,
        instruction_propagation=instruction_propagation,
    )
    checkpoint_data = (
        load_training_checkpoint(
            checkpoint_dir=checkpoint_path,
            expected_contract=training_contract,
        )
        if checkpoint_path is not None
        else None
    )

    if selected_mode == "lora":
        if checkpoint_data is not None:
            trainable = PeftModel.from_pretrained(
                talker,
                str(checkpoint_data["adapter_dir"]),
                is_trainable=True,
            )
        else:
            lora_config = LoraConfig(
                r=lora_rank,
                lora_alpha=lora_alpha,
                target_modules=target_metrics["target_suffixes"],
                lora_dropout=0.05,
                bias="none",
            )
            trainable = get_peft_model(talker, lora_config)
        hf_model.talker = trainable
        trainable.enable_input_require_grads()
        try:
            trainable.base_model.model.model.gradient_checkpointing_enable()
        except AttributeError:
            pass
        artifact_format = "peft_lora_adapter"
    else:
        trainable = talker
        for parameter in hf_model.parameters():
            parameter.requires_grad = False
        for parameter in trainable.parameters():
            parameter.requires_grad = True
        try:
            trainable.model.gradient_checkpointing_enable()
        except AttributeError:
            pass
        artifact_format = "official_sft_full_checkpoint"

    trainable.train()
    parameters = [
        parameter
        for parameter in trainable.parameters()
        if parameter.requires_grad
    ]
    if not parameters:
        raise SidecarTrainingError("No trainable parameters were selected.")
    trainable_count = sum(parameter.numel() for parameter in parameters)
    total_count = sum(parameter.numel() for parameter in trainable.parameters())
    optimizer = torch.optim.AdamW(
        parameters,
        lr=learning_rate,
        weight_decay=0.01,
    )
    optimizer.zero_grad()

    step_metrics: list[dict[str, Any]] = []
    validation_metrics: list[dict[str, Any]] = []
    checkpoints: list[dict[str, Any]] = []
    global_step = 0
    optimizer_steps = 0
    start_epoch = 1
    resumed = checkpoint_data is not None
    if checkpoint_data is not None:
        checkpoint_state = checkpoint_data["state"]
        step_metrics = list(checkpoint_state.get("step_metrics") or [])
        validation_metrics = list(
            checkpoint_state.get("validation_metrics") or []
        )
        global_step = int(checkpoint_state.get("global_step") or 0)
        optimizer_steps = int(checkpoint_state.get("optimizer_steps") or 0)
        start_epoch = int(checkpoint_state["completed_epoch"]) + 1
        restore_training_runtime_state(
            optimizer=optimizer,
            runtime_state=checkpoint_data["runtime_state"],
            device=selected_device,
        )
    if start_epoch > epochs:
        raise SidecarTrainingError(
            "Resume checkpoint already completed the requested epoch count."
        )
    if max_steps is not None and global_step >= max_steps:
        raise SidecarTrainingError(
            "Resume checkpoint already reached the requested max_steps."
        )

    started = time.perf_counter()
    stopped_at_max_steps = False
    completed_epochs = start_epoch - 1
    for epoch in range(start_epoch, epochs + 1):
        epoch_samples = list(train_samples)
        random.Random(seed + epoch).shuffle(epoch_samples)
        epoch_step_metrics = []
        epoch_complete = True
        for sample_index, sample in enumerate(epoch_samples, start=1):
            step_started = time.perf_counter()
            total_loss, talker_loss, sub_loss = _loss_for_sample(
                sample=sample,
                hf_model=hf_model,
                trainable_talker=trainable,
                device=selected_device,
                dtype=dtype,
                language=language,
            )
            scalar_values = {
                "loss": float(total_loss.detach().cpu()),
                "talker_loss": float(talker_loss.detach().cpu()),
                "sub_loss": float(sub_loss.detach().cpu()),
            }
            if not all(math.isfinite(value) for value in scalar_values.values()):
                raise SidecarTrainingError(
                    "Training produced a non-finite loss."
                )
            (total_loss / gradient_accumulation_steps).backward()
            global_step += 1
            optimizer_stepped = (
                global_step % gradient_accumulation_steps == 0
                or sample_index == len(epoch_samples)
            )
            if optimizer_stepped:
                torch.nn.utils.clip_grad_norm_(parameters, max_norm=1.0)
                optimizer.step()
                optimizer.zero_grad()
                optimizer_steps += 1
            metric = {
                "epoch": epoch,
                "sample_index": sample_index,
                "source_index": sample.get("source_index"),
                "global_step": global_step,
                "optimizer_step": optimizer_steps,
                "optimizer_stepped": optimizer_stepped,
                "instruction_sha256": sample.get("instruction_sha256"),
                "instruction_token_ids_sha256": sample.get(
                    "instruction_token_ids_sha256"
                ),
                "instruction_prefill_token_count": sample.get(
                    "instruction_prefill_token_count",
                    0,
                ),
                "instruction_placement": sample.get(
                    "instruction_placement",
                    INSTRUCTION_PLACEMENT,
                ),
                **scalar_values,
                "step_seconds": time.perf_counter() - step_started,
                **_memory_metrics(selected_device),
            }
            step_metrics.append(metric)
            epoch_step_metrics.append(metric)
            del total_loss, talker_loss, sub_loss
            _clear_device_cache(selected_device)
            if max_steps is not None and global_step >= max_steps:
                stopped_at_max_steps = True
                epoch_complete = sample_index == len(epoch_samples)
                break

        validation = evaluate_samples(
            samples=validation_samples,
            hf_model=hf_model,
            trainable_talker=trainable,
            device=selected_device,
            dtype=dtype,
            language=language,
        )
        epoch_summary = {
            "epoch": epoch,
            "epoch_complete": epoch_complete,
            "train_sample_count": len(epoch_step_metrics),
            "train_loss": (
                sum(item["loss"] for item in epoch_step_metrics)
                / len(epoch_step_metrics)
                if epoch_step_metrics
                else None
            ),
            "train_talker_loss": (
                sum(item["talker_loss"] for item in epoch_step_metrics)
                / len(epoch_step_metrics)
                if epoch_step_metrics
                else None
            ),
            "train_sub_loss": (
                sum(item["sub_loss"] for item in epoch_step_metrics)
                / len(epoch_step_metrics)
                if epoch_step_metrics
                else None
            ),
            "validation": validation,
            "global_step": global_step,
            "optimizer_steps": optimizer_steps,
        }
        validation_metrics.append(epoch_summary)
        if epoch_complete:
            completed_epochs = epoch
            if selected_mode == "lora" and checkpoint_every_epoch:
                checkpoints.append(
                    save_training_checkpoint(
                        output_dir=output,
                        epoch=epoch,
                        global_step=global_step,
                        optimizer_steps=optimizer_steps,
                        trainable_talker=trainable,
                        optimizer=optimizer,
                        training_contract=training_contract,
                        step_metrics=step_metrics,
                        validation_metrics=validation_metrics,
                        device=selected_device,
                    )
                )
        if stopped_at_max_steps:
            break

    training_seconds = time.perf_counter() - started
    if selected_mode == "lora":
        trainable.save_pretrained(output)
    else:
        from huggingface_hub import save_torch_model

        save_torch_model(
            hf_model,
            output,
            max_shard_size="4GB",
            safe_serialization=True,
        )
        hf_model.config.to_json_file(
            output / "config.json",
            use_diff=False,
        )
        try:
            bundle["processor"].save_pretrained(output)
        except Exception as exc:
            (output / "processor_save_warning.txt").write_text(
                str(exc),
                encoding="utf-8",
            )

    reference_audio = write_canonical_reference_wav(
        ref_audio_path,
        output / "ref_sample.wav",
    )
    ref_text_path = dataset / "ref_text.txt"
    ref_text = (
        ref_text_path.read_text(encoding="utf-8").strip()
        if ref_text_path.is_file()
        else samples[0]["text"]
    )
    (output / "ref_sample.txt").write_text(ref_text, encoding="utf-8")
    metrics = {
        "mode": selected_mode,
        "environment": environment_identity(),
        "training_contract": training_contract,
        "model_load_seconds": bundle["load_seconds"],
        "base_model_revision": bundle["model_revision"],
        "dataset": data_metrics,
        "trainable_parameters": trainable_count,
        "total_talker_parameters": total_count,
        "trainable_percent": 100.0 * trainable_count / total_count,
        "epochs_requested": epochs,
        "epochs_completed": completed_epochs,
        "steps_completed": global_step,
        "optimizer_steps_completed": optimizer_steps,
        "stopped_at_max_steps": stopped_at_max_steps,
        "resumed": resumed,
        "resume_checkpoint": (
            str(checkpoint_path) if checkpoint_path is not None else None
        ),
        "training_seconds_this_run": training_seconds,
        "cumulative_step_seconds": sum(
            float(item.get("step_seconds") or 0.0)
            for item in step_metrics
        ),
        "step_metrics": step_metrics,
        "validation_metrics": validation_metrics,
        "checkpoints": checkpoints,
        "lora_targets": target_metrics,
        "final_memory": _memory_metrics(selected_device),
        "reference_audio": reference_audio,
        "quality_gate": {
            "dataset_reviewed": bool(data_metrics["all_samples_reviewed"]),
            "held_out_validation_present": bool(validation_samples),
            "human_listening_complete": False,
            "production_assignment_supported": False,
        },
    }
    metrics_path = output / "training_metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    files = [path for path in output.rglob("*") if path.is_file()]
    manifest = _save_manifest(
        output_dir=output,
        artifact_format=artifact_format,
        model_name=model_name,
        model_revision=bundle["model_revision"],
        device=selected_device,
        dataset_dir=dataset,
        metrics=metrics,
        files=files,
    )
    return {
        "status": "completed_experimental",
        "artifact": manifest,
        "metrics": metrics,
        "output_dir": str(output),
    }
