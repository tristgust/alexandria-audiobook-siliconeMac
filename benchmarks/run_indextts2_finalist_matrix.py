#!/usr/bin/env python3
"""Run a resident-model IndexTTS2 finalist matrix against local references.

The matrix is evaluation-only. It loads the pinned local model once, preserves
one independently reviewable WAV and result record per sample, records the
model's internal stage timings, performs no downloads, and never promotes a
backend or Voice assignment for production use.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
from io import StringIO
import json
import math
import random
import re
import resource
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import torch

BENCHMARKS = Path(__file__).resolve().parent
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))

from run_indextts2_emotion_probe import EMOTION_CONTROLS


STAGE_PATTERNS = {
    "gpt_generation_seconds": re.compile(r">> gpt_gen_time: ([0-9.]+) seconds"),
    "gpt_forward_seconds": re.compile(r">> gpt_forward_time: ([0-9.]+) seconds"),
    "s2mel_seconds": re.compile(r">> s2mel_time: ([0-9.]+) seconds"),
    "bigvgan_seconds": re.compile(r">> bigvgan_time: ([0-9.]+) seconds"),
    "model_inference_seconds": re.compile(r">> Total inference time: ([0-9.]+) seconds"),
    "model_reported_rtf": re.compile(r">> RTF: ([0-9.]+)"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--aux-root", required=True)
    parser.add_argument("--matrix", required=True)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--use-fp16", action="store_true")
    parser.add_argument("--greedy", action="store_true")
    parser.add_argument("--diffusion-steps", type=int)
    parser.add_argument("--inference-cfg-rate", type=float)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def audio_metrics(path: Path, word_count: int) -> dict[str, Any]:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = audio.mean(axis=1)
    duration = len(mono) / sample_rate
    rms = float(np.sqrt(np.mean(mono * mono))) if len(mono) else 0.0
    peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
    return {
        "duration_seconds": duration,
        "sample_rate": int(sample_rate),
        "rms_dbfs": 20.0 * math.log10(max(rms, 1e-12)),
        "peak_dbfs": 20.0 * math.log10(max(peak, 1e-12)),
        "words_per_second": word_count / duration if duration else None,
    }


def parse_stage_timings(log: str) -> dict[str, float | None]:
    timings: dict[str, float | None] = {}
    for key, pattern in STAGE_PATTERNS.items():
        match = pattern.search(log)
        timings[key] = float(match.group(1)) if match else None
    known = [
        timings.get("gpt_generation_seconds"),
        timings.get("gpt_forward_seconds"),
        timings.get("s2mel_seconds"),
        timings.get("bigvgan_seconds"),
    ]
    if all(value is not None for value in known):
        total = sum(float(value) for value in known if value is not None)
        timings["measured_stage_sum_seconds"] = total
        timings["gpt_share_of_measured_stages"] = (
            (float(known[0]) + float(known[1])) / total if total else None
        )
    else:
        timings["measured_stage_sum_seconds"] = None
        timings["gpt_share_of_measured_stages"] = None
    return timings


def normalized_sample(raw: dict[str, Any], seen: set[str]) -> dict[str, Any]:
    sample_id = str(raw.get("sample_id") or "").strip()
    if not sample_id or sample_id in seen:
        raise ValueError(f"Duplicate or empty sample_id: {sample_id!r}")
    seen.add(sample_id)

    reference_audio = Path(str(raw.get("reference_audio") or "")).expanduser().resolve()
    if not reference_audio.is_file():
        raise FileNotFoundError(reference_audio)

    emotion_audio_value = raw.get("emotion_audio_prompt")
    emotion_audio_prompt = None
    if emotion_audio_value:
        emotion_audio_prompt = Path(str(emotion_audio_value)).expanduser().resolve()
        if not emotion_audio_prompt.is_file():
            raise FileNotFoundError(emotion_audio_prompt)

    text = str(raw.get("text") or "").strip()
    if not text:
        raise ValueError(f"Sample {sample_id!r} has no text.")

    direction = str(raw.get("direction") or "identity").strip()
    custom_control = raw.get("control")
    if custom_control is not None:
        if not isinstance(custom_control, dict):
            raise ValueError(f"control must be an object for {sample_id!r}.")
        custom_control = deepcopy(custom_control)
        allowed_control = {"emo_vector", "use_emo_text", "emo_text", "emo_alpha"}
        unexpected_control = sorted(set(custom_control) - allowed_control)
        if unexpected_control:
            raise ValueError(
                f"Unsupported control fields for {sample_id!r}: {unexpected_control}"
            )
        has_vector = custom_control.get("emo_vector") is not None
        has_text = bool(custom_control.get("use_emo_text"))
        if has_vector == has_text:
            raise ValueError(
                f"Custom control for {sample_id!r} must choose exactly one of "
                "emo_vector or use_emo_text."
            )
        if has_vector:
            vector = list(custom_control["emo_vector"])
            if len(vector) != 8 or any(float(value) < 0.0 for value in vector):
                raise ValueError(
                    f"emo_vector must contain eight non-negative values for {sample_id!r}."
                )
            custom_control["emo_vector"] = [float(value) for value in vector]
        if has_text and not str(custom_control.get("emo_text") or "").strip():
            raise ValueError(f"emo_text is required for {sample_id!r}.")
        if custom_control.get("emo_alpha") is not None:
            alpha = float(custom_control["emo_alpha"])
            if not 0.0 <= alpha <= 1.0:
                raise ValueError(f"emo_alpha must be within [0, 1] for {sample_id!r}.")
            custom_control["emo_alpha"] = alpha
    elif emotion_audio_prompt is None and direction != "identity" and direction not in EMOTION_CONTROLS:
        raise ValueError(f"Unknown direction {direction!r} for {sample_id!r}.")
    if custom_control is not None and emotion_audio_prompt is not None:
        raise ValueError(
            f"Sample {sample_id!r} cannot combine custom control and emotion audio."
        )

    emotion_strength = raw.get("emotion_strength")
    if emotion_strength is not None:
        emotion_strength = float(emotion_strength)
        if not 0.0 <= emotion_strength <= 1.0:
            raise ValueError(
                f"emotion_strength must be within [0, 1] for {sample_id!r}."
            )

    generation = dict(raw.get("generation") or {})
    allowed_generation = {
        "top_p",
        "top_k",
        "temperature",
        "num_beams",
        "length_penalty",
        "repetition_penalty",
        "max_mel_tokens",
        "max_text_tokens_per_segment",
        "interval_silence",
    }
    unexpected = sorted(set(generation) - allowed_generation)
    if unexpected:
        raise ValueError(
            f"Unsupported generation controls for {sample_id!r}: {unexpected}"
        )

    return {
        "sample_id": sample_id,
        "reference_audio": reference_audio,
        "reference_label": str(raw.get("reference_label") or reference_audio.stem),
        "text": text,
        "line_label": str(raw.get("line_label") or "test line"),
        "direction": direction,
        "emotion_strength": emotion_strength,
        "emotion_audio_prompt": emotion_audio_prompt,
        "emotion_reference_label": str(
            raw.get("emotion_reference_label")
            or (emotion_audio_prompt.stem if emotion_audio_prompt else "")
        ),
        "custom_control": custom_control,
        "seed": int(raw.get("seed", 1001)),
        "generation": generation,
    }


def build_control(sample: dict[str, Any]) -> dict[str, Any]:
    emotion_audio_prompt = sample.get("emotion_audio_prompt")
    if emotion_audio_prompt is not None:
        return {
            "emo_audio_prompt": str(emotion_audio_prompt),
            "emo_alpha": (
                sample.get("emotion_strength")
                if sample.get("emotion_strength") is not None
                else 0.6
            ),
        }
    custom_control = sample.get("custom_control")
    if custom_control is not None:
        control = deepcopy(custom_control)
    elif sample["direction"] == "identity":
        return {}
    else:
        control = deepcopy(EMOTION_CONTROLS[sample["direction"]])
    strength = sample.get("emotion_strength")
    if strength is not None:
        control["emo_alpha"] = strength
    return control


def install_gpt_generation_overrides(model: Any, *, greedy: bool) -> dict[str, Any]:
    if not greedy:
        return {"greedy_generation": False}
    original = model.gpt.inference_speech

    def greedy_inference(*args, **kwargs):
        kwargs["do_sample"] = False
        kwargs["num_beams"] = 1
        return original(*args, **kwargs)

    model.gpt.inference_speech = greedy_inference
    return {"greedy_generation": True}


def install_cfm_overrides(
    model: Any,
    *,
    diffusion_steps: int | None,
    inference_cfg_rate: float | None,
) -> dict[str, Any]:
    if diffusion_steps is not None and diffusion_steps < 1:
        raise ValueError("diffusion_steps must be positive.")
    if inference_cfg_rate is not None and not 0.0 <= inference_cfg_rate <= 2.0:
        raise ValueError("inference_cfg_rate must be within [0, 2].")
    cfm = model.s2mel.models["cfm"]
    original = cfm.inference

    def inference_override(
        mu,
        x_lens,
        prompt,
        style,
        f0,
        n_timesteps,
        temperature=1.0,
        inference_cfg_rate=0.5,
    ):
        return original(
            mu,
            x_lens,
            prompt,
            style,
            f0,
            diffusion_steps if diffusion_steps is not None else n_timesteps,
            temperature=temperature,
            inference_cfg_rate=(
                inference_cfg_rate
                if inference_cfg_rate_override is None
                else inference_cfg_rate_override
            ),
        )

    inference_cfg_rate_override = inference_cfg_rate
    if diffusion_steps is not None or inference_cfg_rate is not None:
        cfm.inference = inference_override
    return {
        "diffusion_steps_override": diffusion_steps,
        "inference_cfg_rate_override": inference_cfg_rate,
    }


def main() -> int:
    args = parse_args()
    from indextts.infer_v2 import IndexTTS2

    model_dir = Path(args.model_dir).expanduser().resolve()
    aux_root = Path(args.aux_root).expanduser().resolve()
    matrix_path = Path(args.matrix).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if not matrix_path.is_file():
        raise FileNotFoundError(matrix_path)

    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    seen: set[str] = set()
    samples = [normalized_sample(item, seen) for item in matrix.get("samples") or []]
    if not samples:
        raise ValueError("Matrix samples are required.")

    aux_paths = {
        "w2v_bert": str(aux_root / "w2v-bert-2.0"),
        "semantic_codec": str(aux_root / "semantic_codec" / "model.safetensors"),
        "campplus": str(aux_root / "campplus_cn_common.bin"),
        "bigvgan": str(aux_root / "bigvgan"),
    }
    for path in [model_dir, *map(Path, aux_paths.values())]:
        if not path.exists():
            raise FileNotFoundError(path)

    output_dir.mkdir(parents=True, exist_ok=True)
    torch.set_float32_matmul_precision("high")
    load_started = time.perf_counter()
    model = IndexTTS2(
        cfg_path=str(model_dir / "config.yaml"),
        model_dir=str(model_dir),
        use_fp16=args.use_fp16,
        device=args.device,
        use_cuda_kernel=False,
        use_deepspeed=False,
        use_accel=False,
        use_torch_compile=False,
        aux_paths=aux_paths,
    )
    gpt_overrides = install_gpt_generation_overrides(model, greedy=args.greedy)
    cfm_overrides = install_cfm_overrides(
        model,
        diffusion_steps=args.diffusion_steps,
        inference_cfg_rate=args.inference_cfg_rate,
    )
    load_seconds = time.perf_counter() - load_started

    results: list[dict[str, Any]] = []
    for index, sample in enumerate(samples, start=1):
        random.seed(sample["seed"])
        np.random.seed(sample["seed"])
        torch.manual_seed(sample["seed"])

        sample_dir = output_dir / sample["sample_id"]
        sample_dir.mkdir(parents=True, exist_ok=True)
        output_path = sample_dir / "audio.wav"
        control = build_control(sample)
        infer_kwargs = {**control, **sample["generation"]}

        captured = StringIO()
        generation_started = time.perf_counter()
        with torch.inference_mode(), redirect_stdout(captured):
            returned = model.infer(
                spk_audio_prompt=str(sample["reference_audio"]),
                text=sample["text"],
                output_path=str(output_path),
                use_random=False,
                verbose=False,
                **infer_kwargs,
            )
        generation_seconds = time.perf_counter() - generation_started
        model_log = captured.getvalue()
        if not output_path.is_file():
            raise RuntimeError(
                f"IndexTTS2 did not create {output_path}; returned {returned!r}"
            )

        metrics = audio_metrics(output_path, len(sample["text"].split()))
        stage_timings = parse_stage_timings(model_log)
        record = {
            "schema_version": 1,
            "sample_index": index,
            "sample_count": len(samples),
            "sample_id": sample["sample_id"],
            "candidate": "indextts2",
            "device": args.device,
            "reference_label": sample["reference_label"],
            "reference_audio_sha256": sha256_file(sample["reference_audio"]),
            "reference_kind": "supplied_recording_clone",
            "line_label": sample["line_label"],
            "target_text_sha256": sha256_text(sample["text"]),
            "expected_text": sample["text"],
            "direction": sample["direction"],
            "emotion_strength": sample["emotion_strength"],
            "emotion_reference_label": sample["emotion_reference_label"] or None,
            "emotion_audio_sha256": (
                sha256_file(sample["emotion_audio_prompt"])
                if sample["emotion_audio_prompt"] is not None
                else None
            ),
            "seed": sample["seed"],
            "control": control,
            "generation_controls": sample["generation"],
            "runtime_controls": {
                "use_fp16": args.use_fp16,
                **gpt_overrides,
                **cfm_overrides,
            },
            "shared_model_load_seconds": load_seconds,
            "generation_seconds": generation_seconds,
            "real_time_factor": generation_seconds / metrics["duration_seconds"],
            "stage_timings": stage_timings,
            "peak_rss_gib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            / (1024**3),
            "audio": metrics,
            "output_file": str(output_path.relative_to(output_dir)),
            "model_log_tail": model_log[-4000:],
            "production_promotion_allowed": False,
            "manual_listening_required": True,
        }
        (sample_dir / "result.json").write_text(json.dumps(record, indent=2) + "\n")
        results.append(record)
        print(
            json.dumps(
                {
                    "sample_id": sample["sample_id"],
                    "direction": sample["direction"],
                    "reference_label": sample["reference_label"],
                    "rtf": record["real_time_factor"],
                    "gpt_share": stage_timings.get("gpt_share_of_measured_stages"),
                    "output": str(output_path),
                }
            ),
            flush=True,
        )

    measured_gpt_shares = [
        float(item["stage_timings"]["gpt_share_of_measured_stages"])
        for item in results
        if item["stage_timings"]["gpt_share_of_measured_stages"] is not None
    ]
    summary = {
        "schema_version": 1,
        "candidate": "indextts2",
        "device": args.device,
        "runtime_controls": {
            "use_fp16": args.use_fp16,
            **gpt_overrides,
            **cfm_overrides,
        },
        "matrix_sha256": sha256_file(matrix_path),
        "sample_count": len(results),
        "shared_model_load_seconds": load_seconds,
        "mean_real_time_factor": sum(item["real_time_factor"] for item in results)
        / len(results),
        "mean_gpt_share_of_measured_stages": (
            sum(measured_gpt_shares) / len(measured_gpt_shares)
            if measured_gpt_shares
            else None
        ),
        "peak_rss_gib": max(item["peak_rss_gib"] for item in results),
        "results": results,
        "production_promotion_allowed": False,
        "manual_listening_required": True,
    }
    (output_dir / "matrix_result.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: value for key, value in summary.items() if key != "results"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
