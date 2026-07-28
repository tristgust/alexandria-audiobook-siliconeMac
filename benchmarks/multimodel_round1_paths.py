"""Contained filesystem paths for multimodel Round 1 artifacts."""

from __future__ import annotations

import errno
import hashlib
import os
import re
import secrets
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterator, NoReturn, Protocol


_IDENTIFIER_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_COMPONENT_RE: Final = re.compile(r"[A-Za-z0-9._-]+")
_DIRECTORY_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ_FLAGS: Final = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
_WRITE_FLAGS: Final = os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC
_UNSAFE_ENTRY_ERRNOS: Final = frozenset((errno.ELOOP, errno.ENOTDIR))


class PathGuard(Protocol):
    def __call__(self, path: Path, *, allow_missing_leaf: bool) -> None: ...


@dataclass(frozen=True, slots=True)
class PathSafetyError(OSError):
    path: str
    reason: str

    def __str__(self) -> str:
        return f"unsafe filesystem path {self.path!r}: {self.reason}"


@dataclass(frozen=True, slots=True)
class SafeIdentifier:
    value: str

    def __post_init__(self) -> None:
        if _IDENTIFIER_RE.fullmatch(self.value) is None:
            raise PathSafetyError(self.value, "identifier is outside the ASCII allowlist")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class SafeRelativePath:
    value: str

    def __post_init__(self) -> None:
        parts = self.value.split("/")
        valid = 0 < len(self.value) <= 4096 and not self.value.startswith("/")
        valid = valid and "\\" not in self.value and all(
            part not in ("", ".", "..") and _COMPONENT_RE.fullmatch(part)
            for part in parts
        )
        if not valid:
            reason = "path must be an allowlisted relative path without traversal"
            raise PathSafetyError(self.value, reason)

    @property
    def parts(self) -> tuple[str, ...]:
        return tuple(self.value.split("/"))

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ContainedPath:
    root: Path
    relative: SafeRelativePath

    def __post_init__(self) -> None:
        absolute_root = Path(os.path.abspath(os.fspath(self.root.expanduser())))
        object.__setattr__(self, "root", absolute_root)

    @property
    def literal(self) -> Path:
        return self.root.joinpath(*self.relative.parts)


@dataclass(frozen=True, slots=True)
class ArtifactPaths:
    output: ContainedPath
    result: ContainedPath


def contained_path(root: Path, relative: str) -> ContainedPath:
    return ContainedPath(root=root, relative=SafeRelativePath(relative))


def contained_path_from_full(root: Path, path: Path) -> ContainedPath:
    if ".." in path.parts:
        raise PathSafetyError(str(path), "literal parent traversal rejected")
    absolute_root = Path(os.path.abspath(os.fspath(root.expanduser())))
    absolute_path = Path(os.path.abspath(os.fspath(path.expanduser())))
    try:
        relative = absolute_path.relative_to(absolute_root)
    except ValueError as exc:
        raise PathSafetyError(str(path), "path escapes its declared root") from exc
    return contained_path(absolute_root, relative.as_posix())


def parse_artifact_paths(root: Path, output_file: str, result_file: str) -> ArtifactPaths:
    return ArtifactPaths(contained_path(root, output_file), contained_path(root, result_file))


def _unsafe_entry(target: ContainedPath, exc: OSError) -> NoReturn:
    if exc.errno in _UNSAFE_ENTRY_ERRNOS:
        reason = "symlink or non-directory ancestor rejected"
        raise PathSafetyError(str(target.literal), reason) from exc
    raise exc


def _open_root(root: Path, target: ContainedPath) -> int:
    try:
        descriptor = os.open(root, _DIRECTORY_FLAGS)
    except OSError as exc:
        _unsafe_entry(target, exc)
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise PathSafetyError(str(root), "containment root is not a directory")
    return descriptor


def _open_child_directory(parent: int, name: str, target: ContainedPath, create: bool) -> int:
    try:
        return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
    except FileNotFoundError:
        if not create:
            raise
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent)
        except FileExistsError:
            os.stat(name, dir_fd=parent, follow_symlinks=False)
        try:
            return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
        except OSError as exc:
            _unsafe_entry(target, exc)
    except OSError as exc:
        _unsafe_entry(target, exc)


def _open_parent(target: ContainedPath, *, create: bool) -> tuple[int, str]:
    descriptor = _open_root(target.root, target)
    complete = False
    try:
        for part in target.relative.parts[:-1]:
            child = _open_child_directory(descriptor, part, target, create)
            os.close(descriptor)
            descriptor = child
        complete = True
    finally:
        if not complete:
            os.close(descriptor)
    return descriptor, target.relative.parts[-1]


