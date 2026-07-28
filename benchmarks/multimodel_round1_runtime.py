"""Shared integrity and process-safety primitives for multimodel Round 1."""

from __future__ import annotations

import hashlib
import io
import json
import os
import wave
from pathlib import Path
from typing import Any

from multimodel_round1_paths import (
    ContainedPath,
    PathSafetyError,
    contained_path,
    contained_path_from_full,
    parse_artifact_paths,
    safe_atomic_write_text,
    safe_file_stat,
    safe_read_bytes,
    safe_read_text,
    safe_sha256_file,
)
from multimodel_round1_safety import (
    DEFAULT_SAFETY_MARGIN_BYTES,
    PROJECTED_SAMPLE_BYTES,
    STRICT_FREE_FLOOR_BYTES,
    DiskHeadroomError,
    MetalLease,
    MetalLockBusyError,
    acquire_metal_lock,
    disk_headroom_status,
    metal_generation_lock,
    require_disk_headroom,
)


class ReferenceIntegrityError(RuntimeError):
    pass


class GenerationIntegrityError(RuntimeError):
    def __init__(self, code: str, subject: str):
        self.code = code
        self.subject = subject
        super().__init__(f"{code}: {subject}")


class GenerationArgumentError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _existing_containment_root(path: Path) -> Path:
    candidate = Path(os.path.abspath(path.expanduser())).parent
    while not candidate.exists():
        if candidate == candidate.parent:
            raise PathSafetyError(str(path), "no existing containment root")
        candidate = candidate.parent
    return candidate


def _contained_full(path: Path, root: Path | None = None) -> ContainedPath:
    containment_root = root or _existing_containment_root(path)
    return contained_path_from_full(containment_root, path)


def sha256_file(path: Path, *, root: Path | None = None) -> str:
    return safe_sha256_file(_contained_full(path, root))


def sample_fingerprint(sample: dict[str, Any], model_contract: dict[str, Any]) -> str:
    relevant = {
        "round": "alexandria_multimodel_expressive_clone_round1_v1",
        "sample_id": sample["sample_id"],
        "model": model_contract,
        "identity_key": sample["identity_key"],
        "style": sample["style"],
        "target_text_sha256": sample["target_text_sha256"],
        "reference": {
            key: sample["reference"].get(key)
            for key in (
                "conditioning_sha256",
                "conditioning_transcript_sha256",
                "acted_emotion_reference_sha256",
            )
        },
        "control": sample["control"],
        "seed": sample["seed"],
    }
    return sha256_text(canonical_json(relevant))


def atomic_write_text(path: Path, value: str, *, root: Path | None = None) -> None:
    safe_atomic_write_text(_contained_full(path, root), value)


def atomic_write_json(path: Path, value: Any, *, root: Path | None = None) -> None:
    atomic_write_text(
        path,
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        root=root,
    )


def wav_is_decodable(path: Path, *, root: Path | None = None) -> bool:
    try:
        target = _contained_full(path, root)
        if safe_file_stat(target).st_size <= 44:
            return False
        with wave.open(io.BytesIO(safe_read_bytes(target)), "rb") as handle:
            frames = handle.getnframes()
            frame_bytes = handle.getnchannels() * handle.getsampwidth()
            if frames <= 0 or frame_bytes <= 0 or handle.getframerate() <= 0:
                return False
            handle.setpos(frames - 1)
            return len(handle.readframes(1)) == frame_bytes
    except (OSError, EOFError, wave.Error):
        return False


def _contained_child(root: Path, relative: str) -> ContainedPath:
    try:
        return contained_path(root, relative)
    except PathSafetyError as exc:
        raise ReferenceIntegrityError(f"Path escapes evidence root: {relative}") from exc


