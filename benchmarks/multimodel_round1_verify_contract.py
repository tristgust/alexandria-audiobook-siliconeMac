"""Typed state and contained document boundaries for Round 1 verification."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
from typing import Any, Final

from multimodel_round1_paths import (
    PathSafetyError,
    SafeRelativePath,
    contained_path,
    safe_read_text,
)
from multimodel_round1_public_audio import SanitizedAudio


DATA_PREFIX: Final = "window.ALEXANDRIA_ROUND1_DATA = "
_DIRECTORY_FLAGS: Final = (
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
)


class VerificationInputError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class VerificationState:
    evidence: Path
    internal: dict[str, Any]
    generated: dict[str, str]
    fingerprints: dict[str, str]
    receipts: dict[str, dict[str, Any]]
    anomaly_manifest: dict[str, Any]
    issues: list[dict[str, str]]


@dataclass(frozen=True, slots=True)
class PublicVerification:
    data: dict[str, Any]
    artifacts: dict[str, SanitizedAudio]


def add_issue(issues: list[dict[str, str]], code: str, subject: str) -> None:
    issues.append({"code": code, "subject": subject})


def read_json(root: Path, relative: str) -> dict[str, Any]:
    return json.loads(safe_read_text(contained_path(root, relative)))


def read_json_rows(root: Path, relative: str) -> list[dict[str, Any]]:
    return json.loads(safe_read_text(contained_path(root, relative)))


def load_public(root: Path, relative: str) -> tuple[dict[str, Any], str]:
    raw = safe_read_text(contained_path(root, relative))
    if not raw.startswith(DATA_PREFIX) or not raw.endswith(";\n"):
        raise VerificationInputError(f"invalid public data wrapper: {relative}")
    return json.loads(raw[len(DATA_PREFIX) : -2]), raw


def read_text(root: Path, relative: str) -> str:
    return safe_read_text(contained_path(root, relative))


def _open_directory(root: Path, relative: SafeRelativePath) -> int:
    descriptor = os.open(root, _DIRECTORY_FLAGS)
    complete = False
    try:
        for part in relative.parts:
            child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        complete = True
        return descriptor
    finally:
        if not complete:
            os.close(descriptor)


def _walk_directory(
    descriptor: int,
    prefix: str,
    files: set[str],
) -> None:
    for name in sorted(os.listdir(descriptor)):
        details = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        relative = f"{prefix}/{name}" if prefix else name
        if stat.S_ISLNK(details.st_mode):
            raise PathSafetyError(relative, "symlink directory entry rejected")
        if stat.S_ISREG(details.st_mode):
            files.add(relative)
        elif stat.S_ISDIR(details.st_mode):
            child = os.open(name, _DIRECTORY_FLAGS, dir_fd=descriptor)
            try:
                _walk_directory(child, relative, files)
            finally:
                os.close(child)
        else:
            raise PathSafetyError(relative, "non-regular directory entry rejected")


def relative_file_tree(root: Path, directory: str) -> set[str]:
    relative = SafeRelativePath(directory)
    try:
        descriptor = _open_directory(root, relative)
    except FileNotFoundError:
        return set()
    files: set[str] = set()
    try:
        _walk_directory(descriptor, "", files)
    finally:
        os.close(descriptor)
    return files
