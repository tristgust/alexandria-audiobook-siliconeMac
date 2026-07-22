from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import math
import os
import platform
import resource
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
BENCHMARKS = ROOT / "benchmarks"
PYTHON = ROOT / "app" / "env" / "bin" / "python"
WORKER_MARKER = "EXPRESSIVE_CLONE_WORKER_JSON="
SCHEMA_VERSION = 1
DEFAULT_SEEDS = (314159, 271828, 161803)

if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))

from audio_processing import temporary_mono_wav  # noqa: E402
from expressive_clone_candidates import (  # noqa: E402
    comparison_candidate_keys,
    expressive_clone_candidate,
    expressive_clone_candidate_catalog,
    expressive_clone_candidate_status,
    primary_candidate_keys,
)
from hf_access import cached_snapshot_status  # noqa: E402
from model_registry import model_spec  # noqa: E402
from transcription_evaluator import (  # noqa: E402
    EVALUATOR_MODEL_KEY,
    evaluate_transcriptions,
    evaluator_identity,
    word_error_rate as evaluator_word_error_rate,
)


DEFAULT_DIRECTIONS = (
    {
        "key": "neutral",
        "instruction": (
            "Natural, clear, conversational delivery. Preserve the supplied "
            "speaker identity and accent."
        ),
        "fish_tag": None,
    },
    {
        "key": "urgent",
        "instruction": (
            "Urgent but controlled; quicker pace, focused breath, and clear "
            "forward momentum without shouting."
        ),
        "fish_tag": "urgent",
    },
    {
        "key": "restrained_anger",
        "instruction": (
            "Restrained anger; hard consonants, compressed intensity, and "
            "deliberate control rather than yelling."
        ),
        "fish_tag": "angry",
    },
    {
        "key": "panic",
        "instruction": (
            "Panic breaking through; uneven breath, accelerated pace, and "
            "frightened urgency while remaining intelligible."
        ),
        "fish_tag": "panicked",
    },
    {
        "key": "grief",
        "instruction": (
            "Quiet grief; slower pace, fragile breath, softened attack, and "
            "contained sorrow."
        ),
        "fish_tag": "sad",
    },
    {
        "key": "whisper",
        "instruction": (
            "Whispered delivery; very low volume, intimate breath, and clear "
            "articulation."
        ),
        "fish_tag": "whisper",
    },
    {
        "key": "sarcasm",
        "instruction": (
            "Dry sarcasm; understated amusement, precise timing, and a slight "
            "knowing edge without becoming broad comedy."
        ),
        "fish_tag": "sarcastic",
    },
)

CHATTERBOX_NUMERIC_PROXIES = {
    "neutral": {"exaggeration": 0.10, "cfg_weight": 0.50},
    "urgent": {"exaggeration": 0.55, "cfg_weight": 0.40},
    "restrained_anger": {"exaggeration": 0.50, "cfg_weight": 0.45},
    "panic": {"exaggeration": 0.85, "cfg_weight": 0.30},
    "grief": {"exaggeration": 0.45, "cfg_weight": 0.55},
    "whisper": {"exaggeration": 0.20, "cfg_weight": 0.60},
    "sarcasm": {"exaggeration": 0.35, "cfg_weight": 0.50},
}

# Turbo has native paralinguistic tags, not a general emotion language. Only
# mappings that use a documented event token are emitted automatically.
CHATTERBOX_TURBO_TAGS = {
    "neutral": None,
    "sarcasm": "chuckle",
}

MAX_TOKENS = {
    "fish_s2_pro": 1024,
    "chatterbox_original": 1000,
    "chatterbox_turbo": 800,
    "tada_1b": 1024,
    "moss_tts_nano": 375,
    "moss_tts_local_v15": 4096,
    "qwen_icl_patch_baseline": 2000,
    "voxcpm2_baseline": 2000,
}


