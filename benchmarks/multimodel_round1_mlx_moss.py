"""MOSS-TTS reference-code caching and generation adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from multimodel_round1_mlx_support import (
    MOSS_TOKENIZER_REVISION,
    PreparedReferenceError,
    collect_results,
    prepared_reference_wav,
    read_audio,
    sha256_text,
)
from multimodel_round1_mlx_dependencies import ArtifactPathError
from multimodel_round1_mlx_paths import (
    contained_artifact_path,
    safe_hash_file,
    safe_stat_file,
)
from multimodel_round1_paths import PathSafetyError, safe_file_stat, safe_read_text


def moss_reference_cache_paths(
    evidence_root: Path, reference_sha256: str, num_quantizers: int
) -> tuple[Path, Path]:
    identity = sha256_text(
        f"{reference_sha256}\0{MOSS_TOKENIZER_REVISION}\0{num_quantizers}"
    )[:24]
    root = evidence_root / "moss-reference-codes"
    return root / f"{identity}.npz", root / f"{identity}.json"


def load_moss_reference_codes(
    cache_path: Path,
    metadata_path: Path,
    *,
    reference_sha256: str,
    num_quantizers: int,
    evidence_root: Path | None = None,
) -> Any | None:
    root = evidence_root or cache_path.parent
    if not safe_stat_file(root, cache_path, kind="metadata", allow_missing=True):
        return None
    if not safe_stat_file(root, metadata_path, kind="metadata", allow_missing=True):
        return None
    try:
        metadata_target = contained_artifact_path(root, metadata_path, kind="metadata")
        cache_target = contained_artifact_path(root, cache_path, kind="metadata")
        metadata = json.loads(safe_read_text(metadata_target))
        valid = (
            metadata.get("reference_audio_sha256") == reference_sha256
            and metadata.get("tokenizer_revision") == MOSS_TOKENIZER_REVISION
            and int(metadata.get("num_quantizers") or -1) == num_quantizers
            and metadata.get("cache_file_sha256") == safe_hash_file(root, cache_path)
        )
        if not valid:
            return None
        from multimodel_round1_mlx_support import _require_mlx

        safe_file_stat(cache_target)
        codes = _require_mlx().load(str(cache_target.literal))["codes"]
        _require_mlx().eval(codes)
        return codes
    except (ArtifactPathError, PathSafetyError):
        raise
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None


def moss_reference_codes(
    model: Any,
    sample: dict[str, Any],
    reference_path: Path,
    tokenizer_snapshot: Path,
    evidence_root: Path,
    memory_cache: dict[str, Any],
) -> tuple[Any, str]:
    reference_sha = str(sample["reference"]["conditioning_sha256"])
    num_quantizers = int(model.config.n_vq)
    memory_key = f"{reference_sha}:{num_quantizers}"
    if memory_key in memory_cache:
        return memory_cache[memory_key], "memory"
    cache_path, metadata_path = moss_reference_cache_paths(
        evidence_root, reference_sha, num_quantizers
    )
    codes = load_moss_reference_codes(
        cache_path,
        metadata_path,
        reference_sha256=reference_sha,
        num_quantizers=num_quantizers,
        evidence_root=evidence_root,
    )
    if codes is not None:
        memory_cache[memory_key] = codes
        return codes, "disk"
    normalized = prepared_reference_wav(
        evidence_root, reference_path, sample_rate=48000
    )
    from multimodel_round1_mlx_support import _require_mlx

    reference_audio, rate = read_audio(
        normalized,
        root=evidence_root,
        always_2d=False,
    )
    if reference_audio.ndim > 1:
        reference_audio = reference_audio.mean(axis=1)
    if int(rate) != 48000:
        raise PreparedReferenceError(
            normalized,
            48000,
            "prepared MOSS reference has the wrong sample rate",
        )
    codes = model.encode_reference_audio(
        _require_mlx().array(reference_audio),
        sample_rate=48000,
        num_quantizers=num_quantizers,
        source=str(tokenizer_snapshot),
    )
    _require_mlx().eval(codes)
    memory_cache[memory_key] = codes
    return codes, "encoded"


def generate_moss(
    model: Any,
    sample: dict[str, Any],
    reference_path: Path,
    reference_text: str,
    tokenizer_snapshot: Path,
    evidence_root: Path | None = None,
    reference_code_cache: dict[str, Any] | None = None,
) -> tuple[np.ndarray, int] | tuple[np.ndarray, int, str]:
    control = sample["control"]
    reference_kwargs: dict[str, Any] = {"ref_audio": str(reference_path)}
    cache_status = "disabled"
    if evidence_root is not None and reference_code_cache is not None:
        prompt_audio_codes, cache_status = moss_reference_codes(
            model,
            sample,
            reference_path,
            tokenizer_snapshot,
            evidence_root,
            reference_code_cache,
        )
        reference_kwargs = {"prompt_audio_codes": prompt_audio_codes}
    results = model.generate(
        text=sample["target_text"],
        **reference_kwargs,
        ref_text=reference_text,
        mode="generation",
        instruction=control["instruction"],
        language=control["language"],
        max_tokens=int(control["max_tokens"]),
        audio_temperature=float(control["audio_temperature"]),
        audio_top_p=float(control["audio_top_p"]),
        audio_top_k=int(control["audio_top_k"]),
        n_vq_for_inference=int(control["n_vq_for_inference"]),
        audio_tokenizer_source=str(tokenizer_snapshot),
        stream=False,
    )
    audio, sample_rate = collect_results(model, results)
    if reference_code_cache is None:
        return audio, sample_rate
    return audio, sample_rate, cache_status