def _entry_identity(parent: int, name: str, target: ContainedPath) -> tuple[int, int] | None:
    try:
        details = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(details.st_mode):
        raise PathSafetyError(str(target.literal), "symlink target rejected")
    if not stat.S_ISREG(details.st_mode):
        raise PathSafetyError(str(target.literal), "target is not a regular file")
    return details.st_dev, details.st_ino


def _verify_open_entry(descriptor: int, parent: int, name: str, target: ContainedPath) -> tuple[int, int]:
    details = os.fstat(descriptor)
    if not stat.S_ISREG(details.st_mode):
        raise PathSafetyError(str(target.literal), "opened entry is not a regular file")
    identity = details.st_dev, details.st_ino
    if _entry_identity(parent, name, target) != identity:
        raise PathSafetyError(str(target.literal), "entry changed during open")
    return identity


def _open_read(target: ContainedPath) -> int:
    parent, name = _open_parent(target, create=False)
    descriptor = -1
    complete = False
    try:
        try:
            descriptor = os.open(name, _READ_FLAGS, dir_fd=parent)
        except OSError as exc:
            _unsafe_entry(target, exc)
        _verify_open_entry(descriptor, parent, name, target)
        complete = True
        return descriptor
    finally:
        if descriptor >= 0 and not complete:
            os.close(descriptor)
        os.close(parent)


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written == 0:
            raise OSError(errno.EIO, "zero-byte filesystem write")
        remaining = remaining[written:]


@contextmanager
def _atomic_writer(target: ContainedPath) -> Iterator[int]:
    parent, name = _open_parent(target, create=True)
    original = _entry_identity(parent, name, target)
    temporary = f".round1-{os.getpid()}-{secrets.token_hex(16)}.partial"
    descriptor = -1
    renamed = False
    try:
        descriptor = os.open(temporary, _WRITE_FLAGS | os.O_CREAT | os.O_EXCL,
                             0o600, dir_fd=parent)
        details = os.fstat(descriptor)
        temporary_identity = details.st_dev, details.st_ino
        yield descriptor
        os.fsync(descriptor)
        if _entry_identity(parent, name, target) != original:
            raise PathSafetyError(str(target.literal), "target changed before commit")
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        renamed = True
        if _entry_identity(parent, name, target) != temporary_identity:
            raise PathSafetyError(str(target.literal), "target changed during commit")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not renamed:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                _entry_identity(parent, temporary, target)
        os.close(parent)


def safe_read_bytes(target: ContainedPath) -> bytes:
    descriptor = _open_read(target)
    try:
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def safe_read_text(target: ContainedPath) -> str:
    return safe_read_bytes(target).decode("utf-8")


def safe_atomic_write_bytes(target: ContainedPath, payload: bytes) -> Path:
    with _atomic_writer(target) as descriptor:
        _write_all(descriptor, payload)
    return target.literal


def safe_atomic_write_text(target: ContainedPath, value: str) -> Path:
    return safe_atomic_write_bytes(target, value.encode("utf-8"))


def safe_atomic_copy(source: ContainedPath, target: ContainedPath) -> Path:
    source_descriptor = _open_read(source)
    try:
        with _atomic_writer(target) as target_descriptor:
            while chunk := os.read(source_descriptor, 1024 * 1024):
                _write_all(target_descriptor, chunk)
    finally:
        os.close(source_descriptor)
    return target.literal


def guard_path(path: Path, *, allow_missing_leaf: bool, root: Path | None = None) -> None:
    literal = Path(os.path.abspath(os.fspath(path.expanduser())))
    target = contained_path_from_full(root or literal.parent, path)
    parent, name = _open_parent(target, create=False)
    try:
        identity = _entry_identity(parent, name, target)
        if identity is None and not allow_missing_leaf:
            raise FileNotFoundError(target.literal)
    finally:
        os.close(parent)


def contained_path_guard(root: Path) -> PathGuard:
    def guarded(path: Path, *, allow_missing_leaf: bool) -> None:
        guard_path(path, allow_missing_leaf=allow_missing_leaf, root=root)

    return guarded


def safe_sha256_file(target: ContainedPath) -> str:
    descriptor = _open_read(target)
    digest = hashlib.sha256()
    try:
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def safe_file_stat(target: ContainedPath) -> os.stat_result:
    descriptor = _open_read(target)
    try:
        return os.fstat(descriptor)
    finally:
        os.close(descriptor)
