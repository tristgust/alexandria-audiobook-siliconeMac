"""Root-aware MLX manifest, artifact, and reference filesystem boundaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NoReturn

from multimodel_round1_mlx_dependencies import (
    ArtifactPathError,
    ManifestPathError,
    ReferencePathError,
)
from multimodel_round1_paths import (
    ArtifactPaths,
    ContainedPath,
    PathSafetyError,
    contained_path,
    contained_path_from_full,
    safe_atomic_write_bytes,
    safe_atomic_write_text,
    safe_file_stat,
    safe_read_text,
    safe_sha256_file,
)


def assert_never(value: str) -> NoReturn:
    """Reject an unsupported path boundary kind."""

    raise ArtifactPathError(value, "unsupported path boundary kind")


def _raise_path_error(path: str, detail: str, kind: str) -> NoReturn:
    match kind:
        case "manifest":
            raise ManifestPathError(path, detail)
        case "metadata" | "artifact" | "summary":
            raise ArtifactPathError(path, detail)
        case unreachable:
            assert_never(unreachable)


def _relative_path(root: Path, relative: str, *, kind: str) -> ContainedPath:
    if Path(relative).is_absolute():
        _raise_path_error(relative, "absolute manifest path rejected", kind)
    try:
        return contained_path(root, relative)
    except PathSafetyError as exc:
        _raise_path_error(relative, "traversal or invalid component rejected", kind)
        raise AssertionError from exc


def _full_path(root: Path, path: Path, *, kind: str) -> ContainedPath:
    try:
        return contained_path_from_full(root, path)
    except PathSafetyError as exc:
        _raise_path_error(str(path), "path escapes its declared root", kind)
        raise AssertionError from exc


def artifact_paths_for_sample(
    evidence_root: Path, sample: dict[str, Any]
) -> ArtifactPaths:
    """Parse manifest output/result names into root-contained paths."""

    output = _relative_path(
        evidence_root,
        str(sample["output_file"]),
        kind="artifact",
    )
    result = _relative_path(
        evidence_root,
        str(sample["result_file"]),
        kind="artifact",
    )
    return ArtifactPaths(output, result)


def reference_path(evidence_root: Path, relative: str) -> ContainedPath:
    """Parse and pin a manifest reference path under ``references``."""

    try:
        return _relative_path(evidence_root / "references", relative, kind="metadata")
    except ArtifactPathError as exc:
        raise ReferencePathError(Path(relative)) from exc


def contained_artifact_path(
    evidence_root: Path, path: Path, *, kind: str = "artifact"
) -> ContainedPath:
    """Convert an internal artifact path to a root-contained descriptor target."""

    return _full_path(evidence_root, path, kind=kind)


def _safe_text(target: ContainedPath, *, kind: str) -> str:
    try:
        return safe_read_text(target)
    except PathSafetyError as exc:
        _raise_path_error(str(target.literal), "symlink target or ancestor rejected", kind)
        raise AssertionError from exc


def safe_read_json(
    evidence_root: Path,
    relative: str,
    *,
    kind: str = "manifest",
) -> Any:
    """Read one root-contained JSON file without following symlinks."""

    target = _relative_path(evidence_root, relative, kind=kind)
    return json.loads(_safe_text(target, kind=kind))


def safe_write_json(
    evidence_root: Path,
    relative: str,
    value: Any,
    *,
    kind: str = "summary",
) -> Path:
    """Atomically write one root-contained JSON file without following symlinks."""

    target = _relative_path(evidence_root, relative, kind=kind)
    payload = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    try:
        return safe_atomic_write_text(target, payload)
    except PathSafetyError as exc:
        _raise_path_error(str(target.literal), "symlink target or ancestor rejected", kind)
        raise AssertionError from exc


def safe_write_bytes(
    evidence_root: Path,
    path: Path,
    payload: bytes,
    *,
    kind: str = "artifact",
) -> Path:
    """Atomically write bytes to one root-contained internal artifact path."""

    target = contained_artifact_path(evidence_root, path, kind=kind)
    try:
        return safe_atomic_write_bytes(target, payload)
    except PathSafetyError as exc:
        _raise_path_error(str(target.literal), "symlink target or ancestor rejected", kind)
        raise AssertionError from exc


def safe_hash_file(
    evidence_root: Path | None,
    path: Path,
    *,
    kind: str = "artifact",
) -> str:
    """Hash a regular root-contained file while rejecting symlink entries."""

    root = evidence_root or path.parent
    target = contained_artifact_path(root, path, kind=kind)
    try:
        return safe_sha256_file(target)
    except PathSafetyError as exc:
        _raise_path_error(str(target.literal), "symlink target or ancestor rejected", kind)
        raise AssertionError from exc


def safe_stat_file(
    evidence_root: Path,
    path: Path,
    *,
    kind: str = "artifact",
    allow_missing: bool = False,
) -> bool:
    """Check a root-contained regular file without following symlinks."""

    target = contained_artifact_path(evidence_root, path, kind=kind)
    try:
        safe_file_stat(target)
    except FileNotFoundError:
        if allow_missing:
            return False
        raise
    except PathSafetyError as exc:
        _raise_path_error(str(target.literal), "symlink target or ancestor rejected", kind)
        raise AssertionError from exc
    return True
