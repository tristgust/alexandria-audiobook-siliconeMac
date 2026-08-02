from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from model_registry import engine_record_fingerprint, engine_record_payload


ARTIFACT_ADMISSION_SCHEMA_VERSION = 1


class ArtifactAdmissionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise ArtifactAdmissionError(code, message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _publish_without_overwrite(staging: Path, destination: Path) -> None:
    if sys.platform == "darwin":
        rename = ctypes.CDLL(None, use_errno=True).renameatx_np
        rename.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        rename.restype = ctypes.c_int
        result = rename(
            -2,
            os.fsencode(staging),
            -2,
            os.fsencode(destination),
            4,
        )
        if result != 0:
            error_number = ctypes.get_errno()
            if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
                raise FileExistsError(destination)
            raise OSError(error_number, os.strerror(error_number), destination)
        return
    os.rename(staging, destination)


def _safe_relative_path(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        _fail("invalid_path", "Artifact paths must be non-empty text.")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        _fail("unsafe_path", f"Unsafe artifact path: {value!r}.")
    return path


def _validate_safetensors(path: Path) -> None:
    size = path.stat().st_size
    if size < 10:
        _fail("unsafe_serialization", "Safetensors artifact is truncated.")
    with path.open("rb") as handle:
        header_size = int.from_bytes(handle.read(8), "little")
        if header_size <= 1 or header_size > size - 8:
            _fail("unsafe_serialization", "Safetensors header length is invalid.")
        try:
            header = json.loads(handle.read(header_size))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _fail("unsafe_serialization", "Safetensors header is invalid JSON.")
    if not isinstance(header, dict):
        _fail("unsafe_serialization", "Safetensors header must be an object.")
    data_size = size - 8 - header_size
    for name, declaration in header.items():
        if name == "__metadata__":
            continue
        if not isinstance(declaration, dict):
            _fail("unsafe_serialization", "Safetensors tensor metadata is invalid.")
        offsets = declaration.get("data_offsets")
        if (
            not isinstance(offsets, list)
            or len(offsets) != 2
            or any(isinstance(item, bool) or not isinstance(item, int) for item in offsets)
            or offsets[0] < 0
            or offsets[0] > offsets[1]
            or offsets[1] > data_size
        ):
            _fail("unsafe_serialization", "Safetensors data offsets are invalid.")


def _expected_artifacts(engine: dict[str, Any]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for component in engine["components"]:
        declarations = {item["path"]: item for item in component["artifacts"]}
        for relative in component["required_paths"]:
            declaration = declarations[relative]
            artifact_id = f'{component["component_id"]}:{relative}'
            result[artifact_id] = {
                "component_id": component["component_id"],
                "component_revision": component["revision"],
                "component_build_id": component["build_id"],
                "source_id": component["source_id"],
                "path": relative,
                "role": declaration["role"],
                "runtime": component["runtime"],
                "loader": component["loader"],
                "serialization": declaration["serialization"],
            }
    return result


def _validate_manifest(manifest: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    fields = {
        "schema_version",
        "engine_id",
        "engine_revision",
        "record_fingerprint",
        "artifacts",
    }
    if not isinstance(manifest, dict) or set(manifest) != fields:
        _fail("unknown_field", "Admission manifest fields do not match the schema.")
    if manifest["schema_version"] != ARTIFACT_ADMISSION_SCHEMA_VERSION:
        _fail("unsupported_schema", "Admission manifest schema is unsupported.")
    try:
        engine = engine_record_payload(manifest["engine_id"])
    except (KeyError, ValueError):
        _fail("unknown_engine", "Admission manifest names an unknown engine.")
    if manifest["engine_revision"] != engine["engine_revision"]:
        _fail("stale_revision", "Admission manifest engine revision is stale.")
    if manifest["record_fingerprint"] != engine_record_fingerprint(engine):
        _fail("stale_record", "Admission manifest record fingerprint is stale.")
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list):
        _fail("invalid_artifacts", "Admission artifacts must be a list.")
    expected = _expected_artifacts(engine)
    identifiers = [item.get("artifact_id") for item in artifacts if isinstance(item, dict)]
    if len(identifiers) != len(artifacts):
        _fail("invalid_artifact", "Every artifact must be an object with an identifier.")
    if len(identifiers) != len(set(identifiers)):
        _fail("duplicate_id", "Artifact identifiers must be unique.")
    if set(identifiers) != set(expected):
        _fail("artifact_set_mismatch", "Manifest artifacts do not match the engine record.")
    artifact_fields = {
        "artifact_id",
        "component_id",
        "component_revision",
        "component_build_id",
        "source_id",
        "role",
        "path",
        "size",
        "sha256",
        "runtime",
        "loader",
        "serialization",
    }
    for artifact in artifacts:
        if set(artifact) != artifact_fields:
            _fail("unknown_field", "Artifact fields do not match the schema.")
        _safe_relative_path(artifact["path"])
        declaration = expected[artifact["artifact_id"]]
        for field in (
            "component_id",
            "component_revision",
            "component_build_id",
            "source_id",
            "path",
            "role",
            "runtime",
            "serialization",
        ):
            if artifact[field] != declaration[field]:
                if field == "role":
                    roles = {str(artifact[field]), declaration[field]}
                    role = next(
                        (name for name in ("tokenizer", "codec", "adapter") if name in roles),
                        "role",
                    )
                    code = f"incompatible_{role}"
                elif field in {
                    "component_id",
                    "component_revision",
                    "component_build_id",
                    "source_id",
                }:
                    role = declaration["role"]
                    code = (
                        f"incompatible_{role}"
                        if role in {"tokenizer", "codec", "adapter"}
                        else "incompatible_component"
                    )
                else:
                    code = "unsafe_serialization" if field == "serialization" else f"incompatible_{field}"
                _fail(code, f"Artifact {field} differs from the engine record.")
        if artifact["loader"] != declaration["loader"]:
            _fail("incompatible_loader", "Artifact loader differs from the engine record.")
        if isinstance(artifact["size"], bool) or not isinstance(artifact["size"], int) or artifact["size"] < 0:
            _fail("invalid_size", "Artifact sizes must be non-negative integers.")
        digest = artifact["sha256"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            _fail("invalid_digest", "Artifact digest must be a SHA-256 hex digest.")
    return engine, artifacts


def _validate_source(source: Path, artifacts: list[dict[str, Any]]) -> None:
    expected_paths = {artifact["path"] for artifact in artifacts}
    actual_paths = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual_paths != expected_paths:
        missing = expected_paths - actual_paths
        _fail("missing_artifact" if missing else "unexpected_artifact", "Source artifact tree does not match the manifest.")
    for artifact in artifacts:
        path = source / _safe_relative_path(artifact["path"])
        if path.is_symlink() or not path.is_file():
            _fail("unsafe_path", "Admission artifacts must be regular files.")
        if path.stat().st_size != artifact["size"]:
            _fail("size_mismatch", f"Artifact size differs for {artifact['path']!r}.")
        if _sha256(path) != artifact["sha256"]:
            _fail("digest_mismatch", f"Artifact digest differs for {artifact['path']!r}.")
        serialization = artifact["serialization"]
        if serialization == "json":
            try:
                value = json.loads(path.read_bytes())
            except (UnicodeDecodeError, json.JSONDecodeError):
                _fail("unsafe_serialization", "JSON artifact is invalid.")
            if not isinstance(value, (dict, list)):
                _fail("unsafe_serialization", "JSON artifact root is invalid.")
        elif serialization == "safetensors":
            _validate_safetensors(path)
        elif serialization == "text":
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                _fail("unsafe_serialization", "Text artifact is not UTF-8.")
            if "\0" in text:
                _fail("unsafe_serialization", "Text artifact contains null bytes.")
        elif serialization == "numpy_npz":
            try:
                with np.load(path, allow_pickle=False) as archive:
                    for name in archive.files:
                        archive[name]
            except (OSError, ValueError, TypeError):
                _fail("unsafe_serialization", "NumPy artifact is unsafe or invalid.")
        else:
            _fail("unsafe_serialization", "Artifact serialization is not allowlisted.")


def admit_engine_artifacts(
    manifest: Any,
    source: str | Path,
    destination: str | Path,
    *,
    interrupt_after_copy: int | None = None,
) -> dict[str, Any]:
    engine, artifacts = _validate_manifest(manifest)
    source_path = Path(source).expanduser().absolute()
    destination_path = Path(destination).expanduser().absolute()
    if source_path.is_symlink():
        _fail("unsafe_path", "Artifact source directory must not be a symlink.")
    if not source_path.is_dir():
        _fail("missing_source", "Artifact source directory does not exist.")
    _validate_source(source_path, artifacts)
    if destination_path.exists():
        _fail("destination_collision", "Artifact destination already exists.")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{destination_path.name}.admission-",
            dir=destination_path.parent,
        )
    )
    try:
        for copied, artifact in enumerate(artifacts, start=1):
            relative = _safe_relative_path(artifact["path"])
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path / relative, target)
            if _sha256(target) != artifact["sha256"]:
                _fail("copy_digest_mismatch", "Staged artifact digest differs after copy.")
            with target.open("rb") as handle:
                os.fsync(handle.fileno())
            if interrupt_after_copy is not None and copied >= interrupt_after_copy:
                _fail("interrupted", "Artifact admission was interrupted.")
        try:
            _publish_without_overwrite(staging, destination_path)
        except FileExistsError:
            _fail("destination_collision", "Artifact destination already exists.")
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return {
        "schema_version": ARTIFACT_ADMISSION_SCHEMA_VERSION,
        "engine_id": engine["engine_id"],
        "engine_revision": engine["engine_revision"],
        "record_fingerprint": manifest["record_fingerprint"],
        "manifest_sha256": _json_sha256(manifest),
        "artifact_count": len(artifacts),
        "artifacts": [
            {
                "path": artifact["path"],
                "size": artifact["size"],
                "sha256": artifact["sha256"],
            }
            for artifact in sorted(artifacts, key=lambda item: item["path"])
        ],
        "destination": str(destination_path),
    }
