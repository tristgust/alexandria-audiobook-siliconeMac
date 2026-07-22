from __future__ import annotations

import base64
import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from audio_artifacts import (
    AudioArtifactError,
    audio_backup_map,
    backup_operation_audio,
    consume_operation_audio_backups,
    remove_restored_operation_audio,
    restore_operation_audio,
    validate_operation_audio_backups,
)
from approved_audio import active_approved_audio_lock
from generation_state import atomic_json_write, fingerprint_value


AUDIO_INVALIDATION_SCHEMA_VERSION = 2
AUDIO_INVALIDATION_HISTORY_DIRNAME = "audio_invalidation_history"


class AudioInvalidationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def normalize_audio_invalidation(
    value: Mapping[str, Any],
    *,
    operation_id: str,
    operation: str,
    default_reason: str,
) -> dict[str, Any]:
    item = copy.deepcopy(dict(value))
    old_chunk_id = item.get("old_chunk_id", item.get("chunk_id"))
    new_chunk_id = item.get("new_chunk_id")
    canonical_path = _text(
        item.get("canonical_audio_path", item.get("audio_path"))
    )
    backup_path = _text(item.get("backup_audio_path"))
    reason = _text(item.get("reason")) or default_reason
    result = {
        "schema_version": AUDIO_INVALIDATION_SCHEMA_VERSION,
        "invalidation_id": "audio_invalid_" + fingerprint_value(
            {
                "operation_id": operation_id,
                "operation": operation,
                "old_chunk_id": old_chunk_id,
                "new_chunk_id": new_chunk_id,
                "canonical_audio_path": canonical_path,
                "backup_audio_path": backup_path,
                "reason": reason,
            }
        )[:24],
        "operation_id": operation_id,
        "operation": operation,
        "old_chunk_id": old_chunk_id,
        "new_chunk_id": new_chunk_id,
        "chunk_id": new_chunk_id if new_chunk_id is not None else old_chunk_id,
        "speaker": item.get("speaker"),
        "canonical_audio_path": canonical_path,
        "audio_path": canonical_path,
        "backup_audio_path": backup_path,
        "audio_sha256": _text(item.get("audio_sha256")),
        "audio_size_bytes": item.get("audio_size_bytes"),
        "reason": reason,
        "dependency_kind": _text(item.get("dependency_kind")) or "production_audio",
        "undo_available": bool(backup_path and item.get("audio_sha256")),
    }
    for key in (
        "audio_duration_ms",
        "audio_format",
        "voice_fingerprint",
        "script_fingerprint",
        "pronunciation_fingerprint",
        "settings_fingerprint",
        "seed_fingerprint",
    ):
        if key in item:
            result[key] = copy.deepcopy(item[key])
    return result


def build_audio_validity_record(
    *,
    operation_id: str,
    operation: str,
    at_utc: str,
    invalidations: Iterable[Mapping[str, Any]],
    note: str,
    default_reason: str,
) -> dict[str, Any]:
    normalized = [
        normalize_audio_invalidation(
            item,
            operation_id=operation_id,
            operation=operation,
            default_reason=default_reason,
        )
        for item in invalidations
    ]
    return {
        "schema_version": AUDIO_INVALIDATION_SCHEMA_VERSION,
        "stale": bool(normalized),
        "last_operation_id": operation_id,
        "operation": operation,
        "updated_at_utc": at_utc,
        "invalidated_chunks": normalized,
        "invalidation_fingerprint": fingerprint_value(normalized),
        "undo_available": any(item["undo_available"] for item in normalized),
        "note": note,
    }


def build_audio_invalidation_operation(
    *,
    operation_id: str,
    operation: str,
    at_utc: str,
    invalidations: Iterable[Mapping[str, Any]],
    note: str,
    default_reason: str,
) -> dict[str, Any]:
    validity = build_audio_validity_record(
        operation_id=operation_id,
        operation=operation,
        at_utc=at_utc,
        invalidations=invalidations,
        note=note,
        default_reason=default_reason,
    )
    normalized = validity["invalidated_chunks"]
    return {
        "schema_version": AUDIO_INVALIDATION_SCHEMA_VERSION,
        "record_kind": "audio_invalidation_operation",
        "operation_id": operation_id,
        "operation": operation,
        "at_utc": at_utc,
        "reason": default_reason,
        "note": note,
        "affected_speakers": sorted(
            {
                str(item.get("speaker")).strip()
                for item in normalized
                if str(item.get("speaker") or "").strip()
            }
        ),
        "affected_chunk_ids": sorted(
            {
                item.get("chunk_id")
                for item in normalized
                if item.get("chunk_id") is not None
            },
            key=str,
        ),
        "invalidated_chunks": copy.deepcopy(normalized),
        "invalidation_fingerprint": validity["invalidation_fingerprint"],
        "undo_available": validity["undo_available"],
    }


