#!/usr/bin/env python3
"""Compatibility facade for the resumable Round 1 MLX runner."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from multimodel_round1_mlx_cli import (
    SUPPORTED_MODELS as _SUPPORTED_MODELS,
    main as _main,
)
from multimodel_round1_mlx_generation import generate_pending_samples
from multimodel_round1_mlx_loading import SupportedModel, load_requested_models
from multimodel_round1_mlx_models import (
    generate_fish,
    generate_qwen,
    generate_voxcpm,
)
from multimodel_round1_mlx_moss import (
    generate_moss,
    load_moss_reference_codes,
    moss_reference_cache_paths,
    moss_reference_codes,
)
from multimodel_round1_mlx_paths import (
    artifact_paths_for_sample,
    safe_read_json,
    safe_write_json,
)
from multimodel_round1_mlx_support import (
    MOSS_TOKENIZER_REVISION,
    ArtifactPathError,
    GenerationInputError,
    InvalidAudioError,
    MlxDependencyError,
    MlxRunnerError,
    ModelSnapshotError,
    ManifestPathError,
    NoAudioGeneratedError,
    PreparedReferenceError,
    ReferencePathError,
    audio_metrics,
    collect_results,
    disable_optional_sklearn,
    exact_snapshot,
    load_model,
    mx,
    np,
    peak_rss_gib,
    prepared_reference_wav,
    read_audio,
    release_sample_mlx_cache as _release_sample_mlx_cache,
    resolve_reference,
    sf,
    sha256_file,
    sha256_text,
    write_audio_wav,
)
from multimodel_round1_runtime import (
    DiskHeadroomError,
    MetalLockBusyError,
    PROJECTED_SAMPLE_BYTES,
    ReferenceIntegrityError,
    acquire_metal_lock,
    atomic_write_json,
    canonical_json,
    disk_headroom_status,
    metal_generation_lock,
    partition_generation_samples,
    require_disk_headroom,
    sample_fingerprint,
    validate_sample_references,
    wav_is_decodable,
)


DEFAULT_EVIDENCE = ROOT / ".omo" / "evidence" / "b17-t05-multimodel-round1"
SUPPORTED_MODELS = set(_SUPPORTED_MODELS)


def release_sample_mlx_cache() -> None:
    """Release MLX allocations through the legacy monkeypatchable surface."""

    _release_sample_mlx_cache(mx)


def main() -> int:
    return _main(DEFAULT_EVIDENCE)


__all__ = [
    "APP",
    "ArtifactPathError",
    "DEFAULT_EVIDENCE",
    "DiskHeadroomError",
    "GenerationInputError",
    "InvalidAudioError",
    "MOSS_TOKENIZER_REVISION",
    "MetalLockBusyError",
    "MlxDependencyError",
    "MlxRunnerError",
    "ManifestPathError",
    "ModelSnapshotError",
    "NoAudioGeneratedError",
    "PROJECTED_SAMPLE_BYTES",
    "PreparedReferenceError",
    "ReferenceIntegrityError",
    "ReferencePathError",
    "SUPPORTED_MODELS",
    "SupportedModel",
    "acquire_metal_lock",
    "atomic_write_json",
    "audio_metrics",
    "artifact_paths_for_sample",
    "collect_results",
    "canonical_json",
    "disable_optional_sklearn",
    "disk_headroom_status",
    "exact_snapshot",
    "generate_fish",
    "generate_moss",
    "generate_pending_samples",
    "generate_qwen",
    "generate_voxcpm",
    "load_model",
    "load_requested_models",
    "load_moss_reference_codes",
    "main",
    "metal_generation_lock",
    "moss_reference_cache_paths",
    "moss_reference_codes",
    "mx",
    "np",
    "partition_generation_samples",
    "peak_rss_gib",
    "prepared_reference_wav",
    "read_audio",
    "release_sample_mlx_cache",
    "require_disk_headroom",
    "resolve_reference",
    "sample_fingerprint",
    "safe_read_json",
    "safe_write_json",
    "sf",
    "sha256_file",
    "sha256_text",
    "validate_sample_references",
    "wav_is_decodable",
    "write_audio_wav",
]


if __name__ == "__main__":
    raise SystemExit(main())