@dataclass(frozen=True)
class PreparedControl:
    supported: bool
    text: str
    reference_audio: str
    reference_text: str
    instruction: str | None
    kwargs: dict[str, Any]
    summary: dict[str, Any]
    skip_reason: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def repository_head() -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def system_hardware() -> dict[str, Any]:
    result = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
    }
    if platform.system() != "Darwin":
        return result
    completed = subprocess.run(
        ["system_profiler", "SPHardwareDataType", "-json"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        return result
    try:
        rows = json.loads(completed.stdout).get("SPHardwareDataType", [])
    except json.JSONDecodeError:
        return result
    if rows:
        row = rows[0]
        result.update(
            {
                "model_name": row.get("machine_name"),
                "model_identifier": row.get("machine_model"),
                "chip": row.get("chip_type"),
                "memory": row.get("physical_memory"),
            }
        )
    return result


def _peak_rss_gib() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if platform.system() == "Darwin":
        return value / (1024**3)
    return value * 1024 / (1024**3)


def _monitor_peak_rss(stop: list[bool], peak: list[int]) -> None:
    try:
        import psutil
    except ImportError:
        return
    process = psutil.Process()
    while not stop[0]:
        peak[0] = max(peak[0], process.memory_info().rss)
        time.sleep(0.05)


def _word_error_rate(reference: str, hypothesis: str) -> float:
    return evaluator_word_error_rate(reference, hypothesis)


def objective_audio_metrics(
    path: str | Path,
    *,
    word_count: int | None = None,
) -> dict[str, Any]:
    audio, sample_rate = sf.read(
        str(path),
        dtype="float32",
        always_2d=False,
    )
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if not len(audio):
        raise ValueError("Audio output is empty.")
    duration = len(audio) / float(sample_rate)
    peak = float(np.max(np.abs(audio)))
    rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
    epsilon = 1e-12

    frame = max(32, int(sample_rate * 0.02))
    hop = max(16, int(sample_rate * 0.01))
    frame_rms = []
    for start in range(0, max(1, len(audio) - frame + 1), hop):
        segment = audio[start : start + frame]
        if not len(segment):
            continue
        frame_rms.append(
            float(np.sqrt(np.mean(np.square(segment, dtype=np.float64))))
        )
    frame_rms_array = np.asarray(frame_rms or [0.0], dtype=np.float64)
    frame_db = 20.0 * np.log10(np.maximum(frame_rms_array, epsilon))
    silence_mask = frame_db <= -40.0
    longest_silence_frames = 0
    current_silence_frames = 0
    for silent in silence_mask:
        if silent:
            current_silence_frames += 1
            longest_silence_frames = max(
                longest_silence_frames,
                current_silence_frames,
            )
        else:
            current_silence_frames = 0

    crossings = np.count_nonzero(np.diff(np.signbit(audio)))
    metrics = {
        "duration_seconds": duration,
        "sample_rate": int(sample_rate),
        "channels": 1,
        "frames": int(len(audio)),
        "rms_dbfs": 20.0 * math.log10(max(rms, epsilon)),
        "peak_dbfs": 20.0 * math.log10(max(peak, epsilon)),
        "silence_fraction_below_minus_40_dbfs": float(np.mean(silence_mask)),
        "longest_silence_seconds": (
            longest_silence_frames * hop / float(sample_rate)
        ),
        "frame_level_dynamic_range_db": float(
            np.percentile(frame_db, 95) - np.percentile(frame_db, 10)
        ),
        "zero_crossing_rate": crossings / max(1, len(audio) - 1),
    }
    if word_count is not None:
        metrics["words_per_second"] = word_count / duration
    return metrics


def _direction_map(
    directions: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> dict[str, dict[str, Any]]:
    result = {}
    for item in directions:
        key = str(item.get("key") or "").strip()
        instruction = str(item.get("instruction") or "").strip()
        if not key or not instruction:
            raise ValueError("Every direction requires a key and instruction.")
        if key in result:
            raise ValueError(f"Duplicate direction key: {key}.")
        result[key] = {
            "key": key,
            "instruction": instruction,
            "fish_tag": item.get("fish_tag"),
        }
    return result


def _reference_for_direction(
    direction_key: str,
    *,
    primary_audio: str,
    primary_text: str,
    reference_map: dict[str, Any],
) -> tuple[str, str] | None:
    if direction_key == "neutral":
        return primary_audio, primary_text
    entry = reference_map.get(direction_key)
    if not isinstance(entry, dict):
        return None
    audio = str(entry.get("audio") or "").strip()
    text = str(entry.get("text") or "").strip()
    if not audio or not text:
        return None
    return audio, text


def prepare_control(
    candidate_key: str,
    direction: dict[str, Any],
    *,
    text: str,
    primary_reference_audio: str,
    primary_reference_text: str,
    reference_map: dict[str, Any] | None = None,
) -> PreparedControl:
    candidate = expressive_clone_candidate(candidate_key)
    reference_map = reference_map or {}
    direction_key = direction["key"]
    instruction = direction["instruction"]
    instruction_hash = sha256_text(instruction)
    summary = {
        "mode": candidate.control_mode,
        "direction": direction_key,
        "instruction_sha256": instruction_hash,
        "control_applied": True,
        "semantic_control_claimed": False,
    }

    if candidate.control_mode == "inline_freeform_tags":
        tag = direction.get("fish_tag")
        prepared_text = text if not tag else f"[{tag}] {text}"
        summary.update(
            {
                "translation": "inline_freeform_tag" if tag else "plain_text",
                "inline_tag": tag,
                "semantic_control_claimed": bool(tag),
            }
        )
        return PreparedControl(
            True,
            prepared_text,
            primary_reference_audio,
            primary_reference_text,
            None,
            {},
            summary,
        )

    if candidate.control_mode == "numeric_exaggeration_cfg":
        values = CHATTERBOX_NUMERIC_PROXIES[direction_key]
        summary.update(
            {
                "translation": "numeric_proxy",
                "numeric_proxy": values,
                "semantic_control_claimed": False,
            }
        )
        return PreparedControl(
            True,
            text,
            primary_reference_audio,
            primary_reference_text,
            None,
            dict(values),
            summary,
        )

    if candidate.control_mode == "native_event_tags":
        if direction_key not in CHATTERBOX_TURBO_TAGS:
            summary.update(
                {
                    "control_applied": False,
                    "translation": "unsupported_direction",
                }
            )
            return PreparedControl(
                False,
                text,
                primary_reference_audio,
                primary_reference_text,
                None,
                {},
                summary,
                (
                    "Chatterbox-Turbo has no native event-tag translation for "
                    f"{direction_key}."
                ),
            )
        tag = CHATTERBOX_TURBO_TAGS[direction_key]
        prepared_text = text if tag is None else f"{text} [{tag}]"
        summary.update(
            {
                "translation": "native_event_tag" if tag else "plain_text",
                "inline_tag": tag,
                "semantic_control_claimed": False,
            }
        )
        return PreparedControl(
            True,
            prepared_text,
            primary_reference_audio,
            primary_reference_text,
            None,
            {},
            summary,
        )

    if candidate.control_mode == "reference_style_bank":
        reference = _reference_for_direction(
            direction_key,
            primary_audio=primary_reference_audio,
            primary_text=primary_reference_text,
            reference_map=reference_map,
        )
        if reference is None:
            summary.update(
                {
                    "control_applied": False,
                    "translation": "reference_required",
                }
            )
            return PreparedControl(
                False,
                text,
                primary_reference_audio,
                primary_reference_text,
                None,
                {},
                summary,
                f"No approved {direction_key} reference clip was supplied.",
            )
        summary.update(
            {
                "translation": "direction_specific_reference",
                "reference_audio_sha256": sha256_file(reference[0]),
                "reference_text_sha256": sha256_text(reference[1]),
                "semantic_control_claimed": direction_key != "neutral",
            }
        )
        return PreparedControl(
            True,
            text,
            reference[0],
            reference[1],
            None,
            {},
            summary,
        )

    if candidate.control_mode in {
        "instruction_and_pause_syntax",
        "untrained_instruction_embedding_patch",
        "freeform_instruction_comparison",
    }:
        summary.update(
            {
                "translation": "instruction_field",
                "semantic_control_claimed": (
                    candidate.control_mode == "instruction_and_pause_syntax"
                ),
            }
        )
        return PreparedControl(
            True,
            text,
            primary_reference_audio,
            primary_reference_text,
            instruction,
            {},
            summary,
        )

    raise ValueError(f"Unsupported control mode: {candidate.control_mode}.")


def _disable_optional_sklearn() -> None:
    import transformers.utils as transformers_utils
    import transformers.utils.import_utils as import_utils

    unavailable = lambda: False
    import_utils.is_sklearn_available = unavailable
    transformers_utils.is_sklearn_available = unavailable


def _candidate_snapshot_paths(
    status: dict[str, Any],
) -> dict[str, Path]:
    paths = {}
    for item in status["repositories"]:
        snapshot = item["cache"].get("snapshot_path")
        if not snapshot:
            continue
        paths[item["repo_id"]] = Path(snapshot).resolve()
    return paths


def _scipy_free_resample_audio(
    audio: Any,
    orig_sample_rate: int,
    sample_rate: int,
    axis: int = -1,
):
    """Resample Chatterbox conditioning audio without importing SciPy."""
    import mlx.core as mx
    import soxr

    if int(orig_sample_rate) == int(sample_rate):
        return audio
    was_mlx = isinstance(audio, mx.array)
    array = np.asarray(audio, dtype=np.float32)
    normalized_axis = int(axis) % array.ndim
    time_first = np.moveaxis(array, normalized_axis, 0)
    original_tail = time_first.shape[1:]
    channels = time_first.reshape(time_first.shape[0], -1)
    resampled = soxr.resample(
        channels,
        int(orig_sample_rate),
        int(sample_rate),
        quality="HQ",
    )
    restored = np.asarray(resampled, dtype=np.float32).reshape(
        (resampled.shape[0], *original_tail)
    )
    restored = np.moveaxis(restored, 0, normalized_axis)
    return mx.array(restored) if was_mlx else restored


def _install_chatterbox_scipy_free_resampler(candidate_key: str) -> None:
    import importlib

    modules_by_candidate = {
        "chatterbox_original": (
            "mlx_audio.tts.models.chatterbox.chatterbox",
            "mlx_audio.tts.models.chatterbox.s3gen.s3gen",
            "mlx_audio.tts.models.chatterbox.voice_encoder.voice_encoder",
        ),
        "chatterbox_turbo": (
            "mlx_audio.tts.models.chatterbox_turbo.chatterbox_turbo",
            "mlx_audio.tts.models.chatterbox_turbo.models.s3gen.s3gen",
            "mlx_audio.tts.models.chatterbox_turbo.models.voice_encoder.voice_encoder",
        ),
    }
    for module_name in modules_by_candidate.get(candidate_key, ()):
        module = importlib.import_module(module_name)
        if hasattr(module, "resample_audio"):
            module.resample_audio = _scipy_free_resample_audio


def _install_pinned_snapshot_router(
    dependency_paths: dict[str, Path],
):
    """Temporarily route MLX-Audio's internal Hub lookups to exact snapshots."""
    import huggingface_hub

    original = huggingface_hub.snapshot_download

    def local_snapshot_download(repo_id: str, *args: Any, **kwargs: Any) -> str:
        del args, kwargs
        target = dependency_paths.get(repo_id)
        if target is None:
            raise RuntimeError(
                f"Benchmark worker refused an unpinned model request: {repo_id}."
            )
        return str(target)

    huggingface_hub.snapshot_download = local_snapshot_download
    return original


def _load_candidate_model(
    candidate_key: str,
    status: dict[str, Any],
):
    _disable_optional_sklearn()
    from mlx_audio.tts.utils import load_model
    from mlx_audio.utils import get_model_name_parts

    candidate = expressive_clone_candidate(candidate_key)
    paths = _candidate_snapshot_paths(status)
    main_path = paths[candidate.model_repo_id]
    _install_chatterbox_scipy_free_resampler(candidate_key)
    restore_snapshot_download = None
    if candidate_key == "chatterbox_original":
        restore_snapshot_download = _install_pinned_snapshot_router(paths)
    try:
        model = load_model(
            main_path,
            model_name_parts=get_model_name_parts(candidate.model_repo_id),
            strict=False,
        )
    finally:
        if restore_snapshot_download is not None:
            import huggingface_hub

            huggingface_hub.snapshot_download = restore_snapshot_download
    return model, paths


def _collect_results(model: Any, results: Any) -> tuple[np.ndarray, int]:
    arrays = []
    sample_rate = int(getattr(model, "sample_rate", 24000))
    for result in results:
        audio = np.asarray(result.audio, dtype=np.float32).reshape(-1)
        if len(audio):
            arrays.append(audio)
        result_rate = getattr(result, "sample_rate", None)
        if result_rate:
            sample_rate = int(result_rate)
    if not arrays:
        raise RuntimeError("MLX-Audio returned no audio.")
    return (
        arrays[0] if len(arrays) == 1 else np.concatenate(arrays),
        sample_rate,
    )


def _generate_with_candidate(
    *,
    candidate_key: str,
    model: Any,
    dependency_paths: dict[str, Path],
    prepared: PreparedControl,
    seed: int,
    output_path: Path,
) -> dict[str, Any]:
    import mlx.core as mx

    mx.random.seed(int(seed))
    sample_rate = int(getattr(model, "sample_rate", 24000))
    reference_sample_rate = int(
        getattr(model, "_encode_sample_rate", sample_rate)
    )
    max_tokens = MAX_TOKENS[candidate_key]
    started = time.perf_counter()

    with temporary_mono_wav(
        prepared.reference_audio,
        sample_rate=reference_sample_rate,
    ) as normalized_reference:
        if candidate_key == "fish_s2_pro":
            reference_audio, reference_rate = sf.read(
                normalized_reference,
                dtype="float32",
                always_2d=False,
            )
            if reference_audio.ndim > 1:
                reference_audio = np.mean(reference_audio, axis=1)
            if int(reference_rate) != reference_sample_rate:
                raise RuntimeError(
                    "Fish reference normalization produced the wrong sample rate."
                )
            results = model.generate(
                text=prepared.text,
                ref_audio=mx.array(reference_audio),
                ref_text=prepared.reference_text,
                max_tokens=max_tokens,
                temperature=0.7,
                top_p=0.7,
                top_k=30,
                verbose=False,
            )
        elif candidate_key == "chatterbox_original":
            results = model.generate(
                text=prepared.text,
                ref_audio=str(normalized_reference),
                max_tokens=max_tokens,
                temperature=0.8,
                repetition_penalty=1.2,
                top_p=1.0,
                verbose=False,
                **prepared.kwargs,
            )
        elif candidate_key == "chatterbox_turbo":
            results = model.generate(
                text=prepared.text,
                ref_audio=str(normalized_reference),
                sample_rate=reference_sample_rate,
                max_tokens=max_tokens,
                temperature=0.8,
                top_p=0.95,
                top_k=1000,
                repetition_penalty=1.2,
                verbose=False,
            )
        elif candidate_key == "tada_1b":
            reference_audio, reference_rate = sf.read(
                normalized_reference,
                dtype="float32",
                always_2d=False,
            )
            if reference_audio.ndim > 1:
                reference_audio = np.mean(reference_audio, axis=1)
            if int(reference_rate) != reference_sample_rate:
                raise RuntimeError(
                    "TADA reference normalization produced the wrong sample rate."
                )
            results = model.generate(
                text=prepared.text,
                ref_audio=mx.array(reference_audio),
                ref_text=prepared.reference_text,
                max_tokens=max_tokens,
                temperature=0.6,
                top_p=0.9,
                repetition_penalty=1.1,
                verbose=False,
            )
        elif candidate_key in {"moss_tts_nano", "moss_tts_local_v15"}:
            tokenizer_repo = (
                "mlx-community/MOSS-Audio-Tokenizer-Nano"
                if candidate_key == "moss_tts_nano"
                else "OpenMOSS-Team/MOSS-Audio-Tokenizer-v2"
            )
            kwargs = {
                "text": prepared.text,
                "ref_audio": str(normalized_reference),
                "ref_text": prepared.reference_text,
                "max_tokens": max_tokens,
                "audio_tokenizer_source": str(
                    dependency_paths[tokenizer_repo]
                ),
                "ref_audio_sample_rate": reference_sample_rate,
                "stream": False,
            }
            if candidate_key == "moss_tts_nano":
                kwargs["mode"] = "voice_clone"
            else:
                kwargs["mode"] = "generation"
                kwargs["instruction"] = prepared.instruction
            results = model.generate(**kwargs)
        elif candidate_key == "qwen_icl_patch_baseline":
            from mlx_backend import MLXBackend

            MLXBackend._enable_qwen_icl_instruction(model)
            model._alexandria_icl_instruction = prepared.instruction
            try:
                results = model.generate(
                    text=prepared.text,
                    ref_audio=str(normalized_reference),
                    ref_text=prepared.reference_text,
                    lang_code="English",
                    temperature=0.75,
                    top_k=50,
                    top_p=0.95,
                    repetition_penalty=1.5,
                    max_tokens=max_tokens,
                )
                audio, sample_rate = _collect_results(model, results)
            finally:
                model._alexandria_icl_instruction = None
            elapsed = time.perf_counter() - started
            output_path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(output_path, audio, sample_rate)
            return {
                "elapsed_seconds": elapsed,
                "sample_rate": sample_rate,
                "post_generation_prosody_applied": False,
            }
        elif candidate_key == "voxcpm2_baseline":
            results = model.generate(
                text=prepared.text,
                ref_audio=str(normalized_reference),
                ref_text=prepared.reference_text,
                instruct=prepared.instruction,
                cfg_value=2.0,
                inference_timesteps=10,
                max_tokens=max_tokens,
            )
        else:
            raise ValueError(candidate_key)

        audio, sample_rate = _collect_results(model, results)

    elapsed = time.perf_counter() - started
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output_path, audio, sample_rate)
    return {
        "elapsed_seconds": elapsed,
        "sample_rate": sample_rate,
        "post_generation_prosody_applied": False,
    }


def _sample_id(candidate: str, direction: str, seed: int) -> str:
    return hashlib.sha256(
        f"{candidate}\0{direction}\0{seed}".encode("utf-8")
    ).hexdigest()[:16]


def _run_candidate_worker(payload: dict[str, Any]) -> dict[str, Any]:
    candidate_key = payload["candidate_key"]
    status = payload["candidate_status"]
    output_dir = Path(payload["output_dir"]).resolve()
    directions = _direction_map(payload["directions"])
    seeds = [int(item) for item in payload["seeds"]]
    reference_map = payload.get("reference_map") or {}

    stop = [False]
    peak = [0]
    monitor = threading.Thread(
        target=_monitor_peak_rss,
        args=(stop, peak),
        daemon=True,
    )
    monitor.start()
    load_started = time.perf_counter()
    model, dependency_paths = _load_candidate_model(candidate_key, status)
    load_seconds = time.perf_counter() - load_started

    measurements = []
    skipped = []
    errors = []
    for direction_key, direction in directions.items():
        prepared = prepare_control(
            candidate_key,
            direction,
            text=payload["text"],
            primary_reference_audio=payload["reference_audio"],
            primary_reference_text=payload["reference_text"],
            reference_map=reference_map,
        )
        if not prepared.supported:
            skipped.append(
                {
                    "direction": direction_key,
                    "reason": prepared.skip_reason,
                    "control": prepared.summary,
                }
            )
            continue
        for seed in seeds:
            sample_id = _sample_id(candidate_key, direction_key, seed)
            target = output_dir / f"sample_{sample_id}.wav"
            try:
                timing = _generate_with_candidate(
                    candidate_key=candidate_key,
                    model=model,
                    dependency_paths=dependency_paths,
                    prepared=prepared,
                    seed=seed,
                    output_path=target,
                )
                metrics = objective_audio_metrics(
                    target,
                    word_count=len(payload["text"].split()),
                )
                duration = metrics["duration_seconds"]
                measurements.append(
                    {
                        "sample_id": sample_id,
                        "candidate": candidate_key,
                        "direction": direction_key,
                        "seed": seed,
                        "output_file": target.name,
                        "output_sha256": sha256_file(target),
                        "elapsed_seconds": timing["elapsed_seconds"],
                        "real_time_factor": (
                            timing["elapsed_seconds"] / duration
                            if duration > 0
                            else None
                        ),
                        "audio": metrics,
                        "control": prepared.summary,
                        "reference_audio_sha256": sha256_file(
                            prepared.reference_audio
                        ),
                        "reference_text_sha256": sha256_text(
                            prepared.reference_text
                        ),
                        "post_generation_prosody_applied": False,
                    }
                )
            except Exception as exc:
                errors.append(
                    {
                        "candidate": candidate_key,
                        "direction": direction_key,
                        "seed": seed,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:2000],
                    }
                )

    stop[0] = True
    monitor.join(timeout=1)
    peak_gib = peak[0] / (1024**3) if peak[0] else _peak_rss_gib()
    return {
        "candidate": candidate_key,
        "cold_load_seconds": load_seconds,
        "peak_process_rss_gib": peak_gib,
        "measurements": measurements,
        "skipped": skipped,
        "errors": errors,
    }


def _speaker_embedding(model: Any, path: str | Path) -> np.ndarray:
    import mlx.core as mx

    with temporary_mono_wav(path, sample_rate=24000) as prepared:
        audio, sample_rate = sf.read(
            prepared,
            dtype="float32",
            always_2d=False,
        )
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)
    embedding = np.asarray(
        model.extract_speaker_embedding(mx.array(audio), sr=sample_rate)
    ).reshape(-1)
    return embedding / (np.linalg.norm(embedding) + 1e-12)


