"""Text, audio, and speaker metrics used by the Round 1 evaluator."""

from __future__ import annotations

import importlib
import importlib.metadata as metadata
import io
import json
import re
import sys
import types
import unicodedata
from pathlib import Path
from typing import Any

import mlx.core as mx
import numpy as np
import soundfile as sf

from multimodel_round1_paths import (
    ArtifactPaths,
    ContainedPath,
    contained_path,
    contained_path_from_full,
    parse_artifact_paths,
    safe_file_stat,
    safe_read_bytes,
    safe_read_text,
)
from run_multimodel_round1_mlx import prepared_reference_wav


WHISPER_REPO = "mlx-community/whisper-base-mlx"
WHISPER_REVISION = "1e3e249fb8d01c655324bd6841b1deadffd6d04c"
WHISPER_VERSION = "0.4.3"
_WORD_PATTERN = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)


class ObjectiveInputError(RuntimeError):
    """Raised when objective-evaluation inputs violate the manifest contract."""


class ObjectiveDependencyError(RuntimeError):
    """Raised when the pinned evaluator runtime is unavailable."""


class ObjectiveIntegrityError(RuntimeError):
    """Raised when generated evidence fails an integrity check."""


def normalized_words(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return [word.replace("’", "'") for word in _WORD_PATTERN.findall(normalized)]


def word_error_rate(reference: str, hypothesis: str) -> float:
    left = normalized_words(reference)
    right = normalized_words(hypothesis)
    if not left:
        return 0.0 if not right else 1.0
    previous = list(range(len(right) + 1))
    for index, left_word in enumerate(left, start=1):
        current = [index]
        for column, right_word in enumerate(right, start=1):
            current.append(
                min(
                    previous[column - 1] + (left_word != right_word),
                    previous[column] + 1,
                    current[column - 1] + 1,
                )
            )
        previous = current
    return previous[-1] / len(left)


def _median_filter(volume: Any, kernel_size: Any = None) -> np.ndarray:
    array = np.asarray(volume)
    if kernel_size is None:
        sizes = (3,) * array.ndim
    elif isinstance(kernel_size, int):
        sizes = (kernel_size,) * array.ndim
    else:
        sizes = tuple(int(value) for value in kernel_size)
    if any(value <= 0 or value % 2 == 0 for value in sizes):
        raise ObjectiveInputError("Median filter sizes must be positive odd integers.")
    padded = np.pad(
        array,
        tuple((value // 2, value // 2) for value in sizes),
        mode="constant",
    )
    windows = np.lib.stride_tricks.sliding_window_view(padded, sizes)
    axes = tuple(range(array.ndim, windows.ndim))
    return np.median(windows, axis=axes).astype(array.dtype, copy=False)


def load_whisper() -> Any:
    if metadata.version("mlx-whisper") != WHISPER_VERSION:
        raise ObjectiveDependencyError(f"mlx-whisper=={WHISPER_VERSION} is required.")
    prior = {
        name: module
        for name, module in list(sys.modules.items())
        if name == "scipy" or name.startswith("scipy.")
    }
    for name in prior:
        sys.modules.pop(name, None)
    scipy = types.ModuleType("scipy")
    signal = types.ModuleType("scipy.signal")
    signal.medfilt = _median_filter
    scipy.signal = signal
    scipy.__version__ = "1.15.3"
    sys.modules["scipy"] = scipy
    sys.modules["scipy.signal"] = signal
    try:
        return importlib.import_module("mlx_whisper")
    finally:
        for name in list(sys.modules):
            if name == "scipy" or name.startswith("scipy."):
                sys.modules.pop(name, None)
        sys.modules.update(prior)


def reference_path(evidence_root: Path, sample: dict[str, Any]) -> Path:
    value = sample["reference"].get("conditioning_file")
    if not value:
        raise ObjectiveInputError(
            f"Reference file is missing for {sample['sample_id']}"
        )
    target = contained_path(evidence_root / "references", str(value))
    safe_file_stat(target)
    return target.literal


def generation_artifacts(
    evidence_root: Path, sample: dict[str, Any]
) -> ArtifactPaths:
    artifacts = parse_artifact_paths(
        evidence_root,
        str(sample["output_file"]),
        str(sample["result_file"]),
    )
    safe_file_stat(artifacts.output)
    safe_file_stat(artifacts.result)
    return artifacts


def select_generated_samples(
    evidence_root: Path,
    samples: list[dict[str, Any]],
    group: str,
    models: set[str] | None,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for sample in samples:
        if sample["group"] != group or (models and sample["model_key"] not in models):
            continue
        try:
            generation_artifacts(evidence_root, sample)
        except OSError:
            continue
        selected.append(sample)
    return selected


def read_existing_measurements(
    target: ContainedPath, *, force: bool
) -> dict[str, Any]:
    if force:
        return {}
    try:
        payload = json.loads(safe_read_text(target))
    except FileNotFoundError:
        return {}
    return payload.get("measurements") or {}


def _read_audio(
    evidence_root: Path, path: Path, *, always_2d: bool
) -> tuple[np.ndarray, int]:
    target = contained_path_from_full(evidence_root, path)
    return sf.read(
        io.BytesIO(safe_read_bytes(target)),
        dtype="float32",
        always_2d=always_2d,
    )


def speaker_embedding(model: Any, evidence_root: Path, path: Path) -> np.ndarray:
    prepared = prepared_reference_wav(evidence_root, path, sample_rate=24000)
    audio, sample_rate = _read_audio(evidence_root, prepared, always_2d=False)
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)
    embedding = np.asarray(
        model.extract_speaker_embedding(mx.array(audio), sr=int(sample_rate))
    ).reshape(-1)
    return embedding / (np.linalg.norm(embedding) + 1e-12)


def audio_diagnostics(evidence_root: Path, path: Path) -> dict[str, Any]:
    audio, sample_rate = _read_audio(evidence_root, path, always_2d=True)
    mono = audio.mean(axis=1)
    onset_count = min(len(mono), max(1, int(sample_rate * 0.10)))
    tail_count = min(len(mono), max(1, int(sample_rate * 0.10)))
    onset = mono[:onset_count]
    tail = mono[-tail_count:]
    non_silent = np.flatnonzero(np.abs(mono) > 0.002)
    leading_silence = float(non_silent[0]) / sample_rate if len(non_silent) else len(mono) / sample_rate
    trailing_silence = float(len(mono) - 1 - non_silent[-1]) / sample_rate if len(non_silent) else len(mono) / sample_rate
    return {
        "duration_seconds": len(mono) / int(sample_rate),
        "sample_rate": int(sample_rate),
        "onset_peak": float(np.max(np.abs(onset))) if len(onset) else 0.0,
        "onset_rms": float(np.sqrt(np.mean(onset * onset))) if len(onset) else 0.0,
        "tail_peak": float(np.max(np.abs(tail))) if len(tail) else 0.0,
        "leading_silence_seconds": leading_silence,
        "trailing_silence_seconds": trailing_silence,
        "empty_or_nearly_silent": bool(not len(mono) or float(np.max(np.abs(mono))) < 0.005),
    }
