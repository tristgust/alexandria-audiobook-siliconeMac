"""Fail-closed disk and Metal-lease controls for Round 1 workers."""

from __future__ import annotations

import errno
import fcntl
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Final, Iterator, TypedDict

from multimodel_round1_paths import (
    ContainedPath,
    PathSafetyError,
    SafeIdentifier,
    _entry_identity,
    _open_parent,
    _open_root,
    _unsafe_entry,
    _verify_open_entry,
    _write_all,
    contained_path,
    contained_path_from_full,
)


STRICT_FREE_FLOOR_BYTES: Final = 30 * 1024**3
DEFAULT_SAFETY_MARGIN_BYTES: Final = 2 * 1024**3
PROJECTED_SAMPLE_BYTES: Final = 256 * 1024**2


class DiskHeadroomStatus(TypedDict):
    ok: bool
    checked_at_unix: float
    path: str
    free_bytes: int
    projected_bytes: int
    safety_margin_bytes: int
    strict_floor_bytes: int
    remaining_after_reservations_bytes: int


class DiskHeadroomReceipt(DiskHeadroomStatus):
    stage: str
    sample_id: str | None
    pid: int


@dataclass(frozen=True, slots=True)
class InvalidDiskReservationError(ValueError):
    projected_bytes: int
    safety_margin_bytes: int

    def __str__(self) -> str:
        return "disk reservations must be nonnegative"


@dataclass(frozen=True, slots=True)
class DiskHeadroomError(RuntimeError):
    remaining_bytes: int

    def __str__(self) -> str:
        return "disk headroom would not remain strictly above 30 GiB"


@dataclass(frozen=True, slots=True)
class MetalLockBusyError(RuntimeError):
    path: Path

    def __str__(self) -> str:
        return f"Metal lock is already held: {self.path}"


@dataclass(frozen=True, slots=True)
class _PinnedEntry:
    """Mutable descriptor state pinned to one literal regular-file entry."""

    descriptor: int
    parent: int
    name: str
    target: ContainedPath
    identity: tuple[int, int]

    def verify(self) -> None:
        actual = _verify_open_entry(
            self.descriptor, self.parent, self.name, self.target
        )
        if actual != self.identity:
            raise PathSafetyError(str(self.target.literal), "entry identity changed")

    def close(self) -> None:
        if self.descriptor < 0:
            return
        os.close(self.descriptor)
        os.close(self.parent)
        object.__setattr__(self, "descriptor", -1)
        object.__setattr__(self, "parent", -1)


def _open_pinned(target: ContainedPath, flags: int) -> _PinnedEntry:
    parent, name = _open_parent(target, create=True)
    expected = _entry_identity(parent, name, target)
    descriptor = -1
    complete = False
    try:
        safe_flags = flags | os.O_NOFOLLOW | os.O_CLOEXEC
        if expected is None:
            try:
                descriptor = os.open(
                    name,
                    safe_flags | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=parent,
                )
            except FileExistsError as exc:
                raise PathSafetyError(
                    str(target.literal), "entry appeared during exclusive create"
                ) from exc
        else:
            try:
                descriptor = os.open(name, safe_flags, dir_fd=parent)
            except OSError as exc:
                _unsafe_entry(target, exc)
        identity = _verify_open_entry(descriptor, parent, name, target)
        if expected is not None and identity != expected:
            raise PathSafetyError(str(target.literal), "entry changed during open")
        complete = True
        return _PinnedEntry(descriptor, parent, name, target, identity)
    finally:
        if not complete:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(parent)


def _literal_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _safe_disk_free_bytes(path: Path) -> int:
    probe = contained_path(path, ".disk-probe")
    descriptor = _open_root(probe.root, probe)
    try:
        details = os.fstatvfs(descriptor)
        return details.f_bavail * details.f_frsize
    finally:
        os.close(descriptor)