def _run_speaker_worker(payload: dict[str, Any]) -> dict[str, Any]:
    status = payload["model_status"]
    if not status["cached"]:
        return {
            "available": False,
            "reason": "speaker_evaluation_model_missing",
            "measurements": {},
        }
    _disable_optional_sklearn()
    from mlx_audio.tts.utils import load_model
    from mlx_audio.utils import get_model_name_parts

    spec = model_spec("mlx_clone")
    model = load_model(
        Path(status["snapshot_path"]),
        model_name_parts=get_model_name_parts(spec.repo_id),
        strict=False,
    )
    reference = _speaker_embedding(model, payload["reference_audio"])
    measurements = {}
    for item in payload["outputs"]:
        embedding = _speaker_embedding(model, item["path"])
        measurements[item["sample_id"]] = {
            "speaker_cosine_to_primary_reference": float(
                np.dot(reference, embedding)
            )
        }
    return {
        "available": True,
        "model": spec.repo_id,
        "revision": spec.revision,
        "measurements": measurements,
    }


def _run_transcription_worker(payload: dict[str, Any]) -> dict[str, Any]:
    result = evaluate_transcriptions(payload)
    for measurement in result.get("measurements", {}).values():
        transcript = measurement.get("transcript")
        if transcript is not None:
            measurement["transcript_sha256"] = sha256_text(str(transcript))
    return result


