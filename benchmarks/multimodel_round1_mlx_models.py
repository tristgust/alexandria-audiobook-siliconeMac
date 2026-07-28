"""Model-specific adapters for non-MOSS Round 1 MLX candidates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from multimodel_round1_mlx_support import (
    GenerationInputError,
    PreparedReferenceError,
    collect_results,
    prepared_reference_wav,
    read_audio,
)


def generate_voxcpm(
    model: Any,
    sample: dict[str, Any],
    reference_path: Path,
    evidence_root: Path,
) -> tuple[np.ndarray, int]:
    control = sample["control"]
    encode_rate = int(getattr(model, "_encode_sample_rate", 16000))
    normalized = prepared_reference_wav(
        evidence_root, reference_path, sample_rate=encode_rate
    )
    results = model.generate(
        text=sample["target_text"],
        ref_audio=str(normalized),
        ref_text=sample["reference"].get("conditioning_transcript"),
        instruct=control["instruct"],
        cfg_value=float(control["cfg_value"]),
        inference_timesteps=int(control["inference_timesteps"]),
        warmup_patches=int(control.get("warmup_patches", 1)),
        max_tokens=1800,
    )
    return collect_results(model, results)


def generate_qwen(
    base_model: Any,
    custom_model: Any,
    sample: dict[str, Any],
    reference_path: Path | None,
    reference_text: str | None,
) -> tuple[np.ndarray, int]:
    control = sample["control"]
    if sample["identity_key"] == "native_qwen_aiden":
        results = custom_model.generate(
            text=sample["target_text"],
            voice="Aiden",
            instruct=control["instruct"],
            lang_code="English",
            temperature=0.75,
            top_k=50,
            top_p=0.95,
            repetition_penalty=1.2,
            max_tokens=1800,
            verbose=False,
        )
    else:
        if reference_path is None or not reference_text:
            raise GenerationInputError(
                "qwen3_tts",
                "clone requires reference audio and transcript",
            )
        results = base_model.generate(
            text=sample["target_text"],
            ref_audio=str(reference_path),
            ref_text=reference_text,
            lang_code="English",
            temperature=0.75,
            top_k=50,
            top_p=0.95,
            repetition_penalty=1.5,
            max_tokens=1800,
            verbose=False,
        )
    model = custom_model if sample["identity_key"] == "native_qwen_aiden" else base_model
    return collect_results(model, results)


def generate_fish(
    model: Any,
    sample: dict[str, Any],
    reference_path: Path,
    reference_text: str,
    evidence_root: Path,
) -> tuple[np.ndarray, int]:
    control = sample["control"]
    tag = control.get("inline_tag")
    text = sample["target_text"] if not tag else f"[{tag}] {sample['target_text']}"
    reference_rate = int(getattr(model, "_encode_sample_rate", 24000))
    normalized = prepared_reference_wav(
        evidence_root, reference_path, sample_rate=reference_rate
    )
    audio, rate = read_audio(
        normalized,
        root=evidence_root,
        always_2d=False,
    )
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if int(rate) != reference_rate:
        raise PreparedReferenceError(
            normalized,
            reference_rate,
            "prepared Fish reference has the wrong sample rate",
        )
    from multimodel_round1_mlx_support import _require_mlx

    results = model.generate(
        text=text,
        ref_audio=_require_mlx().array(audio),
        ref_text=reference_text,
        instruct=control["instruct"],
        max_tokens=1400,
        temperature=float(control["temperature"]),
        top_p=float(control["top_p"]),
        top_k=int(control["top_k"]),
        verbose=False,
    )
    return collect_results(model, results)