def _snapshot_bytes(value: bytes | None) -> dict[str, Any]:
    return {
        "exists": value is not None,
        "sha256": fingerprint_value(value.hex()) if value is not None else None,
        "content_base64": base64.b64encode(value).decode("ascii") if value is not None else None,
    }


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _atomic_bytes(path: Path, value: bytes | None) -> None:
    if value is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def _decode_snapshot(snapshot: Mapping[str, Any]) -> bytes | None:
    if not snapshot.get("exists"):
        return None
    value = snapshot.get("content_base64")
    if not isinstance(value, str):
        raise AudioInvalidationError(
            "audio_invalidation_snapshot_invalid",
            "Audio invalidation snapshot is missing its saved bytes.",
        )
    try:
        return base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise AudioInvalidationError(
            "audio_invalidation_snapshot_invalid",
            "Audio invalidation snapshot bytes are invalid.",
        ) from exc


def _confined_project_path(root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise AudioInvalidationError(
            "audio_invalidation_dependency_unsafe",
            "Audio invalidation files must remain inside the project root.",
        ) from exc
    return path


def _attach_transaction_backup_state(
    *,
    root: Path,
    changes: dict[Path, Any],
    backups: list[dict[str, Any]],
    operation_id: str,
    validity_path: Path,
) -> None:
    mapping = audio_backup_map(backups)
    chunks = changes.get(root / "chunks.json")
    if isinstance(chunks, list):
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            stale_path = _text(chunk.get("stale_audio_path"))
            backup = mapping.get(stale_path or "")
            if backup is None:
                continue
            chunk["stale_audio_path"] = backup["backup_path"]
            chunk["audio_state"] = "stale"
            chunk["invalidated_by_operation"] = operation_id
            for field in (
                "audio_fingerprint",
                "audio_sha256",
                "audio_size_bytes",
                "audio_duration_ms",
                "audio_format",
                "error",
            ):
                chunk[field] = None
    validity = changes.get(validity_path)
    if isinstance(validity, Mapping):
        changes[validity_path] = attach_audio_backup_evidence(
            validity,
            mapping,
        )


def apply_audio_invalidation_transaction(
    *,
    project_root: str | Path,
    operation_dir: str | Path,
    operation_id: str,
    operation: str,
    at_utc: str,
    changes: Mapping[str | Path, Any],
    invalidations: Iterable[Mapping[str, Any]],
    default_reason: str,
    note: str,
    record_metadata: Mapping[str, Any] | None = None,
    record_schema_version: int = AUDIO_INVALIDATION_SCHEMA_VERSION,
    history_path: str | Path | None = None,
    tracked_before: Mapping[str | Path, bytes | None] | None = None,
    validity_path: str | Path = "audio_validity.json",
    json_writer: Any = None,
) -> dict[str, Any]:
    writer = json_writer or atomic_json_write
    root = Path(project_root).expanduser().resolve()
    directory = _confined_project_path(root, operation_dir)
    record_target = _confined_project_path(
        root,
        history_path if history_path is not None else directory / "operation.json",
    )
    validity_target = _confined_project_path(root, validity_path)
    normalized_changes = {
        _confined_project_path(root, path): copy.deepcopy(value)
        for path, value in changes.items()
    }
    before_overrides = {
        _confined_project_path(root, path): value
        for path, value in (tracked_before or {}).items()
    }
    raw_invalidations = [copy.deepcopy(dict(item)) for item in invalidations]
    validity = build_audio_validity_record(
        operation_id=operation_id,
        operation=operation,
        at_utc=at_utc,
        invalidations=raw_invalidations,
        note=note,
        default_reason=default_reason,
    )
    if raw_invalidations or validity_target in normalized_changes:
        normalized_changes[validity_target] = validity
    canonical_paths = [
        item.get("canonical_audio_path", item.get("audio_path"))
        for item in validity["invalidated_chunks"]
    ]
    backups = backup_operation_audio(
        root_dir=root,
        operation_dir=directory,
        relative_paths=canonical_paths,
    )
    _attach_transaction_backup_state(
        root=root,
        changes=normalized_changes,
        backups=backups,
        operation_id=operation_id,
        validity_path=validity_target,
    )
    validity = normalized_changes.get(validity_target, validity)
    canonical_record = build_audio_invalidation_operation(
        operation_id=operation_id,
        operation=operation,
        at_utc=at_utc,
        invalidations=validity.get("invalidated_chunks", []),
        note=note,
        default_reason=default_reason,
    )
    tracked_paths = sorted(
        set(normalized_changes) | set(before_overrides),
        key=lambda path: path.as_posix(),
    )
    before = {
        path: _snapshot_bytes(
            before_overrides[path]
            if path in before_overrides
            else _read_bytes(path)
        )
        for path in tracked_paths
    }
    written: list[Path] = []
    try:
        for path in sorted(normalized_changes, key=lambda item: item.as_posix()):
            value = normalized_changes[path]
            if value is None:
                _atomic_bytes(path, None)
            else:
                writer(value, path)
            written.append(path)
        after = {
            path: _snapshot_bytes(_read_bytes(path))
            for path in tracked_paths
        }
        metadata = copy.deepcopy(dict(record_metadata or {}))
        record = {
            **metadata,
            "schema_version": record_schema_version,
            "operation_id": operation_id,
            "operation": operation,
            "at_utc": at_utc,
            "audio_invalidation": canonical_record,
            "audio_backups": copy.deepcopy(backups),
            "files": {
                path.relative_to(root).as_posix(): {
                    "before": before[path],
                    "after": after[path],
                    "after_sha256": after[path]["sha256"],
                }
                for path in tracked_paths
            },
            "status": "applied",
            "undone_at_utc": None,
        }
        writer(record, record_target)
        return record
    except Exception:
        for path in reversed(tracked_paths):
            _atomic_bytes(path, _decode_snapshot(before[path]))
        try:
            restore_operation_audio(
                root_dir=root,
                records=backups,
                require_original_absent=False,
                consume_backups=True,
            )
        except Exception:
            pass
        raise


def undo_audio_invalidation_transaction(
    *,
    project_root: str | Path,
    record_path: str | Path,
    undone_at_utc: str,
    consume_backups: bool = False,
    mark_record_undone: bool = True,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    target = _confined_project_path(root, record_path)
    try:
        record = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AudioInvalidationError(
            "audio_invalidation_operation_missing",
            f"Audio invalidation operation could not be read: {exc}",
        ) from exc
    files = record.get("files")
    if not isinstance(files, Mapping):
        raise AudioInvalidationError(
            "audio_invalidation_operation_invalid",
            "Audio invalidation operation has no valid file snapshots.",
        )
    if record.get("status") == "undone":
        raise AudioInvalidationError(
            "audio_invalidation_already_undone",
            "This audio invalidation operation is not available for undo.",
        )
    for relative, state in files.items():
        path = _confined_project_path(root, relative)
        expected = state.get("after") if isinstance(state, Mapping) else None
        current = _snapshot_bytes(_read_bytes(path))
        expected_hash = (
            expected.get("sha256")
            if isinstance(expected, Mapping)
            else state.get("after_sha256")
            if isinstance(state, Mapping)
            else None
        )
        expected_exists = (
            bool(expected.get("exists"))
            if isinstance(expected, Mapping)
            else expected_hash is not None
        )
        if current.get("exists") != expected_exists or current.get("sha256") != expected_hash:
            raise AudioInvalidationError(
                "audio_invalidation_undo_conflict",
                f"Cannot undo because {relative} changed after the invalidation.",
            )
    backups = list(record.get("audio_backups") or [])
    try:
        validate_operation_audio_backups(
            root_dir=root,
            records=backups,
            require_original_absent=True,
        )
    except AudioArtifactError as exc:
        raise AudioInvalidationError(
            "audio_invalidation_undo_conflict",
            str(exc),
        ) from exc
    current_snapshots = {
        relative: _snapshot_bytes(_read_bytes(_confined_project_path(root, relative)))
        for relative in files
    }
    restored_files: list[str] = []
    restored_audio: list[str] = []
    try:
        for relative, state in files.items():
            path = _confined_project_path(root, relative)
            before = state.get("before") if isinstance(state, Mapping) else None
            if not isinstance(before, Mapping):
                raise AudioInvalidationError(
                    "audio_invalidation_operation_invalid",
                    f"Stored snapshot for {relative} is invalid.",
                )
            _atomic_bytes(path, _decode_snapshot(before))
            restored_files.append(str(relative))
        restored_audio = restore_operation_audio(
            root_dir=root,
            records=backups,
            require_original_absent=True,
            consume_backups=False,
        )
    except Exception:
        for relative, snapshot in current_snapshots.items():
            _atomic_bytes(
                _confined_project_path(root, relative),
                _decode_snapshot(snapshot),
            )
        remove_restored_operation_audio(
            root_dir=root,
            records=backups,
        )
        raise
    cleanup = {
        "status": "not_needed",
        "removed_paths": [],
        "already_missing_paths": [],
        "failed_paths": [],
    }
    if consume_backups and backups:
        cleanup = consume_operation_audio_backups(
            root_dir=root,
            records=backups,
        )
    if mark_record_undone:
        record["status"] = "undone"
        record["undone_at_utc"] = undone_at_utc
        atomic_json_write(record, target)
    return {
        "status": "undone",
        "operation_id": record.get("operation_id"),
        "restored_files": sorted(restored_files),
        "restored_audio_paths": sorted(restored_audio),
        "audio_backup_cleanup": cleanup,
    }


def apply_speaker_audio_dependency_change(
    *,
    project_root: str | Path,
    operation_id: str,
    operation: str,
    at_utc: str,
    speakers: Iterable[str],
    reason: str,
    changes: Mapping[str | Path, Any],
    dependency_kind: str = "production_audio",
    note: str | None = None,
    history_dirname: str = AUDIO_INVALIDATION_HISTORY_DIRNAME,
    record_metadata: Mapping[str, Any] | None = None,
    record_schema_version: int = AUDIO_INVALIDATION_SCHEMA_VERSION,
    json_writer: Any = None,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    selected = {
        str(value).strip().casefold()
        for value in speakers
        if str(value).strip()
    }
    chunks_path = root / "chunks.json"
    if chunks_path.exists():
        try:
            chunks_value = json.loads(chunks_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AudioInvalidationError(
                "audio_invalidation_chunks_invalid",
                f"Project chunks could not be read: {exc}",
            ) from exc
        if not isinstance(chunks_value, list):
            raise AudioInvalidationError(
                "audio_invalidation_chunks_invalid",
                "Project chunks must contain a JSON array.",
            )
    else:
        chunks_value = []
    invalidations: list[dict[str, Any]] = []
    changed_chunks = copy.deepcopy(chunks_value)
    for index, chunk in enumerate(changed_chunks):
        if not isinstance(chunk, dict):
            continue
        if str(chunk.get("speaker") or "").strip().casefold() not in selected:
            continue
        old_path = _text(chunk.get("audio_path"))
        if old_path is None and chunk.get("status") != "done":
            continue
        invalidations.append(
            {
                "chunk_id": chunk.get("id", index),
                "speaker": chunk.get("speaker"),
                "audio_path": old_path,
                "reason": reason,
                "dependency_kind": dependency_kind,
                "voice_fingerprint": chunk.get("voice_fingerprint"),
                "script_fingerprint": chunk.get("script_fingerprint"),
                "pronunciation_fingerprint": chunk.get(
                    "pronunciation_fingerprint"
                ),
                "settings_fingerprint": chunk.get("settings_fingerprint"),
                "seed_fingerprint": chunk.get("seed_fingerprint"),
            }
        )
        chunk["status"] = "pending"
        chunk["audio_path"] = None
        chunk["stale_audio_path"] = old_path
        chunk["audio_state"] = "stale" if old_path else "pending"
        chunk["invalidated_by_operation"] = operation_id
        for field in (
            "audio_fingerprint",
            "audio_sha256",
            "audio_size_bytes",
            "audio_duration_ms",
            "audio_format",
            "error",
        ):
            chunk[field] = None
    transaction_changes = {
        _confined_project_path(root, path): copy.deepcopy(value)
        for path, value in changes.items()
    }
    if invalidations:
        transaction_changes[chunks_path] = changed_chunks
    return apply_audio_invalidation_transaction(
        project_root=root,
        operation_dir=root / history_dirname / operation_id,
        operation_id=operation_id,
        operation=operation,
        at_utc=at_utc,
        changes=transaction_changes,
        invalidations=invalidations,
        default_reason=reason,
        note=(
            note
            or "Production audio was moved to content-addressed backup and must be regenerated after the dependency change."
        ),
        record_metadata=record_metadata,
        record_schema_version=record_schema_version,
        json_writer=json_writer,
    )


def apply_project_audio_invalidation(
    *,
    project_root: str | Path,
    operation_id: str,
    operation: str,
    at_utc: str,
    speakers: Iterable[str],
    reason: str,
    dependency_before: Mapping[str | Path, bytes | None],
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    selected = {str(value).strip().casefold() for value in speakers if str(value).strip()}
    chunks_path = root / "chunks.json"
    validity_path = root / "audio_validity.json"
    operation_dir = root / AUDIO_INVALIDATION_HISTORY_DIRNAME / operation_id
    record_path = operation_dir / "operation.json"
    if record_path.exists():
        raise AudioInvalidationError(
            "audio_invalidation_operation_exists",
            "This audio invalidation operation already exists.",
        )
    chunks_exist = chunks_path.exists()
    if chunks_exist:
        try:
            chunks_value = json.loads(chunks_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AudioInvalidationError(
                "audio_invalidation_chunks_invalid",
                f"Project chunks could not be read: {exc}",
            ) from exc
        if not isinstance(chunks_value, list):
            raise AudioInvalidationError(
                "audio_invalidation_chunks_invalid",
                "Project chunks must contain a JSON array.",
            )
        before_chunks = chunks_path.read_bytes()
    else:
        chunks_value = []
        before_chunks = None
    before_validity = _read_bytes(validity_path)
    affected = [
        chunk
        for chunk in chunks_value
        if isinstance(chunk, dict)
        and str(chunk.get("speaker") or "").strip().casefold() in selected
        and (chunk.get("audio_path") or chunk.get("status") == "done")
        and active_approved_audio_lock(chunk) is None
    ]
    audio_paths = [chunk.get("audio_path") for chunk in affected]
    backups = backup_operation_audio(
        root_dir=root,
        operation_dir=operation_dir,
        relative_paths=audio_paths,
    )
    backup_map = audio_backup_map(backups)
    invalidations = []
    changed_ids = set()
    for index, chunk in enumerate(chunks_value):
        if not isinstance(chunk, dict):
            continue
        if str(chunk.get("speaker") or "").strip().casefold() not in selected:
            continue
        if active_approved_audio_lock(chunk) is not None:
            continue
        old_path = _text(chunk.get("audio_path"))
        if old_path is None and chunk.get("status") != "done":
            continue
        backup = backup_map.get(old_path or "")
        invalidations.append(
            {
                "chunk_id": chunk.get("id", index),
                "speaker": chunk.get("speaker"),
                "audio_path": old_path,
                "backup_audio_path": backup.get("backup_path") if backup else None,
                "audio_sha256": backup.get("sha256") if backup else None,
                "audio_size_bytes": backup.get("size_bytes") if backup else None,
                "reason": reason,
            }
        )
        changed_ids.add(chunk.get("id", index))
        chunk["status"] = "pending"
        chunk["audio_path"] = None
        chunk["stale_audio_path"] = backup.get("backup_path") if backup else old_path
        chunk["audio_state"] = "stale" if old_path else "pending"
        chunk["invalidated_by_operation"] = operation_id
        for field in (
            "audio_fingerprint",
            "audio_sha256",
            "audio_size_bytes",
            "audio_duration_ms",
            "audio_format",
            "error",
        ):
            chunk[field] = None
    validity = build_audio_validity_record(
        operation_id=operation_id,
        operation=operation,
        at_utc=at_utc,
        invalidations=invalidations,
        default_reason=reason,
        note="Production audio was moved to content-addressed backup and must be regenerated after the dependency change.",
    )
    dependency_snapshots = {}
    for value, before in dependency_before.items():
        path = Path(value).expanduser().resolve()
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError as exc:
            raise AudioInvalidationError(
                "audio_invalidation_dependency_unsafe",
                "Dependency files must remain inside the project root.",
            ) from exc
        dependency_snapshots[relative] = {
            "before": _snapshot_bytes(before),
            "after": _snapshot_bytes(_read_bytes(path)),
        }
    tracked_files = {
        **dependency_snapshots,
    }
    if invalidations:
        tracked_files.update(
            {
                "chunks.json": {
                    "before": _snapshot_bytes(before_chunks),
                    "after": None,
                },
                "audio_validity.json": {
                    "before": _snapshot_bytes(before_validity),
                    "after": None,
                },
            }
        )
    record = {
        "schema_version": AUDIO_INVALIDATION_SCHEMA_VERSION,
        "operation_id": operation_id,
        "operation": operation,
        "at_utc": at_utc,
        "speakers": sorted(selected),
        "reason": reason,
        "audio_invalidation": build_audio_invalidation_operation(
            operation_id=operation_id,
            operation=operation,
            at_utc=at_utc,
            invalidations=validity["invalidated_chunks"],
            note=validity["note"],
            default_reason=reason,
        ),
        "affected_chunk_ids": sorted(changed_ids, key=str),
        "audio_backups": backups,
        "files": tracked_files,
        "status": "applied",
        "undone_at_utc": None,
    }
    try:
        if invalidations:
            atomic_json_write(chunks_value, chunks_path)
            atomic_json_write(validity, validity_path)
            record["files"]["chunks.json"]["after"] = _snapshot_bytes(chunks_path.read_bytes())
            record["files"]["audio_validity.json"]["after"] = _snapshot_bytes(validity_path.read_bytes())
        atomic_json_write(record, record_path)
    except Exception:
        if invalidations:
            _atomic_bytes(chunks_path, before_chunks)
            _atomic_bytes(validity_path, before_validity)
        restore_operation_audio(
            root_dir=root,
            records=backups,
            require_original_absent=False,
            consume_backups=False,
        )
        raise
    return record


def undo_project_audio_invalidation(
    *,
    project_root: str | Path,
    operation_id: str,
    undone_at_utc: str,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    record_path = root / AUDIO_INVALIDATION_HISTORY_DIRNAME / operation_id / "operation.json"
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AudioInvalidationError(
            "audio_invalidation_operation_missing",
            f"Audio invalidation operation could not be read: {exc}",
        ) from exc
    if record.get("status") != "applied":
        raise AudioInvalidationError(
            "audio_invalidation_already_undone",
            "This audio invalidation operation is not available for undo.",
        )
    for relative, snapshots in record.get("files", {}).items():
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise AudioInvalidationError(
                "audio_invalidation_dependency_unsafe",
                "Saved audio invalidation path escaped the project root.",
            ) from exc
        expected = snapshots.get("after") or {}
        current = _snapshot_bytes(_read_bytes(path))
        if current.get("exists") != expected.get("exists") or current.get("sha256") != expected.get("sha256"):
            raise AudioInvalidationError(
                "audio_invalidation_undo_conflict",
                f"Cannot undo because {relative} changed after the invalidation.",
            )
    restore_operation_audio(
        root_dir=root,
        records=list(record.get("audio_backups") or []),
        require_original_absent=True,
        consume_backups=False,
    )
    restored = []
    try:
        for relative, snapshots in record.get("files", {}).items():
            path = (root / relative).resolve()
            _atomic_bytes(path, _decode_snapshot(snapshots.get("before") or {}))
            restored.append(relative)
        record["status"] = "undone"
        record["undone_at_utc"] = undone_at_utc
        atomic_json_write(record, record_path)
    except Exception:
        for relative, snapshots in record.get("files", {}).items():
            path = (root / relative).resolve()
            _atomic_bytes(path, _decode_snapshot(snapshots.get("after") or {}))
        raise
    return {
        "status": "undone",
        "operation_id": operation_id,
        "restored_files": sorted(restored),
        "restored_audio_paths": [item.get("original_path") for item in record.get("audio_backups") or []],
    }


def attach_audio_backup_evidence(
    validity: Mapping[str, Any],
    backup_by_path: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    updated = copy.deepcopy(dict(validity))
    operation_id = str(updated.get("last_operation_id") or "")
    operation = str(updated.get("operation") or "")
    refreshed = []
    for raw in updated.get("invalidated_chunks") or []:
        item = copy.deepcopy(dict(raw))
        canonical = _text(
            item.get("canonical_audio_path", item.get("audio_path"))
        )
        backup = backup_by_path.get(canonical or "")
        if backup is not None:
            item["canonical_audio_path"] = backup.get("original_path", canonical)
            item["backup_audio_path"] = backup.get("backup_path")
            item["audio_sha256"] = backup.get("sha256")
            item["audio_size_bytes"] = backup.get("size_bytes")
        refreshed.append(
            normalize_audio_invalidation(
                item,
                operation_id=operation_id,
                operation=operation,
                default_reason=str(item.get("reason") or "audio dependency changed"),
            )
        )
    updated["schema_version"] = AUDIO_INVALIDATION_SCHEMA_VERSION
    updated["invalidated_chunks"] = refreshed
    updated["stale"] = bool(refreshed)
    updated["invalidation_fingerprint"] = fingerprint_value(refreshed)
    updated["undo_available"] = any(item["undo_available"] for item in refreshed)
    return updated
