"""Chatterbox generation identity and fail-closed condition-cache policy."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol, TypedDict

import numpy as np
from numpy.typing import NDArray

from multimodel_round1_runtime import (
    GenerationIntegrityError,
    canonical_json,
    sha256_text,
    validate_generation_pair,
)


@dataclass(frozen=True, slots=True)
class ChatterboxError(RuntimeError):
    code: str
    subject: str

    def __str__(self) -> str:
        return f"{self.code}: {self.subject}"


class GeneratedAudio(Protocol):
    def detach(self) -> GeneratedAudio: ...

    def cpu(self) -> GeneratedAudio: ...

    def numpy(self) -> NDArray[np.float32]: ...


class ChatterboxModel(Protocol):
    sr: int

    def prepare_conditionals(self, reference: str, *, exaggeration: float) -> None: ...

    def generate(
        self, text: str, **controls: str | float | None
    ) -> GeneratedAudio: ...


class MpsRuntime(Protocol):
    def synchronize(self) -> None: ...

    def empty_cache(self) -> None: ...


class TorchRuntime(Protocol):
    mps: MpsRuntime

    def manual_seed(self, seed: int) -> None: ...


class DeviceHandle(Protocol):
    def __str__(self) -> str: ...


class MetalLease(Protocol):
    def close(self) -> None: ...


class AudioMetrics(TypedDict):
    duration_seconds: float
    sample_rate: int
    channels: int
    rms_dbfs: float
    peak_dbfs: float
    words_per_second: float | None


def repository_head(source: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    value = completed.stdout.strip()
    if completed.returncode != 0 or not value:
        raise ChatterboxError("source_commit_unavailable", str(source))
    return value


MODEL_REPO: Final = "ResembleAI/chatterbox"
MODEL_REVISION: Final = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"
SOURCE_COMMIT: Final = "5de7a54aa4e5e2baadb0182dde554908b48b85c2"
T3_MODEL: Final = "v3"
ROUND_ID: Final = "alexandria_multimodel_expressive_clone_round1_v1"
FULL_CONDITIONALS_REUSE_POLICY: Final = (
    "disabled_pending_real_model_equivalence_revalidation"
)
REVALIDATION_REQUIRED: Final = "requires_revalidation"
REVALIDATION_NOT_FLAGGED: Final = "not_flagged"
RUNTIME_CONTROLS: Final = {
    "device": "mps",
    "cpu_staged_checkpoint_load": True,
    "watermark_applied": False,
    "watermark_reason": "perth_backend_unavailable_on_macos",
    "temperature": 0.8,
    "repetition_penalty": 1.2,
    "min_p": 0.05,
    "top_p": 1.0,
}


class ConditionalsCacheProbe:
    """Tracks repeated reference keys without retaining model conditionals."""

    def __init__(self) -> None:
        self._seen_reference_keys: set[str] = set()

    def observe(self, reference_key: str) -> bool:
        key_seen_before = reference_key in self._seen_reference_keys
        self._seen_reference_keys.add(reference_key)
        return key_seen_before


def legacy_cache_revalidation_status(conditionals_cache_hit: bool | None) -> str:
    if conditionals_cache_hit is True:
        return REVALIDATION_REQUIRED
    return REVALIDATION_NOT_FLAGGED


def chatterbox_sample_fingerprint(sample: dict[str, Any]) -> str:
    value = {
        "round_id": ROUND_ID,
        "sample_id": sample["sample_id"],
        "model_repo": MODEL_REPO,
        "model_revision": MODEL_REVISION,
        "source_commit": SOURCE_COMMIT,
        "t3_model": T3_MODEL,
        "identity_key": sample["identity_key"],
        "style": sample["style"],
        "target_text_sha256": sample["target_text_sha256"],
        "reference_audio_sha256": sample["reference"].get("conditioning_sha256"),
        "control": sample["control"],
        "seed": sample["seed"],
        "runtime": RUNTIME_CONTROLS,
    }
    return sha256_text(canonical_json(value))


def partition_chatterbox_samples(
    evidence_root: Path,
    samples: list[dict[str, Any]],
    *,
    reuse_existing: bool,
    max_samples: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    model_contract = {"key": "chatterbox_multilingual_v3"}
    pending: list[dict[str, Any]] = []
    reused: list[dict[str, Any]] = []
    for sample in samples:
        if not reuse_existing:
            pending.append(sample)
            continue
        try:
            receipt, _ = validate_generation_pair(
                evidence_root,
                sample,
                model_contract,
                expected_fingerprint=chatterbox_sample_fingerprint(sample),
            )
        except GenerationIntegrityError:
            pending.append(sample)
        else:
            reused.append(receipt)
    if max_samples is not None:
        if max_samples < 0:
            raise ChatterboxError("max_samples_negative", str(max_samples))
        pending = pending[:max_samples]
    return pending, reused