def _require_complete_transcription_evaluation(
    result: dict[str, Any],
) -> None:
    if result.get("available") and result.get("complete"):
        return
    reason = result.get("reason") or "incomplete"
    failures = result.get("failure_count")
    detail = (
        f"; {failures} sample(s) failed"
        if isinstance(failures, int) and failures > 0
        else ""
    )
    raise RuntimeError(
        "Transcription evaluation was required but did not produce a "
        f"complete result ({reason}{detail})."
    )


def _invoke_worker(
    mode: str,
    payload: dict[str, Any],
    *,
    timeout: int,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="alexandria-expressive-clone-worker-"
    ) as temp:
        payload_path = Path(temp) / "payload.json"
        payload_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        command = [
            str(PYTHON),
            str(Path(__file__).resolve()),
            f"--worker-{mode}",
            "--worker-payload",
            str(payload_path),
        ]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(APP)
        environment["HF_HUB_OFFLINE"] = "1"
        environment["TRANSFORMERS_OFFLINE"] = "1"
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    combined = completed.stdout + "\n" + completed.stderr
    marker = next(
        (
            line
            for line in reversed(combined.splitlines())
            if line.startswith(WORKER_MARKER)
        ),
        None,
    )
    if marker is None:
        raise RuntimeError(
            f"{mode} worker returned no result.\n{combined[-6000:]}"
        )
    result = json.loads(marker[len(WORKER_MARKER) :])
    result["worker_exit_code"] = completed.returncode
    return result


