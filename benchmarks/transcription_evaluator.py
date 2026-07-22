from __future__ import annotations

import importlib
import importlib.metadata as metadata
import re
import sys
import types
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from model_registry import model_spec  # noqa: E402


EVALUATOR_MODEL_KEY = "mlx_whisper_base"
EVALUATOR_RUNTIME_PACKAGE = "mlx-whisper"
EVALUATOR_RUNTIME_VERSION = "0.4.3"
EVALUATOR_DEPENDENCY_PATH = "alexandria_scipy_free_signal_shim_v1"
EVALUATOR_LANGUAGE = "en"
_WORD_PATTERN = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)


class TranscriptionEvaluatorError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def evaluator_identity() -> dict[str, Any]:
    spec = model_spec(EVALUATOR_MODEL_KEY)
    return {
        "model_key": spec.key,
        "model": spec.repo_id,
        "revision": spec.revision,
        "runtime": spec.runtime,
        "runtime_package": EVALUATOR_RUNTIME_PACKAGE,
        "runtime_version": EVALUATOR_RUNTIME_VERSION,
        "dependency_path": EVALUATOR_DEPENDENCY_PATH,
        "language": EVALUATOR_LANGUAGE,
        "local_files_only": True,
    }


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
            substitution = previous[column - 1] + (left_word != right_word)
            deletion = previous[column] + 1
            insertion = current[column - 1] + 1
            current.append(min(substitution, deletion, insertion))
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
    if len(sizes) != array.ndim:
        raise ValueError("kernel_size must have one entry per input dimension.")
    if any(value <= 0 or value % 2 == 0 for value in sizes):
        raise ValueError("Each kernel size must be a positive odd integer.")
    padding = tuple((value // 2, value // 2) for value in sizes)
    padded = np.pad(array, padding, mode="constant")
    windows = np.lib.stride_tricks.sliding_window_view(padded, sizes)
    axes = tuple(range(array.ndim, windows.ndim))
    return np.median(windows, axis=axes).astype(array.dtype, copy=False)


def _scipy_signal_shim() -> tuple[types.ModuleType, types.ModuleType]:
    scipy = types.ModuleType("scipy")
    signal = types.ModuleType("scipy.signal")
    signal.medfilt = _median_filter
    scipy.signal = signal
    # Numba performs a numeric major/minor version check while importing.
    scipy.__version__ = "1.15.3"
    scipy.__alexandria_dependency_path__ = EVALUATOR_DEPENDENCY_PATH
    return scipy, signal


@contextmanager
def _isolated_scipy_signal() -> Iterator[None]:
    prior = {
        name: module
        for name, module in list(sys.modules.items())
        if name == "scipy" or name.startswith("scipy.")
    }
    for name in prior:
        sys.modules.pop(name, None)
    scipy, signal = _scipy_signal_shim()
    sys.modules["scipy"] = scipy
    sys.modules["scipy.signal"] = signal
    try:
        yield
    finally:
        for name in list(sys.modules):
            if name == "scipy" or name.startswith("scipy."):
                sys.modules.pop(name, None)
        sys.modules.update(prior)


def _clear_partial_mlx_whisper_import() -> None:
    for name in list(sys.modules):
        if name == "mlx_whisper" or name.startswith("mlx_whisper."):
            sys.modules.pop(name, None)


def load_pinned_runtime() -> Any:
    try:
        runtime_version = metadata.version(EVALUATOR_RUNTIME_PACKAGE)
    except metadata.PackageNotFoundError as exc:
        raise TranscriptionEvaluatorError(
            "transcription_runtime_missing",
            (
                f"{EVALUATOR_RUNTIME_PACKAGE}=={EVALUATOR_RUNTIME_VERSION} is "
                "required for transcription evaluation."
            ),
        ) from exc
    if runtime_version != EVALUATOR_RUNTIME_VERSION:
        raise TranscriptionEvaluatorError(
            "transcription_runtime_version_mismatch",
            (
                f"Transcription evaluation requires {EVALUATOR_RUNTIME_PACKAGE}=="
                f"{EVALUATOR_RUNTIME_VERSION}; found {runtime_version}."
            ),
        )
    existing = sys.modules.get("mlx_whisper")
    if existing is not None:
        return existing
    try:
        with _isolated_scipy_signal():
            return importlib.import_module("mlx_whisper")
    except Exception as exc:
        _clear_partial_mlx_whisper_import()
        raise TranscriptionEvaluatorError(
            "transcription_runtime_import_failed",
            (
                "The pinned MLX Whisper evaluator could not load through "
                f"{EVALUATOR_DEPENDENCY_PATH}: {type(exc).__name__}: {exc}"
            ),
        ) from exc


def runtime_probe() -> dict[str, Any]:
    identity = evaluator_identity()
    try:
        runtime = load_pinned_runtime()
    except TranscriptionEvaluatorError as exc:
        return {
            **identity,
            "available": False,
            "reason": exc.code,
            "error": str(exc),
        }
    return {
        **identity,
        "available": True,
        "reported_runtime_version": getattr(runtime, "__version__", None),
    }


def evaluate_transcriptions(payload: dict[str, Any]) -> dict[str, Any]:
    identity = evaluator_identity()
    status = payload.get("model_status") or {}
    spec = model_spec(EVALUATOR_MODEL_KEY)
    if status.get("revision") != spec.revision:
        return {
            **identity,
            "available": False,
            "complete": False,
            "reason": "transcription_model_revision_mismatch",
            "measurements": {},
        }
    if not status.get("cached"):
        return {
            **identity,
            "available": False,
            "complete": False,
            "reason": "transcription_model_missing",
            "measurements": {},
        }
    snapshot = Path(str(status.get("snapshot_path") or "")).expanduser()
    if not snapshot.is_dir():
        return {
            **identity,
            "available": False,
            "complete": False,
            "reason": "transcription_model_snapshot_invalid",
            "measurements": {},
        }
    try:
        runtime = load_pinned_runtime()
    except TranscriptionEvaluatorError as exc:
        return {
            **identity,
            "available": False,
            "complete": False,
            "reason": exc.code,
            "error": str(exc),
            "measurements": {},
        }

    default_expected_text = str(payload.get("text") or "")
    outputs = list(payload.get("outputs") or [])
    measurements: dict[str, Any] = {}
    success_count = 0
    for item in outputs:
        sample_id = str(item.get("sample_id") or "").strip()
        audio_path = Path(str(item.get("path") or "")).expanduser()
        expected_text = str(item.get("text") or default_expected_text).strip()
        if not sample_id:
            continue
        try:
            if not expected_text:
                raise ValueError(f"Expected text is missing for sample {sample_id!r}.")
            if not audio_path.is_file():
                raise FileNotFoundError(audio_path)
            result = runtime.transcribe(
                str(audio_path),
                path_or_hf_repo=str(snapshot),
                language=EVALUATOR_LANGUAGE,
                word_timestamps=False,
                condition_on_previous_text=False,
                verbose=False,
            )
            transcript = str(result.get("text") or "").strip()
            measurements[sample_id] = {
                "word_error_rate": word_error_rate(expected_text, transcript),
                "transcript": transcript,
            }
            success_count += 1
        except Exception as exc:
            measurements[sample_id] = {
                "error_type": type(exc).__name__,
                "error": str(exc)[:2000],
            }

    expected_count = len(outputs)
    failure_count = expected_count - success_count
    return {
        **identity,
        "available": True,
        "complete": expected_count > 0 and failure_count == 0,
        "expected_count": expected_count,
        "success_count": success_count,
        "failure_count": failure_count,
        "measurements": measurements,
    }