def disk_headroom_status(
    path: Path,
    *,
    projected_bytes: int,
    safety_margin_bytes: int = DEFAULT_SAFETY_MARGIN_BYTES,
    free_bytes: int | None = None,
) -> DiskHeadroomStatus:
    if projected_bytes < 0 or safety_margin_bytes < 0:
        raise InvalidDiskReservationError(projected_bytes, safety_margin_bytes)
    free = free_bytes if free_bytes is not None else _safe_disk_free_bytes(path)
    remaining = free - projected_bytes - safety_margin_bytes
    return {
        "ok": remaining > STRICT_FREE_FLOOR_BYTES,
        "checked_at_unix": time.time(),
        "path": str(_literal_absolute(path)),
        "free_bytes": free,
        "projected_bytes": projected_bytes,
        "safety_margin_bytes": safety_margin_bytes,
        "strict_floor_bytes": STRICT_FREE_FLOOR_BYTES,
        "remaining_after_reservations_bytes": remaining,
    }


def _append_receipt(target: ContainedPath, payload: bytes) -> None:
    entry = _open_pinned(target, os.O_WRONLY | os.O_APPEND)
    try:
        fcntl.flock(entry.descriptor, fcntl.LOCK_EX)
        entry.verify()
        _write_all(entry.descriptor, payload)
        os.fsync(entry.descriptor)
        entry.verify()
    finally:
        entry.close()


def require_disk_headroom(
    path: Path,
    *,
    projected_bytes: int,
    safety_margin_bytes: int = DEFAULT_SAFETY_MARGIN_BYTES,
    free_bytes: int | None = None,
    receipt_path: Path | None = None,
    stage: str,
    sample_id: str | None = None,
) -> DiskHeadroomReceipt:
    safe_stage = SafeIdentifier(stage)
    safe_sample_id = SafeIdentifier(sample_id) if sample_id is not None else None
    status = disk_headroom_status(
        path,
        projected_bytes=projected_bytes,
        safety_margin_bytes=safety_margin_bytes,
        free_bytes=free_bytes,
    )
    record: DiskHeadroomReceipt = {
        **status,
        "stage": str(safe_stage),
        "sample_id": str(safe_sample_id) if safe_sample_id is not None else None,
        "pid": os.getpid(),
    }
    if receipt_path is not None:
        receipt = contained_path_from_full(path, receipt_path)
        payload = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
        _append_receipt(receipt, payload)
    if not record["ok"]:
        raise DiskHeadroomError(record["remaining_after_reservations_bytes"])
    return record


class MetalLease:
    """Mutable owner of an advisory lock on a pinned literal entry."""

    __slots__ = ("_entry", "path")

    def __init__(self, entry: _PinnedEntry):
        self._entry = entry
        self.path = entry.target.literal

    def close(self) -> None:
        if self._entry.descriptor < 0:
            return
        try:
            fcntl.flock(self._entry.descriptor, fcntl.LOCK_UN)
        finally:
            self._entry.close()

    def __enter__(self) -> MetalLease:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()


def acquire_metal_lock(path: str | Path, *, purpose: str) -> MetalLease:
    safe_purpose = SafeIdentifier(purpose)
    raw_path = Path(path)
    if ".." in raw_path.parts:
        raise PathSafetyError(str(path), "literal parent traversal rejected")
    literal = _literal_absolute(raw_path)
    entry = _open_pinned(contained_path(literal.parent, literal.name), os.O_RDWR)
    try:
        fcntl.flock(entry.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        entry.close()
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            raise MetalLockBusyError(literal) from exc
        raise
    complete = False
    try:
        entry.verify()
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "purpose": str(safe_purpose),
                "acquired_at_unix": time.time(),
            },
            sort_keys=True,
        ).encode("utf-8")
        os.ftruncate(entry.descriptor, 0)
        _write_all(entry.descriptor, payload)
        os.fsync(entry.descriptor)
        entry.verify()
        complete = True
        return MetalLease(entry)
    finally:
        if not complete:
            entry.close()


@contextmanager
def metal_generation_lock(path: str | Path, *, purpose: str) -> Iterator[MetalLease]:
    lease = acquire_metal_lock(path, purpose=purpose)
    try:
        yield lease
    finally:
        lease.close()