def _probe_import(module_name: str) -> dict[str, Any]:
    command = [
        str(PYTHON),
        "-c",
        f"import {module_name}",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return {
        "module": module_name,
        "available": completed.returncode == 0,
        "exit_code": completed.returncode,
        "error": (
            completed.stderr.strip()[-2000:]
            if completed.returncode != 0
            else None
        ),
    }


def _probe_transcription_runtime() -> dict[str, Any]:
    marker = "TRANSCRIPTION_EVALUATOR_PROBE_JSON="
    command = [
        str(PYTHON),
        "-c",
        (
            "import json; "
            "from transcription_evaluator import runtime_probe; "
            f"print({marker!r} + json.dumps(runtime_probe(), sort_keys=True))"
        ),
    ]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join((str(APP), str(BENCHMARKS)))
    environment["HF_HUB_OFFLINE"] = "1"
    environment["TRANSFORMERS_OFFLINE"] = "1"
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    combined = completed.stdout + "\n" + completed.stderr
    line = next(
        (item for item in reversed(combined.splitlines()) if item.startswith(marker)),
        None,
    )
    if line is None:
        return {
            **evaluator_identity(),
            "available": False,
            "reason": "transcription_runtime_probe_failed",
            "exit_code": completed.returncode,
            "error": combined[-2000:].strip(),
        }
    result = json.loads(line[len(marker) :])
    result["exit_code"] = completed.returncode
    return result


def build_probe_result(
    *,
    cache_dir: str | Path | None = None,
) -> dict[str, Any]:
    whisper_spec = model_spec(EVALUATOR_MODEL_KEY)
    speaker_spec = model_spec("mlx_clone")
    return {
        "schema_version": SCHEMA_VERSION,
        "run_kind": "candidate_probe",
        "created_at_utc": utc_now(),
        "repository_head": repository_head(),
        "hardware": system_hardware(),
        "environment": {
            "python": sys.version,
            "mlx_audio_version": package_version("mlx-audio"),
            "mlx_whisper_version": package_version("mlx-whisper"),
        },
        "catalog": expressive_clone_candidate_catalog(cache_dir=cache_dir),
        "evaluators": {
            "speaker_similarity": {
                "model": speaker_spec.repo_id,
                "revision": speaker_spec.revision,
                "cache": cached_snapshot_status(
                    speaker_spec.repo_id,
                    revision=speaker_spec.revision,
                    cache_dir=cache_dir,
                    required_paths=speaker_spec.required_paths,
                ),
            },
            "transcription_accuracy": {
                **evaluator_identity(),
                "cache": cached_snapshot_status(
                    whisper_spec.repo_id,
                    revision=whisper_spec.revision,
                    cache_dir=cache_dir,
                    required_paths=whisper_spec.required_paths,
                ),
                "runtime_import": _probe_transcription_runtime(),
            },
        },
        "benchmark_contract": {
            "directions": [item["key"] for item in DEFAULT_DIRECTIONS],
            "seeds": list(DEFAULT_SEEDS),
            "same_primary_reference_required": True,
            "same_target_text_required": True,
            "reference_bank_allowed_only_for_reference_style_candidates": True,
            "implicit_downloads_allowed": False,
            "post_generation_prosody_allowed": False,
            "manual_blinded_listening_required": True,
            "production_promotion_allowed": False,
        },
    }


def _read_json(path: str | None, default: Any) -> Any:
    if not path:
        return default
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _read_text(value: str | None, path: str | None, label: str) -> str:
    if value and path:
        raise ValueError(f"Use either --{label} or --{label}-file, not both.")
    text = value
    if path:
        text = Path(path).read_text(encoding="utf-8")
    resolved = str(text or "").strip()
    if not resolved:
        raise ValueError(f"{label.replace('-', ' ')} is required.")
    return resolved


def _parse_seeds(value: str) -> list[int]:
    seeds = [int(item.strip()) for item in value.split(",") if item.strip()]
    if len(seeds) < 2:
        raise ValueError("At least two deterministic seeds are required.")
    if any(item < 0 for item in seeds):
        raise ValueError("Benchmark seeds must be non-negative integers.")
    return seeds


def _duration_variation(
    measurements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[float]] = {}
    for item in measurements:
        duration = item.get("audio", {}).get("duration_seconds")
        if not isinstance(duration, (int, float)):
            continue
        groups.setdefault(
            (item["candidate"], item["direction"]),
            [],
        ).append(float(duration))
    result = []
    for (candidate, direction), values in sorted(groups.items()):
        mean = float(np.mean(values))
        result.append(
            {
                "candidate": candidate,
                "direction": direction,
                "sample_count": len(values),
                "mean_duration_seconds": mean,
                "standard_deviation_seconds": float(np.std(values)),
                "coefficient_of_variation": (
                    float(np.std(values)) / mean if mean else None
                ),
            }
        )
    return result


def _write_review_manifests(
    output_dir: Path,
    measurements: list[dict[str, Any]],
    *,
    expected_text: str,
    transcription_measurements: dict[str, Any],
) -> dict[str, str]:
    blinded = []
    answer_key = []
    for item in sorted(measurements, key=lambda row: row["sample_id"]):
        automatic = transcription_measurements.get(item["sample_id"], {})
        automatic_transcript = automatic.get("transcript")
        if automatic_transcript is not None:
            automatic_status = "available"
        elif automatic.get("error_type"):
            automatic_status = "failed"
        else:
            automatic_status = "unavailable"
        blinded.append(
            {
                "sample_id": item["sample_id"],
                "file": item["output_file"],
                "requested_direction": item["direction"],
                "expected_text": expected_text,
                "automatic_transcription_status": automatic_status,
                "automatic_transcript": automatic_transcript,
                "automatic_word_error_rate": automatic.get("word_error_rate"),
                "spoken_text_matches_expected": None,
                "missing_changed_or_extra_words": "",
                "speaker_identity_1_to_5": None,
                "delivery_adherence_1_to_5": None,
                "naturalness_1_to_5": None,
                "artifact_severity_1_to_5": None,
                "approve_for_candidate_comparison": None,
                "notes": "",
            }
        )
        answer_key.append(
            {
                "sample_id": item["sample_id"],
                "candidate": item["candidate"],
                "direction": item["direction"],
                "seed": item["seed"],
            }
        )
    blinded_path = output_dir / "listening_review_blinded.json"
    key_path = output_dir / "listening_review_answer_key.json"
    blinded_path.write_text(
        json.dumps(blinded, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    key_path.write_text(
        json.dumps(answer_key, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "blinded_review": blinded_path.name,
        "answer_key": key_path.name,
    }


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    reference_audio = Path(args.reference_audio).expanduser().resolve()
    if not reference_audio.is_file():
        raise FileNotFoundError(reference_audio)
    reference_text = _read_text(
        args.reference_text,
        args.reference_text_file,
        "reference-text",
    )
    text = _read_text(args.text, args.text_file, "text")
    directions = _read_json(args.directions_file, list(DEFAULT_DIRECTIONS))
    direction_map = _direction_map(directions)
    if args.direction:
        requested_directions = list(dict.fromkeys(args.direction))
        unknown_directions = [
            key for key in requested_directions if key not in direction_map
        ]
        if unknown_directions:
            raise ValueError(
                "Unknown benchmark direction(s): "
                + ", ".join(unknown_directions)
                + "."
            )
        direction_map = {
            key: direction_map[key] for key in requested_directions
        }
    reference_map = _read_json(args.reference_map, {})
    for key, item in reference_map.items():
        if not isinstance(item, dict):
            raise ValueError(f"Reference-map entry {key!r} must be an object.")
        audio = Path(str(item.get("audio") or "")).expanduser().resolve()
        if not audio.is_file():
            raise FileNotFoundError(audio)
        item["audio"] = str(audio)
    seeds = _parse_seeds(args.seeds)
    selected = args.candidate or list(primary_candidate_keys())
    if args.include_comparison_baselines:
        selected.extend(comparison_candidate_keys())
    selected = list(dict.fromkeys(selected))
    for key in selected:
        expressive_clone_candidate(key)

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    candidate_results = []
    measurements = []
    unavailable = []
    for candidate_key in selected:
        status = expressive_clone_candidate_status(
            candidate_key,
            cache_dir=args.cache_dir,
        )
        if not status["ready_for_benchmark"]:
            unavailable.append(status)
            if args.require_all_candidates:
                raise RuntimeError(
                    f"{candidate_key} is not ready for benchmark: "
                    + "; ".join(
                        item["message"] for item in status["blockers"]
                    )
                )
            continue
        worker_result = _invoke_worker(
            "candidate",
            {
                "candidate_key": candidate_key,
                "candidate_status": status,
                "output_dir": str(output_dir),
                "reference_audio": str(reference_audio),
                "reference_text": reference_text,
                "text": text,
                "directions": list(direction_map.values()),
                "reference_map": reference_map,
                "seeds": seeds,
            },
            timeout=args.worker_timeout,
        )
        candidate_results.append(worker_result)
        measurements.extend(worker_result.get("measurements", []))

    output_records = [
        {
            "sample_id": item["sample_id"],
            "path": str(output_dir / item["output_file"]),
        }
        for item in measurements
    ]
    speaker_evaluation: dict[str, Any] = {
        "available": False,
        "reason": "disabled",
        "measurements": {},
    }
    if args.speaker_evaluation != "off" and output_records:
        spec = model_spec("mlx_clone")
        status = cached_snapshot_status(
            spec.repo_id,
            revision=spec.revision,
            cache_dir=args.cache_dir,
            required_paths=spec.required_paths,
        )
        speaker_evaluation = _invoke_worker(
            "speaker",
            {
                "model_status": status,
                "reference_audio": str(reference_audio),
                "outputs": output_records,
            },
            timeout=args.worker_timeout,
        )
        if (
            args.speaker_evaluation == "required"
            and not speaker_evaluation.get("available")
        ):
            raise RuntimeError("Speaker evaluation was required but unavailable.")

    transcription_evaluation: dict[str, Any] = {
        "available": False,
        "reason": "disabled",
        "measurements": {},
    }
    if args.transcription_evaluation != "off" and output_records:
        spec = model_spec(EVALUATOR_MODEL_KEY)
        status = cached_snapshot_status(
            spec.repo_id,
            revision=spec.revision,
            cache_dir=args.cache_dir,
            required_paths=spec.required_paths,
        )
        transcription_evaluation = _invoke_worker(
            "transcription",
            {
                "model_status": status,
                "text": text,
                "outputs": output_records,
            },
            timeout=args.worker_timeout,
        )
        if args.transcription_evaluation == "required":
            _require_complete_transcription_evaluation(
                transcription_evaluation
            )

    for item in measurements:
        sample_id = item["sample_id"]
        speaker = speaker_evaluation.get("measurements", {}).get(sample_id)
        transcript = transcription_evaluation.get("measurements", {}).get(
            sample_id
        )
        if speaker is not None:
            item["speaker_evaluation"] = speaker
        if transcript is not None:
            item["transcription_evaluation"] = {
                key: value
                for key, value in transcript.items()
                if key != "transcript"
            }

    review_files = _write_review_manifests(
        output_dir,
        measurements,
        expected_text=text,
        transcription_measurements=transcription_evaluation.get(
            "measurements",
            {},
        ),
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "run_kind": "expressive_clone_matrix",
        "created_at_utc": utc_now(),
        "repository_head": repository_head(),
        "hardware": system_hardware(),
        "environment": {
            "python": sys.version,
            "mlx_audio_version": package_version("mlx-audio"),
            "mlx_whisper_version": package_version("mlx-whisper"),
            "hf_hub_offline_workers": True,
        },
        "corpus": {
            "primary_reference_audio_sha256": sha256_file(reference_audio),
            "primary_reference_text_sha256": sha256_text(reference_text),
            "target_text_sha256": sha256_text(text),
            "target_word_count": len(text.split()),
            "directions": [
                {
                    "key": item["key"],
                    "instruction_sha256": sha256_text(item["instruction"]),
                }
                for item in direction_map.values()
            ],
            "seeds": seeds,
        },
        "selected_candidates": selected,
        "unavailable_candidates": unavailable,
        "candidate_results": candidate_results,
        "measurements": measurements,
        "duration_variation": _duration_variation(measurements),
        "speaker_evaluation": {
            key: value
            for key, value in speaker_evaluation.items()
            if key != "measurements"
        },
        "transcription_evaluation": {
            key: value
            for key, value in transcription_evaluation.items()
            if key != "measurements"
        },
        "listening_review": {
            "status": "pending",
            "manual_blinded_review_required": True,
            **review_files,
        },
        "acceptance": {
            "production_promotion_allowed": False,
            "promotion_requires_complete_same_corpus_matrix": True,
            "promotion_requires_speaker_similarity": True,
            "promotion_requires_transcription_accuracy": True,
            "promotion_requires_blinded_delivery_review": True,
            "qwen_patch_remains_comparison_only": True,
        },
    }
    result_path = output_dir / "result.json"
    result_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run Alexandria's local-only supplied-voice expressive-clone matrix."
        )
    )
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--output-dir")
    parser.add_argument("--cache-dir")
    parser.add_argument(
        "--candidate",
        action="append",
        choices=[
            *primary_candidate_keys(),
            *comparison_candidate_keys(),
        ],
    )
    parser.add_argument(
        "--include-comparison-baselines",
        action="store_true",
    )
    parser.add_argument("--require-all-candidates", action="store_true")
    parser.add_argument("--reference-audio")
    parser.add_argument("--reference-text")
    parser.add_argument("--reference-text-file")
    parser.add_argument("--text")
    parser.add_argument("--text-file")
    parser.add_argument("--reference-map")
    parser.add_argument("--directions-file")
    parser.add_argument(
        "--direction",
        action="append",
        choices=[item["key"] for item in DEFAULT_DIRECTIONS],
        help=(
            "Limit the run to one or more directions from the active direction "
            "set. Repeat the option to select multiple directions."
        ),
    )
    parser.add_argument(
        "--seeds",
        default=",".join(str(item) for item in DEFAULT_SEEDS),
    )
    parser.add_argument(
        "--speaker-evaluation",
        choices=("auto", "off", "required"),
        default="auto",
    )
    parser.add_argument(
        "--transcription-evaluation",
        choices=("auto", "off", "required"),
        default="auto",
    )
    parser.add_argument("--worker-timeout", type=int, default=1800)
    parser.add_argument("--worker-candidate", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--worker-speaker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--worker-transcription",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--worker-payload", help=argparse.SUPPRESS)
    return parser.parse_args()


def _emit(value: dict[str, Any], output: str | None = None) -> None:
    rendered = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    if output:
        target = Path(output).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


def main() -> None:
    args = parse_args()
    if args.worker_candidate or args.worker_speaker or args.worker_transcription:
        if not args.worker_payload:
            raise ValueError("Worker payload is required.")
        payload = json.loads(
            Path(args.worker_payload).read_text(encoding="utf-8")
        )
        if args.worker_candidate:
            result = _run_candidate_worker(payload)
        elif args.worker_speaker:
            result = _run_speaker_worker(payload)
        else:
            result = _run_transcription_worker(payload)
        print(WORKER_MARKER + json.dumps(result, ensure_ascii=False))
        return

    if args.probe:
        _emit(build_probe_result(cache_dir=args.cache_dir), args.output)
        return

    if not args.output_dir:
        args.output_dir = str(
            ROOT
            / "benchmarks"
            / "results"
            / f"{timestamp_slug()}_expressive_clone_matrix"
        )
    result = run_benchmark(args)
    if args.output:
        _emit(result, args.output)
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