def validate_sample_references(evidence_root: Path, sample: dict[str, Any]) -> None:
    reference = sample["reference"]
    reference_root = evidence_root / "references"
    for file_key, hash_key in (
        ("source_file", "source_sha256"),
        ("conditioning_file", "conditioning_sha256"),
        ("acted_emotion_reference_file", "acted_emotion_reference_sha256"),
    ):
        relative = reference.get(file_key)
        expected = reference.get(hash_key)
        if not relative:
            if expected:
                raise ReferenceIntegrityError(f"{sample['sample_id']}: {file_key} missing")
            continue
        path = _contained_child(reference_root, str(relative))
        try:
            actual = safe_sha256_file(path)
        except OSError as exc:
            raise ReferenceIntegrityError(
                f"{sample['sample_id']}: {hash_key} mismatch"
            ) from exc
        if not expected or actual != expected:
            raise ReferenceIntegrityError(f"{sample['sample_id']}: {hash_key} mismatch")
    transcript = reference.get("conditioning_transcript")
    transcript_hash = reference.get("conditioning_transcript_sha256")
    if transcript is not None and sha256_text(str(transcript)) != transcript_hash:
        raise ReferenceIntegrityError(
            f"{sample['sample_id']}: conditioning_transcript_sha256 mismatch"
        )


def validate_generation_pair(
    evidence_root: Path,
    sample: dict[str, Any],
    model_contract: dict[str, Any],
    *,
    expected_fingerprint: str | None = None,
    require_control: bool = True,
) -> tuple[dict[str, Any], str]:
    subject = str(sample["sample_id"])
    try:
        artifacts = parse_artifact_paths(
            evidence_root,
            str(sample["output_file"]),
            str(sample["result_file"]),
        )
        safe_file_stat(artifacts.output)
        safe_file_stat(artifacts.result)
    except FileNotFoundError as exc:
        raise GenerationIntegrityError("generation_pair_missing", subject) from exc
    except OSError as exc:
        raise GenerationIntegrityError("generation_path_invalid", subject) from exc
    output = artifacts.output
    receipt_path = artifacts.result
    if output.literal.suffix != ".wav" or receipt_path.literal.suffix != ".json":
        raise GenerationIntegrityError("generation_path_invalid", subject)
    try:
        receipt = json.loads(safe_read_text(receipt_path))
    except FileNotFoundError as exc:
        raise GenerationIntegrityError("generation_pair_missing", subject)
    except (OSError, json.JSONDecodeError) as exc:
        raise GenerationIntegrityError("generation_receipt_invalid", subject) from exc
    expected = expected_fingerprint or sample_fingerprint(sample, model_contract)
    fields = {
        "sample_id": sample["sample_id"],
        "blind_id": sample["blind_id"],
        "model_key": sample["model_key"],
        "target_text_sha256": sample["target_text_sha256"],
        "sample_fingerprint": expected,
    }
    if require_control:
        fields["control"] = sample["control"]
    for key, value in fields.items():
        if receipt.get(key) != value:
            raise GenerationIntegrityError(f"receipt_{key}", subject)
    audio_value = receipt.get("audio_file") or receipt.get("output_file")
    if not audio_value:
        raise GenerationIntegrityError("receipt_audio_file", subject)
    receipt_output = Path(str(audio_value))
    try:
        declared_output = (
            contained_path_from_full(evidence_root, receipt_output)
            if receipt_output.is_absolute()
            else contained_path(evidence_root, str(audio_value))
        )
    except PathSafetyError as exc:
        raise GenerationIntegrityError("receipt_audio_file", subject) from exc
    if declared_output.literal != output.literal:
        raise GenerationIntegrityError("receipt_audio_file", subject)
    audio_sha = safe_sha256_file(output)
    if receipt.get("audio_sha256") != audio_sha:
        raise GenerationIntegrityError("receipt_audio_hash", subject)
    if not wav_is_decodable(output.literal, root=evidence_root):
        raise GenerationIntegrityError("generation_audio_decode", subject)
    return receipt, audio_sha


def partition_generation_samples(
    evidence_root: Path,
    samples: list[dict[str, Any]],
    model_contract: dict[str, Any],
    *,
    reuse_existing: bool,
    max_samples: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pending: list[dict[str, Any]] = []
    reused: list[dict[str, Any]] = []
    for sample in samples:
        if reuse_existing:
            try:
                receipt, _ = validate_generation_pair(evidence_root, sample, model_contract)
            except GenerationIntegrityError:
                pending.append(sample)
            else:
                reused.append(receipt)
            continue
        pending.append(sample)
    if max_samples is not None:
        if max_samples < 0:
            raise GenerationArgumentError("--max-samples must be nonnegative")
        pending = pending[:max_samples]
    return pending, reused
