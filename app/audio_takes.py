from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
import shutil
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from audio_crash_reconciliation import apply_audio_transition, audio_project_lock
from audio_artifacts import (
    AudioArtifactError,
    confined_audio_path,
    plan_verified_audio_install,
    sha256_file,
    validate_audio_file,
)
from approved_audio import active_approved_audio_lock, clear_approved_audio_fields
from generation_state import atomic_json_write, fingerprint_value


AUDIO_TAKE_SCHEMA_VERSION = 1
AUDIO_TAKE_REGISTRY_FILENAME = "audio_takes.json"
AUDIO_TAKE_HISTORY_DIRNAME = "audio_take_history"
AUDIO_TAKE_STORAGE_DIRNAME = "takes"
MAX_REFERENCE_JSON_BYTES = 8 * 1024 * 1024
MAX_FINAL_LISTEN_PAUSE_MS = 30_000
_REGISTRY_LOCKS_GUARD = threading.Lock()
_REGISTRY_LOCKS: dict[str, threading.RLock] = {}


def _reset_registry_locks_after_fork() -> None:
    global _REGISTRY_LOCKS_GUARD, _REGISTRY_LOCKS
    _REGISTRY_LOCKS_GUARD = threading.Lock()
    _REGISTRY_LOCKS = {}


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_registry_locks_after_fork)


class AudioTakeError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.context = copy.deepcopy(dict(context or {}))


def _registry_thread_lock(root_dir: str | Path) -> threading.RLock:
    key = str(Path(root_dir).expanduser().resolve())
    with _REGISTRY_LOCKS_GUARD:
        return _REGISTRY_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _registry_lock(root_dir: str | Path):
    with audio_project_lock(root_dir), _registry_thread_lock(root_dir):
        yield


@contextmanager
def audio_take_registry_lock(root_dir: str | Path):
    with _registry_lock(root_dir):
        yield


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _safe_token(value: Any, *, label: str) -> str:
    text = str(value or "").strip()
    if not text or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-:" for character in text):
        raise AudioTakeError(
            "audio_take_identifier_invalid",
            f"{label} is invalid.",
            context={"value": text},
        )
    return text


def chunk_key(chunk: Mapping[str, Any], index: int) -> str:
    raw = chunk.get("id", index)
    return f"chunk:{raw}"


def _chunk_storage_key(value: str) -> str:
    return _safe_token(value, label="Chunk key").replace(":", "_")


def registry_path(root_dir: str | Path) -> Path:
    return Path(root_dir).expanduser().resolve() / AUDIO_TAKE_REGISTRY_FILENAME


def history_root(root_dir: str | Path) -> Path:
    return Path(root_dir).expanduser().resolve() / AUDIO_TAKE_HISTORY_DIRNAME


def take_directory(root_dir: str | Path, chunk_key_value: str) -> Path:
    root = Path(root_dir).expanduser().resolve()
    return root / "voicelines" / AUDIO_TAKE_STORAGE_DIRNAME / _chunk_storage_key(chunk_key_value)


def new_take_id(*, kind: str = "raw") -> str:
    prefix = "take" if kind == "raw" else "rendition"
    return f"{prefix}_{time.time_ns()}_{secrets.token_hex(6)}"


def take_filename_base(take_id: str) -> str:
    return _safe_token(take_id, label="Take ID")


def empty_registry() -> dict[str, Any]:
    return {
        "schema_version": AUDIO_TAKE_SCHEMA_VERSION,
        "chunks": {},
        "takes": {},
        "updated_at_utc": None,
        "registry_fingerprint": None,
    }


def _registry_seed(registry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": AUDIO_TAKE_SCHEMA_VERSION,
        "chunks": copy.deepcopy(dict(registry.get("chunks") or {})),
        "takes": copy.deepcopy(dict(registry.get("takes") or {})),
        "updated_at_utc": registry.get("updated_at_utc"),
    }


def _with_registry_fingerprint(registry: Mapping[str, Any]) -> dict[str, Any]:
    seed = _registry_seed(registry)
    fingerprint_seed = {
        "schema_version": seed["schema_version"],
        "chunks": seed["chunks"],
        "takes": seed["takes"],
    }
    return {
        **seed,
        "registry_fingerprint": hashlib.sha256(
            _canonical_json(fingerprint_seed)
        ).hexdigest(),
    }


def _normalize_take(value: Mapping[str, Any]) -> dict[str, Any]:
    take_id = _safe_token(value.get("take_id"), label="Take ID")
    key = _safe_token(value.get("chunk_key"), label="Chunk key")
    kind = str(value.get("kind") or "raw")
    if kind not in {"raw", "rendition"}:
        raise AudioTakeError(
            "audio_take_kind_invalid",
            f"Take {take_id} has an unsupported kind.",
        )
    source_take_id = value.get("source_take_id")
    if source_take_id is not None:
        source_take_id = _safe_token(source_take_id, label="Source Take ID")
    artifact = value.get("artifact")
    if not isinstance(artifact, Mapping):
        raise AudioTakeError(
            "audio_take_artifact_invalid",
            f"Take {take_id} has no artifact record.",
        )
    relative_path = str(artifact.get("relative_path") or "").strip()
    sha256 = str(artifact.get("sha256") or "").strip().casefold()
    if not relative_path or len(sha256) != 64:
        raise AudioTakeError(
            "audio_take_artifact_invalid",
            f"Take {take_id} has incomplete artifact identity.",
        )
    normalized = {
        "schema_version": AUDIO_TAKE_SCHEMA_VERSION,
        "take_id": take_id,
        "chunk_key": key,
        "chunk_index_at_creation": int(value.get("chunk_index_at_creation", 0)),
        "kind": kind,
        "source_take_id": source_take_id,
        "root_take_id": _safe_token(
            value.get("root_take_id") or take_id,
            label="Root Take ID",
        ),
        "created_at_utc": str(value.get("created_at_utc") or _utc_now()),
        "current": bool(value.get("current")),
        "kept": bool(value.get("kept")),
        "legacy": bool(value.get("legacy")),
        "authored": copy.deepcopy(dict(value.get("authored") or {})),
        "voice": copy.deepcopy(dict(value.get("voice") or {})),
        "generation": copy.deepcopy(dict(value.get("generation") or {})),
        "synthesis": copy.deepcopy(dict(value.get("synthesis") or {})),
        "artifact": copy.deepcopy(dict(artifact)),
        "review": copy.deepcopy(dict(value.get("review") or {})),
        "processing": copy.deepcopy(dict(value.get("processing") or {})),
        "record_fingerprint": str(value.get("record_fingerprint") or ""),
    }
    recorded_fingerprint = normalized["record_fingerprint"]
    computed_fingerprint = _take_record_fingerprint(normalized)
    if recorded_fingerprint and recorded_fingerprint != computed_fingerprint:
        raise AudioTakeError(
            "audio_take_record_fingerprint_mismatch",
            f"Take {take_id} fingerprint does not match its contents.",
        )
    normalized["record_fingerprint"] = computed_fingerprint
    return normalized


def normalize_registry(value: Any) -> dict[str, Any]:
    if value is None:
        return _with_registry_fingerprint(empty_registry())
    if not isinstance(value, Mapping):
        raise AudioTakeError(
            "audio_take_registry_invalid",
            "Audio Take registry must be a JSON object.",
        )
    if value.get("schema_version") != AUDIO_TAKE_SCHEMA_VERSION:
        raise AudioTakeError(
            "audio_take_registry_schema_unsupported",
            "Audio Take registry schema is unsupported.",
        )
    raw_takes = value.get("takes")
    raw_chunks = value.get("chunks")
    if not isinstance(raw_takes, Mapping) or not isinstance(raw_chunks, Mapping):
        raise AudioTakeError(
            "audio_take_registry_invalid",
            "Audio Take registry is incomplete.",
        )
    takes: dict[str, dict[str, Any]] = {}
    for raw_id, item in raw_takes.items():
        if not isinstance(item, Mapping):
            raise AudioTakeError(
                "audio_take_registry_invalid",
                "Audio Take registry contains a malformed Take.",
            )
        normalized = _normalize_take(item)
        if normalized["take_id"] != raw_id:
            raise AudioTakeError(
                "audio_take_registry_invalid",
                "Audio Take registry key does not match its Take ID.",
            )
        takes[raw_id] = normalized
    chunks: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for raw_key, item in raw_chunks.items():
        key = _safe_token(raw_key, label="Chunk key")
        if not isinstance(item, Mapping):
            raise AudioTakeError(
                "audio_take_registry_invalid",
                f"Take registry entry for {key} is malformed.",
            )
        take_ids = [
            _safe_token(entry, label="Take ID")
            for entry in item.get("take_ids") or []
        ]
        if len(take_ids) != len(set(take_ids)):
            raise AudioTakeError(
                "audio_take_registry_invalid",
                f"Take registry entry for {key} contains duplicate IDs.",
            )
        if any(take_id not in takes for take_id in take_ids):
            raise AudioTakeError(
                "audio_take_registry_invalid",
                f"Take registry entry for {key} references a missing Take.",
            )
        if any(takes[take_id]["chunk_key"] != key for take_id in take_ids):
            raise AudioTakeError(
                "audio_take_registry_invalid",
                f"Take registry entry for {key} references another chunk.",
            )
        current_take_id = item.get("current_take_id")
        if current_take_id is not None:
            current_take_id = _safe_token(current_take_id, label="Current Take ID")
            if current_take_id not in take_ids:
                raise AudioTakeError(
                    "audio_take_registry_invalid",
                    f"Current Take for {key} is not in its ordered Take list.",
                )
        for take_id in take_ids:
            if take_id in seen:
                raise AudioTakeError(
                    "audio_take_registry_invalid",
                    f"Take {take_id} is assigned to more than one chunk.",
                )
            seen.add(take_id)
            takes[take_id]["current"] = take_id == current_take_id
        chunks[key] = {
            "chunk_key": key,
            "current_take_id": current_take_id,
            "take_ids": take_ids,
        }
    if set(takes) != seen:
        raise AudioTakeError(
            "audio_take_registry_invalid",
            "Audio Take registry contains unassigned Take records.",
        )
    normalized = _with_registry_fingerprint(
        {
            "schema_version": AUDIO_TAKE_SCHEMA_VERSION,
            "chunks": chunks,
            "takes": takes,
            "updated_at_utc": value.get("updated_at_utc"),
        }
    )
    recorded = str(value.get("registry_fingerprint") or "")
    if recorded and recorded != normalized["registry_fingerprint"]:
        raise AudioTakeError(
            "audio_take_registry_fingerprint_mismatch",
            "Audio Take registry fingerprint does not match its contents.",
        )
    return normalized


def load_registry(root_dir: str | Path) -> dict[str, Any]:
    path = registry_path(root_dir)
    if not path.exists():
        return normalize_registry(None)
    try:
        return normalize_registry(json.loads(path.read_text(encoding="utf-8")))
    except AudioTakeError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AudioTakeError(
            "audio_take_registry_unreadable",
            f"Audio Take registry could not be read: {exc}",
        ) from exc


def _write_registry(root_dir: str | Path, registry: Mapping[str, Any]) -> dict[str, Any]:
    value = _with_registry_fingerprint(
        {
            **_registry_seed(registry),
            "updated_at_utc": _utc_now(),
        }
    )
    root = Path(root_dir).expanduser().resolve()
    operation_id = f"take-registry-{fingerprint_value(value)[:24]}"
    apply_audio_transition(
        root,
        transition="take_registry",
        operation_id=operation_id,
        json_writes={AUDIO_TAKE_REGISTRY_FILENAME: value},
    )
    return value


def _legacy_take(
    *,
    root: Path,
    chunk: Mapping[str, Any],
    index: int,
    relative_path: str,
    current: bool,
) -> dict[str, Any] | None:
    try:
        path = confined_audio_path(root, relative_path)
    except AudioArtifactError:
        return None
    if not path.is_file():
        return None
    sha256 = str(chunk.get("audio_sha256") or "").strip().casefold()
    if not current or len(sha256) != 64 or sha256_file(path) != sha256:
        sha256 = sha256_file(path)
    stat = path.stat()
    recorded_size = chunk.get("audio_size_bytes")
    size_bytes = (
        int(recorded_size)
        if isinstance(recorded_size, int) and recorded_size > 0
        else int(stat.st_size)
    )
    recorded_duration = chunk.get("audio_duration_ms")
    duration_ms = (
        int(recorded_duration)
        if isinstance(recorded_duration, int) and recorded_duration > 0
        else None
    )
    recorded_format = str(chunk.get("audio_format") or "").strip().casefold()
    audio_format = recorded_format or path.suffix.casefold().lstrip(".")
    key = chunk_key(chunk, index)
    take_id = "take_legacy_" + fingerprint_value(
        {"chunk_key": key, "relative_path": relative_path, "sha256": sha256}
    )[:24]
    record = {
        "schema_version": AUDIO_TAKE_SCHEMA_VERSION,
        "take_id": take_id,
        "chunk_key": key,
        "chunk_index_at_creation": index,
        "kind": "raw",
        "source_take_id": None,
        "root_take_id": take_id,
        "created_at_utc": str(
            chunk.get("generated_at_utc")
            or datetime.fromtimestamp(
                path.stat().st_mtime,
                tz=timezone.utc,
            ).isoformat().replace("+00:00", "Z")
        ),
        "current": current,
        "kept": False,
        "legacy": True,
        "authored": {
            "text": str(chunk.get("text") or ""),
            "text_sha256": hashlib.sha256(
                str(chunk.get("text") or "").encode("utf-8")
            ).hexdigest(),
            "speaker": str(chunk.get("speaker") or ""),
            "direction": str(chunk.get("instruct") or ""),
        },
        "voice": {},
        "generation": {
            "provenance": copy.deepcopy(chunk.get("generation_provenance")),
            "audio_fingerprint": chunk.get("audio_fingerprint"),
        },
        "synthesis": {
            "seam_receipt": copy.deepcopy(chunk.get("synthesis_seam_receipt")),
        },
        "artifact": {
            "relative_path": relative_path,
            "sha256": sha256,
            "size_bytes": size_bytes,
            "duration_ms": duration_ms,
            "format": audio_format,
            "sample_rate": chunk.get("synthesis_sample_rate"),
            "sample_count": chunk.get("synthesis_final_sample_count"),
            "channels": None,
        },
        "review": {
            "state": chunk.get("listening_state") or "unreviewed",
        },
        "processing": {},
    }
    record["record_fingerprint"] = fingerprint_value(
        {key: value for key, value in record.items() if key != "record_fingerprint"}
    )
    return record


def registry_view(
    root_dir: str | Path,
    chunks: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    registry = load_registry(root)
    result = copy.deepcopy(registry)
    represented_paths = {
        str(take["artifact"].get("relative_path") or "")
        for take in result["takes"].values()
    }
    for index, chunk in enumerate(chunks):
        key = chunk_key(chunk, index)
        entry = result["chunks"].setdefault(
            key,
            {"chunk_key": key, "current_take_id": None, "take_ids": []},
        )
        candidates = (
            (str(chunk.get("audio_path") or ""), True),
            (str(chunk.get("stale_audio_path") or ""), False),
        )
        for relative_path, current in candidates:
            if not relative_path or relative_path in represented_paths:
                continue
            legacy = _legacy_take(
                root=root,
                chunk=chunk,
                index=index,
                relative_path=relative_path,
                current=current,
            )
            if legacy is None:
                continue
            result["takes"][legacy["take_id"]] = legacy
            entry["take_ids"].append(legacy["take_id"])
            represented_paths.add(relative_path)
            if current:
                entry["current_take_id"] = legacy["take_id"]
        entry["take_ids"].sort(
            key=lambda take_id: result["takes"][take_id]["created_at_utc"],
            reverse=True,
        )
        for take_id in entry["take_ids"]:
            result["takes"][take_id]["current"] = (
                take_id == entry["current_take_id"]
            )
    return _with_registry_fingerprint(result)


def prepare_invalidation_registry(
    root_dir: str | Path,
    chunks: Iterable[Mapping[str, Any]],
    *,
    invalidations: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return a non-mutating Take-registry update for dependency invalidation.

    Immutable Take bytes remain in place. The selected Take is cleared for each
    affected chunk, and the returned path map tells the invalidation transaction
    which canonical paths must not be moved into rollback storage.
    """
    chunk_values = [
        copy.deepcopy(dict(item))
        for item in chunks
        if isinstance(item, Mapping)
    ]
    # Only persisted Takes receive immutable-retention semantics during
    # invalidation. The read-only legacy overlay is intentionally excluded so
    # pre-registry canonical audio continues through the existing rollback
    # backup contract until an explicit Take mutation materializes it.
    registry = load_registry(root_dir)
    identifiers: set[str] = set()
    paths: set[str] = set()
    for raw in invalidations:
        if not isinstance(raw, Mapping):
            continue
        for field in ("old_chunk_id", "new_chunk_id", "chunk_id"):
            value = raw.get(field)
            if value is not None:
                identifiers.add(str(value))
        relative = str(
            raw.get("canonical_audio_path")
            or raw.get("audio_path")
            or ""
        ).strip()
        if relative:
            paths.add(relative)
    preserved_by_path: dict[str, str] = {}
    affected_take_ids: list[str] = []
    changed = False
    for index, chunk in enumerate(chunk_values):
        key = chunk_key(chunk, index)
        entry = registry["chunks"].get(key)
        if not isinstance(entry, dict):
            continue
        current_id = entry.get("current_take_id")
        current = registry["takes"].get(current_id)
        if not isinstance(current, dict):
            continue
        relative = str(
            (current.get("artifact") or {}).get("relative_path") or ""
        ).strip()
        raw_id = str(chunk.get("id", index))
        if raw_id not in identifiers and key not in identifiers and relative not in paths:
            continue
        entry["current_take_id"] = None
        _clear_final_listen_pin(current)
        current["current"] = False
        current["record_fingerprint"] = _take_record_fingerprint(current)
        if relative:
            preserved_by_path[relative] = str(current_id)
        affected_take_ids.append(str(current_id))
        changed = True
    if changed:
        registry["updated_at_utc"] = _utc_now()
        registry = _with_registry_fingerprint(registry)
    return {
        "changed": changed,
        "registry": registry,
        "registry_fingerprint": registry["registry_fingerprint"],
        "preserved_by_path": preserved_by_path,
        "affected_take_ids": sorted(set(affected_take_ids)),
    }


def materialize_registry(
    root_dir: str | Path,
    chunks: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    with _registry_lock(root_dir):
        view = registry_view(root_dir, chunks)
        return _write_registry(root_dir, view)


def _take_record_fingerprint(record: Mapping[str, Any]) -> str:
    return fingerprint_value(
        {key: copy.deepcopy(value) for key, value in record.items() if key != "record_fingerprint"}
    )


def _clear_final_listen_pin(take: dict[str, Any]) -> bool:
    review = copy.deepcopy(dict(take.get("review") or {}))
    changed = False
    pin_added_keep = review.get("final_listen_pin_added_keep") is True
    final_listen_operation = str(
        review.get("final_listen_operation") or ""
    )
    for field in (
        "final_listen_pinned",
        "final_listen_pinned_at_utc",
        "final_listen_source_order_fingerprint",
        "final_listen_pin_added_keep",
    ):
        if field in review:
            review.pop(field, None)
            changed = True
    if pin_added_keep and take.get("kept") is True:
        take["kept"] = False
        changed = True
    if final_listen_operation in {
        "final_listen_trim_edges",
        "final_listen_split_with_pause",
        "publication_mastering",
    }:
        review.update(
            {
                "state": "needs_listening",
                "review_required": True,
                "listening_required": True,
            }
        )
        changed = True
    if changed:
        take["review"] = review
        take["record_fingerprint"] = _take_record_fingerprint(take)
    return changed


def build_take_record(
    *,
    take_id: str,
    chunk_key_value: str,
    chunk_index: int,
    kind: str,
    source_take_id: str | None,
    root_take_id: str | None,
    artifact: Mapping[str, Any],
    authored: Mapping[str, Any],
    voice: Mapping[str, Any],
    generation: Mapping[str, Any],
    synthesis: Mapping[str, Any],
    review: Mapping[str, Any] | None = None,
    processing: Mapping[str, Any] | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    record = {
        "schema_version": AUDIO_TAKE_SCHEMA_VERSION,
        "take_id": take_id,
        "chunk_key": chunk_key_value,
        "chunk_index_at_creation": int(chunk_index),
        "kind": kind,
        "source_take_id": source_take_id,
        "root_take_id": root_take_id or take_id,
        "created_at_utc": created_at_utc or _utc_now(),
        "current": True,
        "kept": False,
        "legacy": False,
        "authored": copy.deepcopy(dict(authored)),
        "voice": copy.deepcopy(dict(voice)),
        "generation": copy.deepcopy(dict(generation)),
        "synthesis": copy.deepcopy(dict(synthesis)),
        "artifact": copy.deepcopy(dict(artifact)),
        "review": copy.deepcopy(dict(review or {"state": "unreviewed"})),
        "processing": copy.deepcopy(dict(processing or {})),
    }
    record["record_fingerprint"] = _take_record_fingerprint(record)
    return _normalize_take(record)


def plan_take_registration(
    root_dir: str | Path,
    *,
    chunks: list[Mapping[str, Any]],
    record: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    with _registry_lock(root_dir):
        registry = registry_view(root_dir, chunks)
        take = _normalize_take(record)
        if take["take_id"] in registry["takes"]:
            raise AudioTakeError(
                "audio_take_exists",
                f"Take {take['take_id']} already exists.",
            )
        path = confined_audio_path(root_dir, take["artifact"]["relative_path"])
        if not path.is_file() or sha256_file(path) != take["artifact"]["sha256"]:
            raise AudioTakeError(
                "audio_take_artifact_mismatch",
                "Take audio does not match its immutable artifact record.",
            )
        key = take["chunk_key"]
        entry = registry["chunks"].setdefault(
            key,
            {"chunk_key": key, "current_take_id": None, "take_ids": []},
        )
        if take["kind"] == "rendition":
            source = registry["takes"].get(take["source_take_id"])
            if source is None or source["chunk_key"] != key:
                raise AudioTakeError(
                    "audio_take_source_missing",
                    "Child rendition source Take is missing or belongs to another chunk.",
                )
            take["root_take_id"] = source["root_take_id"]
            take["record_fingerprint"] = _take_record_fingerprint(take)
        for take_id in entry["take_ids"]:
            _clear_final_listen_pin(registry["takes"][take_id])
            registry["takes"][take_id]["current"] = False
            registry["takes"][take_id]["record_fingerprint"] = _take_record_fingerprint(
                registry["takes"][take_id]
            )
        take["current"] = True
        take["record_fingerprint"] = _take_record_fingerprint(take)
        registry["takes"][take["take_id"]] = take
        entry["take_ids"].append(take["take_id"])
        entry["take_ids"].sort(
            key=lambda value: registry["takes"][value]["created_at_utc"],
            reverse=True,
        )
        entry["current_take_id"] = take["take_id"]
        written = _with_registry_fingerprint(
            {**_registry_seed(registry), "updated_at_utc": _utc_now()}
        )
        return copy.deepcopy(written["takes"][take["take_id"]]), written


def register_take(
    root_dir: str | Path,
    *,
    chunks: list[Mapping[str, Any]],
    record: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    with _registry_lock(root_dir):
        take, registry = plan_take_registration(
            root_dir,
            chunks=chunks,
            record=record,
        )
        apply_audio_transition(
            root_dir,
            transition="take_registry",
            operation_id=f"take-registry-{fingerprint_value(registry)[:24]}",
            json_writes={AUDIO_TAKE_REGISTRY_FILENAME: registry},
        )
        return take, registry


def take_chunk_audio_fields(take: Mapping[str, Any]) -> dict[str, Any]:
    artifact = dict(take.get("artifact") or {})
    generation = dict(take.get("generation") or {})
    synthesis = dict(take.get("synthesis") or {})
    fields = copy.deepcopy(dict(generation.get("chunk_audio_fields") or {}))
    fields.update(
        {
            "audio_path": artifact.get("relative_path"),
            "audio_state": "current",
            "audio_sha256": artifact.get("sha256"),
            "audio_size_bytes": artifact.get("size_bytes"),
            "audio_duration_ms": artifact.get("duration_ms"),
            "audio_format": artifact.get("format"),
            "audio_fingerprint": generation.get("audio_fingerprint"),
            "stale_audio_path": None,
            "current_take_id": take.get("take_id"),
            "synthesis_seam_receipt": copy.deepcopy(
                synthesis.get("seam_receipt")
            ),
        }
    )
    return fields


def _validated_promotion_chunk_fields(
    chunk: Mapping[str, Any],
    fields: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if fields is None:
        return {}
    if not isinstance(fields, Mapping):
        raise AudioTakeError(
            "audio_take_promotion_fields_invalid",
            "Take promotion metadata is invalid.",
        )
    if not fields:
        return {}
    allowed = {"approved_audio_lock", "approved_audio_origin"}
    if set(fields) != allowed:
        raise AudioTakeError(
            "audio_take_promotion_fields_invalid",
            "Take promotion metadata contains unsupported fields.",
        )
    lock = fields.get("approved_audio_lock")
    origin = fields.get("approved_audio_origin")
    if not isinstance(lock, Mapping) or not isinstance(origin, Mapping):
        raise AudioTakeError(
            "audio_take_promotion_fields_invalid",
            "Approved Take promotion metadata is incomplete.",
        )
    candidate = copy.deepcopy(dict(chunk))
    clear_approved_audio_fields(candidate)
    candidate.update(
        {
            "approved_audio_lock": copy.deepcopy(dict(lock)),
            "approved_audio_origin": copy.deepcopy(dict(origin)),
        }
    )
    if (
        type(lock.get("schema_version")) is not int
        or active_approved_audio_lock(candidate) != dict(lock)
    ):
        raise AudioTakeError(
            "audio_take_promotion_fields_invalid",
            "Approved Take lock metadata is invalid.",
        )
    origin_text_fields = (
        "promotion_id",
        "manifest_path",
        "candidate_id",
        "direct_placement_tier",
        "source_audio_path",
        "source_audio_sha256",
        "installed_at_utc",
    )
    identity_fields = (
        "promotion_id",
        "candidate_id",
        "source_round_id",
        "direct_placement_tier",
        "source_audio_sha256",
        "installed_at_utc",
    )
    if not (
        type(origin.get("schema_version")) is int
        and origin["schema_version"] == 1
        and all(
            type(origin.get(field)) is str and bool(origin[field].strip())
            for field in origin_text_fields
        )
        and "source_round_id" in origin
        and (
            origin["source_round_id"] is None
            or (
                type(origin["source_round_id"]) is str
                and bool(origin["source_round_id"].strip())
            )
        )
        and type(origin.get("reference_bank_eligible")) is bool
        and all(origin.get(field) == lock.get(field) for field in identity_fields)
    ):
        raise AudioTakeError(
            "audio_take_promotion_fields_invalid",
            "Approved Take origin metadata is invalid.",
        )
    return {
        "approved_audio_lock": copy.deepcopy(dict(lock)),
        "approved_audio_origin": copy.deepcopy(dict(origin)),
    }


def public_take(
    take: Mapping[str, Any],
    *,
    registry_fingerprint: str,
) -> dict[str, Any]:
    artifact = dict(take.get("artifact") or {})
    relative = str(artifact.get("relative_path") or "")
    audio_url = (
        "/" + relative.replace("\\", "/").lstrip("/")
        if relative.startswith("voicelines/")
        else None
    )
    review = copy.deepcopy(dict(take.get("review") or {}))
    return {
        "take_id": take.get("take_id"),
        "chunk_key": take.get("chunk_key"),
        "kind": take.get("kind"),
        "source_take_id": take.get("source_take_id"),
        "root_take_id": take.get("root_take_id"),
        "created_at_utc": take.get("created_at_utc"),
        "current": bool(take.get("current")),
        "kept": bool(take.get("kept")),
        "legacy": bool(take.get("legacy")),
        "record_fingerprint": take.get("record_fingerprint"),
        "registry_fingerprint": registry_fingerprint,
        "audio": {
            "available": bool(audio_url),
            "url": audio_url,
            "relative_path": relative or None,
            "sha256": artifact.get("sha256"),
            "size_bytes": artifact.get("size_bytes"),
            "duration_ms": artifact.get("duration_ms"),
            "format": artifact.get("format"),
            "sample_rate": artifact.get("sample_rate"),
            "sample_count": artifact.get("sample_count"),
            "channels": artifact.get("channels"),
        },
        "authored": copy.deepcopy(dict(take.get("authored") or {})),
        "voice": copy.deepcopy(dict(take.get("voice") or {})),
        "generation": copy.deepcopy(dict(take.get("generation") or {})),
        "synthesis": copy.deepcopy(dict(take.get("synthesis") or {})),
        "review": review,
        "final_listen_pinned": review.get("final_listen_pinned") is True,
        "processing": copy.deepcopy(dict(take.get("processing") or {})),
    }


def public_chunk_takes(
    root_dir: str | Path,
    chunks: list[Mapping[str, Any]],
    *,
    index: int,
) -> dict[str, Any]:
    if not 0 <= index < len(chunks):
        raise AudioTakeError(
            "audio_take_chunk_missing",
            "The requested chunk does not exist.",
        )
    registry = registry_view(root_dir, chunks)
    key = chunk_key(chunks[index], index)
    entry = registry["chunks"].get(
        key,
        {"chunk_key": key, "current_take_id": None, "take_ids": []},
    )
    values = [
        public_take(
            registry["takes"][take_id],
            registry_fingerprint=registry["registry_fingerprint"],
        )
        for take_id in entry["take_ids"]
    ]
    pinned_take_id = next(
        (
            take_id
            for take_id in entry["take_ids"]
            if (
                registry["takes"][take_id].get("review") or {}
            ).get("final_listen_pinned")
            is True
        ),
        None,
    )
    return {
        "schema_version": AUDIO_TAKE_SCHEMA_VERSION,
        "chunk_key": key,
        "current_take_id": entry.get("current_take_id"),
        "pinned_take_id": pinned_take_id,
        "takes": values,
        "take_count": len(values),
        "registry_fingerprint": registry["registry_fingerprint"],
    }


def _require_registry_fingerprint(
    registry: Mapping[str, Any],
    expected: str,
) -> None:
    if registry.get("registry_fingerprint") != expected:
        raise AudioTakeError(
            "audio_take_registry_changed",
            "Audio Takes changed after this action was reviewed.",
            context={
                "current_registry_fingerprint": registry.get(
                    "registry_fingerprint"
                )
            },
        )


def _require_take(
    registry: Mapping[str, Any],
    *,
    chunk_key_value: str,
    take_id: str,
    expected_record_fingerprint: str | None = None,
) -> dict[str, Any]:
    take = (registry.get("takes") or {}).get(take_id)
    if not isinstance(take, Mapping) or take.get("chunk_key") != chunk_key_value:
        raise AudioTakeError(
            "audio_take_missing",
            "The requested Take no longer exists for this chunk.",
        )
    if (
        expected_record_fingerprint is not None
        and take.get("record_fingerprint") != expected_record_fingerprint
    ):
        raise AudioTakeError(
            "audio_take_changed",
            "The requested Take changed after this action was reviewed.",
        )
    return copy.deepcopy(dict(take))


def set_take_kept(
    root_dir: str | Path,
    *,
    chunks: list[Mapping[str, Any]],
    chunk_key_value: str,
    take_id: str,
    kept: bool,
    expected_registry_fingerprint: str,
    expected_record_fingerprint: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    with _registry_lock(root_dir):
        registry = materialize_registry(root_dir, chunks)
        _require_registry_fingerprint(registry, expected_registry_fingerprint)
        take = _require_take(
            registry,
            chunk_key_value=chunk_key_value,
            take_id=take_id,
            expected_record_fingerprint=expected_record_fingerprint,
        )
        take["kept"] = bool(kept)
        take["record_fingerprint"] = _take_record_fingerprint(take)
        registry["takes"][take_id] = take
        written = _write_registry(root_dir, registry)
        return copy.deepcopy(written["takes"][take_id]), written


def set_final_listen_pin(
    root_dir: str | Path,
    *,
    chunks: list[Mapping[str, Any]],
    chunks_path: str | Path,
    index: int,
    take_id: str,
    pinned: bool,
    expected_registry_fingerprint: str,
    expected_record_fingerprint: str,
    source_order_fingerprint: str,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    target_chunks_path = Path(chunks_path).expanduser().resolve()
    try:
        relative_chunks_path = target_chunks_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise AudioTakeError(
            "audio_take_chunks_path_unsafe",
            "Chunk metadata path escaped the project root.",
        ) from exc
    if not 0 <= int(index) < len(chunks):
        raise AudioTakeError(
            "audio_take_chunk_missing",
            "The requested chunk no longer exists.",
        )
    index = int(index)
    with _registry_lock(root):
        registry = registry_view(root, chunks)
        _require_registry_fingerprint(registry, expected_registry_fingerprint)
        key = chunk_key(chunks[index], index)
        take = _require_take(
            registry,
            chunk_key_value=key,
            take_id=str(take_id),
            expected_record_fingerprint=str(expected_record_fingerprint),
        )
        entry = registry["chunks"].get(key) or {}
        if entry.get("current_take_id") != take["take_id"]:
            raise AudioTakeError(
                "audio_take_final_listen_not_current",
                "Only the current Take can be pinned for Final Listen.",
            )
        audio_path = confined_audio_path(
            root,
            str((take.get("artifact") or {}).get("relative_path") or ""),
        )
        if (
            not audio_path.is_file()
            or sha256_file(audio_path) != take["artifact"]["sha256"]
        ):
            raise AudioTakeError(
                "audio_take_artifact_mismatch",
                "Current Take audio is missing or changed and cannot be pinned.",
            )
        existing_pinned = [
            value
            for value in entry.get("take_ids") or []
            if (
                registry["takes"][value].get("review") or {}
            ).get("final_listen_pinned")
            is True
        ]
        current_review = dict(take.get("review") or {})
        if (
            pinned
            and existing_pinned == [take["take_id"]]
            and current_review.get(
                "final_listen_source_order_fingerprint"
            )
            == str(source_order_fingerprint)
        ) or (not pinned and not existing_pinned):
            return {
                "status": "current",
                "operation_id": None,
                "take": public_take(
                    take,
                    registry_fingerprint=registry["registry_fingerprint"],
                ),
                "registry_fingerprint": registry["registry_fingerprint"],
                "chunk": copy.deepcopy(chunks[index]),
            }
        before_registry = copy.deepcopy(registry)
        before_chunks = copy.deepcopy(chunks)
        for value in entry.get("take_ids") or []:
            _clear_final_listen_pin(registry["takes"][value])
        selected = registry["takes"][take["take_id"]]
        if pinned:
            review = copy.deepcopy(dict(selected.get("review") or {}))
            pin_added_keep = selected.get("kept") is not True
            review.update(
                {
                    "state": "approved",
                    "review_required": False,
                    "listening_required": False,
                    "final_listen_pinned": True,
                    "final_listen_pinned_at_utc": _utc_now(),
                    "final_listen_source_order_fingerprint": str(
                        source_order_fingerprint
                    ),
                    "final_listen_pin_added_keep": pin_added_keep,
                }
            )
            selected["review"] = review
            selected["kept"] = True
            selected["record_fingerprint"] = _take_record_fingerprint(selected)
        semantic = _with_registry_fingerprint(registry)
        updated_chunks = copy.deepcopy(chunks)
        updated_chunks[index]["take_record_fingerprint"] = semantic["takes"][
            take["take_id"]
        ]["record_fingerprint"]
        updated_chunks[index]["take_registry_fingerprint"] = semantic[
            "registry_fingerprint"
        ]
        if (
            semantic["registry_fingerprint"]
            == before_registry["registry_fingerprint"]
            and updated_chunks == before_chunks
        ):
            return {
                "status": "current",
                "operation_id": None,
                "take": public_take(
                    semantic["takes"][take["take_id"]],
                    registry_fingerprint=semantic["registry_fingerprint"],
                ),
                "registry_fingerprint": semantic["registry_fingerprint"],
                "chunk": copy.deepcopy(updated_chunks[index]),
            }
        written = _with_registry_fingerprint(
            {**_registry_seed(registry), "updated_at_utc": _utc_now()}
        )
        updated_chunks[index]["take_registry_fingerprint"] = written[
            "registry_fingerprint"
        ]
        operation_id = _operation_id(
            "audio_take_final_listen_pin",
            {
                "chunk_key": key,
                "take_id": take["take_id"],
                "pinned": bool(pinned),
                "registry_fingerprint": before_registry[
                    "registry_fingerprint"
                ],
            },
        )
        operation_dir = _history_operation_dir(root, operation_id)
        receipt = {
            "schema_version": 1,
            "operation": "final_listen_pin",
            "operation_id": operation_id,
            "created_at_utc": _utc_now(),
            "status": "applied",
            "before_registry": before_registry,
            "before_chunks": before_chunks,
            "after_registry_fingerprint": written["registry_fingerprint"],
            "after_chunks_fingerprint": fingerprint_value(updated_chunks),
            "backups": [],
            "chunk_key": key,
            "take_id": take["take_id"],
            "pinned": bool(pinned),
        }
        apply_audio_transition(
            root,
            transition="current_take_selection",
            operation_id=operation_id,
            json_writes={
                AUDIO_TAKE_REGISTRY_FILENAME: written,
                relative_chunks_path: updated_chunks,
                (operation_dir / "receipt.json").relative_to(root).as_posix(): receipt,
            },
            required_artifacts={
                str(selected["artifact"]["relative_path"]): str(
                    selected["artifact"]["sha256"]
                )
            },
        )
        return {
            "status": "pinned" if pinned else "unpinned",
            "operation_id": operation_id,
            "take": public_take(
                written["takes"][take["take_id"]],
                registry_fingerprint=written["registry_fingerprint"],
            ),
            "registry_fingerprint": written["registry_fingerprint"],
            "chunk": copy.deepcopy(updated_chunks[index]),
        }


def set_final_listen_pause(
    root_dir: str | Path,
    *,
    chunks: list[Mapping[str, Any]],
    chunks_path: str | Path,
    index: int,
    take_id: str,
    pause_after_ms: int | None,
    expected_registry_fingerprint: str,
    expected_record_fingerprint: str,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    target_chunks_path = Path(chunks_path).expanduser().resolve()
    try:
        relative_chunks_path = target_chunks_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise AudioTakeError(
            "audio_take_chunks_path_unsafe",
            "Chunk metadata path escaped the project root.",
        ) from exc
    if not 0 <= int(index) < len(chunks):
        raise AudioTakeError(
            "audio_take_chunk_missing",
            "The requested chunk no longer exists.",
        )
    index = int(index)
    if pause_after_ms is not None:
        if (
            isinstance(pause_after_ms, bool)
            or not isinstance(pause_after_ms, int)
            or not 0 <= pause_after_ms <= MAX_FINAL_LISTEN_PAUSE_MS
        ):
            raise AudioTakeError(
                "audio_take_final_listen_pause_invalid",
                f"Final Listen pause must be between 0 and {MAX_FINAL_LISTEN_PAUSE_MS} milliseconds.",
            )
    with _registry_lock(root):
        registry = registry_view(root, chunks)
        _require_registry_fingerprint(registry, expected_registry_fingerprint)
        key = chunk_key(chunks[index], index)
        take = _require_take(
            registry,
            chunk_key_value=key,
            take_id=str(take_id),
            expected_record_fingerprint=str(expected_record_fingerprint),
        )
        if (registry["chunks"].get(key) or {}).get("current_take_id") != take[
            "take_id"
        ]:
            raise AudioTakeError(
                "audio_take_final_listen_not_current",
                "Pause can be adjusted only for the current Take.",
            )
        audio_path = confined_audio_path(
            root,
            str((take.get("artifact") or {}).get("relative_path") or ""),
        )
        if (
            not audio_path.is_file()
            or sha256_file(audio_path) != take["artifact"]["sha256"]
        ):
            raise AudioTakeError(
                "audio_take_artifact_mismatch",
                "Current Take audio is missing or changed and its pause cannot be adjusted.",
            )
        before_chunks = copy.deepcopy(chunks)
        updated_chunks = copy.deepcopy(chunks)
        if pause_after_ms is None:
            updated_chunks[index].pop("pause_after", None)
        else:
            updated_chunks[index]["pause_after"] = int(pause_after_ms)
        persisted_registry = _with_registry_fingerprint(
            {**_registry_seed(registry), "updated_at_utc": _utc_now()}
        )
        if updated_chunks == before_chunks:
            return {
                "status": "current",
                "operation_id": None,
                "registry_fingerprint": persisted_registry[
                    "registry_fingerprint"
                ],
                "chunk": copy.deepcopy(updated_chunks[index]),
            }
        operation_id = _operation_id(
            "audio_take_final_listen_pause",
            {
                "chunk_key": key,
                "take_id": take["take_id"],
                "pause_after_ms": pause_after_ms,
                "registry_fingerprint": registry["registry_fingerprint"],
            },
        )
        operation_dir = _history_operation_dir(root, operation_id)
        receipt = {
            "schema_version": 1,
            "operation": "final_listen_pause",
            "operation_id": operation_id,
            "created_at_utc": _utc_now(),
            "status": "applied",
            "before_registry": copy.deepcopy(registry),
            "before_chunks": before_chunks,
            "after_registry_fingerprint": persisted_registry[
                "registry_fingerprint"
            ],
            "after_chunks_fingerprint": fingerprint_value(updated_chunks),
            "backups": [],
            "chunk_key": key,
            "take_id": take["take_id"],
            "pause_after_ms": pause_after_ms,
        }
        apply_audio_transition(
            root,
            transition="chunks_metadata",
            operation_id=operation_id,
            json_writes={
                AUDIO_TAKE_REGISTRY_FILENAME: persisted_registry,
                relative_chunks_path: updated_chunks,
                (operation_dir / "receipt.json").relative_to(root).as_posix(): receipt,
            },
            required_artifacts={
                str(take["artifact"]["relative_path"]): str(
                    take["artifact"]["sha256"]
                )
            },
        )
        return {
            "status": "updated",
            "operation_id": operation_id,
            "registry_fingerprint": persisted_registry[
                "registry_fingerprint"
            ],
            "chunk": copy.deepcopy(updated_chunks[index]),
        }


def promote_take(
    root_dir: str | Path,
    *,
    chunks: list[Mapping[str, Any]],
    chunks_path: str | Path,
    index: int,
    take_id: str,
    expected_registry_fingerprint: str,
    expected_record_fingerprint: str,
    expected_audio_fingerprint: str,
    promotion_chunk_fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    target_chunks_path = Path(chunks_path).expanduser().resolve()
    try:
        target_chunks_path.relative_to(root)
    except ValueError as exc:
        raise AudioTakeError(
            "audio_take_chunks_path_unsafe",
            "Chunk metadata path escaped the project root.",
        ) from exc
    if not 0 <= index < len(chunks):
        raise AudioTakeError(
            "audio_take_chunk_missing",
            "The requested chunk no longer exists.",
        )
    with _registry_lock(root):
        registry = registry_view(root, chunks)
        _require_registry_fingerprint(registry, expected_registry_fingerprint)
        key = chunk_key(chunks[index], index)
        take = _require_take(
            registry,
            chunk_key_value=key,
            take_id=take_id,
            expected_record_fingerprint=expected_record_fingerprint,
        )
        recorded_binding = str(
            (take.get("generation") or {}).get("audio_fingerprint") or ""
        )
        if recorded_binding != expected_audio_fingerprint:
            raise AudioTakeError(
                "audio_take_dependency_mismatch",
                "This Take belongs to an older text, Voice, pronunciation, or synthesis dependency and cannot become current.",
                context={
                    "recorded_audio_fingerprint": recorded_binding or None,
                    "current_audio_fingerprint": expected_audio_fingerprint,
                },
            )
        audio_path = confined_audio_path(
            root,
            str(take["artifact"].get("relative_path") or ""),
        )
        if (
            not audio_path.is_file()
            or sha256_file(audio_path) != take["artifact"]["sha256"]
        ):
            raise AudioTakeError(
                "audio_take_artifact_mismatch",
                "Take audio is missing or changed and cannot become current.",
            )
        before_registry = copy.deepcopy(registry)
        before_chunks = copy.deepcopy(chunks)
        validated_promotion_fields = _validated_promotion_chunk_fields(
            chunks[index],
            promotion_chunk_fields,
        )
        entry = registry["chunks"][key]
        for value in entry["take_ids"]:
            _clear_final_listen_pin(registry["takes"][value])
            registry["takes"][value]["current"] = value == take_id
            registry["takes"][value]["record_fingerprint"] = (
                _take_record_fingerprint(registry["takes"][value])
            )
        entry["current_take_id"] = take_id
        selected = registry["takes"][take_id]
        semantic_registry = _with_registry_fingerprint(registry)
        updated_chunks = copy.deepcopy(chunks)
        updated_chunk = updated_chunks[index]
        clear_approved_audio_fields(updated_chunk)
        selected_audio_fields = take_chunk_audio_fields(selected)
        selected_audio_fields.pop("approved_audio_lock", None)
        selected_audio_fields.pop("approved_audio_origin", None)
        updated_chunk.update(
            {
                "status": "done",
                "error": None,
                "error_code": None,
                **selected_audio_fields,
                **validated_promotion_fields,
                "take_record_fingerprint": selected["record_fingerprint"],
                "take_registry_fingerprint": semantic_registry[
                    "registry_fingerprint"
                ],
            }
        )
        if (
            semantic_registry["registry_fingerprint"]
            == before_registry["registry_fingerprint"]
            and updated_chunk == before_chunks[index]
        ):
            return {
                "status": "current",
                "operation_id": None,
                "take": public_take(
                    semantic_registry["takes"][take_id],
                    registry_fingerprint=semantic_registry[
                        "registry_fingerprint"
                    ],
                ),
                "registry_fingerprint": semantic_registry[
                    "registry_fingerprint"
                ],
                "chunk": copy.deepcopy(updated_chunk),
            }
        operation_id = _operation_id(
            "audio_take_promote",
            {
                "chunk_key": key,
                "take_id": take_id,
                "registry_fingerprint": registry["registry_fingerprint"],
            },
        )
        operation_dir = _history_operation_dir(root, operation_id)
        if operation_dir.exists():
            raise AudioTakeError(
                "audio_take_operation_conflict",
                "Take operation history already exists.",
            )
        try:
            written = _with_registry_fingerprint(
                {
                    **_registry_seed(registry),
                    "updated_at_utc": _utc_now(),
                }
            )
            updated_chunks[index]["take_registry_fingerprint"] = written[
                "registry_fingerprint"
            ]
            after_chunks_fingerprint = fingerprint_value(updated_chunks)
            receipt = {
                "schema_version": 1,
                "operation": "promote_take",
                "operation_id": operation_id,
                "created_at_utc": _utc_now(),
                "status": "applied",
                "before_registry": before_registry,
                "before_chunks": before_chunks,
                "after_registry_fingerprint": written[
                    "registry_fingerprint"
                ],
                "after_chunks_fingerprint": after_chunks_fingerprint,
                "backups": [],
                "chunk_key": key,
                "take_id": take_id,
            }
            apply_audio_transition(
                root,
                transition="current_take_selection",
                operation_id=operation_id,
                json_writes={
                    AUDIO_TAKE_REGISTRY_FILENAME: written,
                    target_chunks_path.resolve().relative_to(root).as_posix(): updated_chunks,
                    (operation_dir / "receipt.json").relative_to(root).as_posix(): receipt,
                },
                required_artifacts={
                    str(selected["artifact"]["relative_path"]): str(selected["artifact"]["sha256"])
                },
            )
        except Exception:
            _write_registry(root, before_registry)
            atomic_json_write(before_chunks, target_chunks_path)
            shutil.rmtree(operation_dir, ignore_errors=True)
            raise
        return {
            "status": "promoted",
            "operation_id": operation_id,
            "take": public_take(
                written["takes"][take_id],
                registry_fingerprint=written["registry_fingerprint"],
            ),
            "registry_fingerprint": written["registry_fingerprint"],
            "chunk": copy.deepcopy(updated_chunks[index]),
        }


def register_rendition(
    root_dir: str | Path,
    *,
    chunks: list[Mapping[str, Any]],
    chunks_path: str | Path,
    index: int,
    source_take_id: str,
    source_audio_path: str | Path,
    expected_source_sha256: str,
    expected_registry_fingerprint: str,
    expected_source_record_fingerprint: str,
    expected_audio_fingerprint: str,
    processing: Mapping[str, Any],
    review: Mapping[str, Any] | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    target_chunks_path = Path(chunks_path).expanduser().resolve()
    try:
        target_chunks_path.relative_to(root)
    except ValueError as exc:
        raise AudioTakeError(
            "audio_take_chunks_path_unsafe",
            "Chunk metadata path escaped the project root.",
        ) from exc
    if not 0 <= int(index) < len(chunks):
        raise AudioTakeError(
            "audio_take_chunk_missing",
            "The requested chunk no longer exists.",
        )
    index = int(index)
    created_at = created_at_utc or _utc_now()
    with _registry_lock(root):
        registry = registry_view(root, chunks)
        _require_registry_fingerprint(
            registry,
            str(expected_registry_fingerprint),
        )
        key = chunk_key(chunks[index], index)
        source = _require_take(
            registry,
            chunk_key_value=key,
            take_id=str(source_take_id),
            expected_record_fingerprint=str(
                expected_source_record_fingerprint
            ),
        )
        source_binding = str(
            (source.get("generation") or {}).get("audio_fingerprint") or ""
        )
        if source_binding != str(expected_audio_fingerprint):
            raise AudioTakeError(
                "audio_take_dependency_mismatch",
                "The source Take belongs to an older text, Voice, pronunciation, or synthesis dependency.",
                context={
                    "recorded_audio_fingerprint": source_binding or None,
                    "current_audio_fingerprint": expected_audio_fingerprint,
                },
            )
        if len(source_binding) != 64:
            raise AudioTakeError(
                "audio_take_binding_missing",
                "The source Take has no complete production binding.",
            )
        operation_id = _operation_id(
            "audio_take_rendition",
            {
                "chunk_key": key,
                "source_take_id": source_take_id,
                "registry_fingerprint": registry[
                    "registry_fingerprint"
                ],
                "processing": copy.deepcopy(dict(processing)),
            },
        )
        operation_dir = _history_operation_dir(root, operation_id)
        before_registry = copy.deepcopy(registry)
        before_chunks = copy.deepcopy(chunks)
        take_id = new_take_id(kind="rendition")
        try:
            install_plan = plan_verified_audio_install(
                root_dir=root,
                voicelines_dir=take_directory(root, key),
                source_audio_path=source_audio_path,
                filename_base=take_filename_base(take_id),
                binding_fingerprint=source_binding,
                expected_sha256=str(expected_source_sha256),
                text=str(chunks[index].get("text") or ""),
            )
            artifact = install_plan["artifact"]
            generation = copy.deepcopy(source.get("generation") or {})
            stored_fields = copy.deepcopy(
                generation.get("chunk_audio_fields") or {}
            )
            stored_fields.update(
                {
                    **artifact,
                    "generated_at_utc": created_at,
                }
            )
            generation.update(
                {
                    "parent_take_id": source["take_id"],
                    "chunk_audio_fields": stored_fields,
                }
            )
            record = build_take_record(
                take_id=take_id,
                chunk_key_value=key,
                chunk_index=index,
                kind="rendition",
                source_take_id=source["take_id"],
                root_take_id=source["root_take_id"],
                artifact={
                    "relative_path": artifact["audio_path"],
                    "sha256": artifact["audio_sha256"],
                    "size_bytes": artifact["audio_size_bytes"],
                    "duration_ms": artifact["audio_duration_ms"],
                    "format": artifact["audio_format"],
                    "sample_rate": artifact.get("audio_sample_rate"),
                    "sample_count": artifact.get("audio_sample_count"),
                    "channels": artifact.get("audio_channels"),
                    "installed_sample_width": artifact.get(
                        "audio_sample_width"
                    ),
                },
                authored=copy.deepcopy(source.get("authored") or {}),
                voice=copy.deepcopy(source.get("voice") or {}),
                generation=generation,
                synthesis=copy.deepcopy(source.get("synthesis") or {}),
                review=(
                    copy.deepcopy(dict(review))
                    if isinstance(review, Mapping)
                    else {
                        "state": "approved",
                        "review_required": False,
                        "listening_required": False,
                    }
                ),
                processing=copy.deepcopy(dict(processing)),
                created_at_utc=created_at,
            )
            normalized = _normalize_take(record)
            entry = registry["chunks"][key]
            for value in entry["take_ids"]:
                _clear_final_listen_pin(registry["takes"][value])
                registry["takes"][value]["current"] = False
                registry["takes"][value]["record_fingerprint"] = (
                    _take_record_fingerprint(registry["takes"][value])
                )
            registry["takes"][take_id] = normalized
            entry["take_ids"].append(take_id)
            entry["take_ids"].sort(
                key=lambda value: registry["takes"][value][
                    "created_at_utc"
                ],
                reverse=True,
            )
            entry["current_take_id"] = take_id
            updated_chunks = copy.deepcopy(chunks)
            updated_chunks[index].update(
                {
                    "status": "done",
                    "error": None,
                    "error_code": None,
                    **take_chunk_audio_fields(normalized),
                    "take_record_fingerprint": normalized[
                        "record_fingerprint"
                    ],
                }
            )
            written = _with_registry_fingerprint(
                {**_registry_seed(registry), "updated_at_utc": _utc_now()}
            )
            updated_chunks[index]["take_registry_fingerprint"] = written[
                "registry_fingerprint"
            ]
            receipt = {
                "schema_version": 1,
                "operation": "create_rendition",
                "operation_id": operation_id,
                "created_at_utc": created_at,
                "status": "applied",
                "before_registry": before_registry,
                "before_chunks": before_chunks,
                "after_registry_fingerprint": written[
                    "registry_fingerprint"
                ],
                "after_chunks_fingerprint": fingerprint_value(
                    updated_chunks
                ),
                "backups": [],
                "chunk_key": key,
                "take_id": take_id,
                "source_take_id": source["take_id"],
                "created_audio_path": artifact["audio_path"],
                "created_audio_sha256": artifact["audio_sha256"],
            }
            apply_audio_transition(
                root,
                transition="immutable_take_installation",
                operation_id=operation_id,
                binary_writes={
                    artifact["audio_path"]: install_plan["content"],
                },
                deletes=[install_plan["obsolete_relative_path"]],
                json_writes={
                    AUDIO_TAKE_REGISTRY_FILENAME: written,
                    target_chunks_path.relative_to(root).as_posix(): updated_chunks,
                    (operation_dir / "receipt.json").relative_to(root).as_posix(): receipt,
                },
                required_artifacts={
                    artifact["audio_path"]: artifact["audio_sha256"],
                },
            )
            return {
                "status": "created",
                "operation_id": operation_id,
                "take": public_take(
                    written["takes"][take_id],
                    registry_fingerprint=written[
                        "registry_fingerprint"
                    ],
                ),
                "registry_fingerprint": written[
                    "registry_fingerprint"
                ],
                "chunk": copy.deepcopy(updated_chunks[index]),
            }
        except Exception:
            raise


def _json_reference_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for relative in (
        "audio_generation_requests",
        "audio_invalidation_history",
        "approved_audio_promotion_history",
        "speaker_management_history",
        "external_workflows",
        "export_build_history",
        "migration_backups",
    ):
        directory = root / relative
        if not directory.is_dir() or directory.is_symlink():
            continue
        for candidate in directory.rglob("*.json"):
            if candidate.is_file() and not candidate.is_symlink():
                paths.append(candidate)
                if len(paths) >= 2000:
                    return paths
    return paths


def _external_references(
    root: Path,
    *,
    take_id: str,
    relative_path: str,
) -> list[str]:
    needles = (take_id, relative_path)
    references: list[str] = []
    for path in _json_reference_paths(root):
        try:
            if path.stat().st_size > MAX_REFERENCE_JSON_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(needle and needle in text for needle in needles):
            references.append(path.relative_to(root).as_posix())
            if len(references) >= 50:
                break
    return references


def _protected_ancestor_ids(registry: Mapping[str, Any]) -> set[str]:
    takes = dict(registry.get("takes") or {})
    protected = {
        take_id
        for take_id, take in takes.items()
        if take.get("current") or take.get("kept")
    }
    pending = list(protected)
    while pending:
        take_id = pending.pop()
        parent = (takes.get(take_id) or {}).get("source_take_id")
        if parent and parent not in protected:
            protected.add(parent)
            pending.append(parent)
    return protected


def delete_impact(
    root_dir: str | Path,
    *,
    chunks: list[Mapping[str, Any]],
    chunk_key_value: str,
    take_id: str,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    registry = registry_view(root, chunks)
    take = _require_take(
        registry,
        chunk_key_value=chunk_key_value,
        take_id=take_id,
    )
    blockers: list[dict[str, Any]] = []
    if take.get("current"):
        blockers.append({"code": "current_take", "message": "Current Take cannot be deleted."})
    if take.get("kept"):
        blockers.append({"code": "kept_take", "message": "Kept Take cannot be deleted."})
    if take_id in _protected_ancestor_ids(registry) and not take.get("current") and not take.get("kept"):
        blockers.append(
            {
                "code": "protected_ancestor",
                "message": "Take is an ancestor of a current or kept rendition.",
            }
        )
    children = [
        child_id
        for child_id, child in registry["takes"].items()
        if child.get("source_take_id") == take_id
    ]
    if children:
        blockers.append(
            {
                "code": "rendition_parent",
                "message": "Delete child renditions before deleting their source Take.",
                "take_ids": children,
            }
        )
    references = _external_references(
        root,
        take_id=take_id,
        relative_path=str(take["artifact"].get("relative_path") or ""),
    )
    if references:
        blockers.append(
            {
                "code": "referenced_by_evidence",
                "message": "Take is referenced by active or rollback evidence.",
                "paths": references,
            }
        )
    impact_seed = {
        "schema_version": AUDIO_TAKE_SCHEMA_VERSION,
        "chunk_key": chunk_key_value,
        "take_id": take_id,
        "take_record_fingerprint": take["record_fingerprint"],
        "registry_fingerprint": registry["registry_fingerprint"],
        "size_bytes": int(take["artifact"].get("size_bytes") or 0),
        "blockers": blockers,
    }
    return {
        **impact_seed,
        "safe_to_delete": not blockers,
        "impact_fingerprint": fingerprint_value(impact_seed),
    }


def _operation_id(operation: str, seed: Mapping[str, Any]) -> str:
    return f"{operation}_" + fingerprint_value(
        {**copy.deepcopy(dict(seed)), "nonce": secrets.token_hex(12)}
    )[:24]


def _history_operation_dir(root: Path, operation_id: str) -> Path:
    return history_root(root) / _safe_token(operation_id, label="Operation ID")


def _move_to_backup(
    *,
    root: Path,
    operation_dir: Path,
    take: Mapping[str, Any],
) -> dict[str, Any]:
    relative = str((take.get("artifact") or {}).get("relative_path") or "")
    source = confined_audio_path(root, relative)
    backup = operation_dir / "audio" / f"{take['take_id']}{source.suffix.casefold()}"
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not source.is_file() or sha256_file(source) != take["artifact"]["sha256"]:
        raise AudioTakeError(
            "audio_take_artifact_mismatch",
            "Take audio changed before deletion.",
        )
    os.replace(source, backup)
    return {
        "take_id": take["take_id"],
        "original_relative_path": relative,
        "backup_relative_path": backup.relative_to(root).as_posix(),
        "sha256": take["artifact"]["sha256"],
        "size_bytes": take["artifact"].get("size_bytes"),
    }


def _remove_take_from_registry(registry: dict[str, Any], take_id: str) -> None:
    take = registry["takes"].pop(take_id)
    entry = registry["chunks"][take["chunk_key"]]
    entry["take_ids"] = [value for value in entry["take_ids"] if value != take_id]
    if not entry["take_ids"] and entry.get("current_take_id") is None:
        registry["chunks"].pop(take["chunk_key"], None)


def apply_delete(
    root_dir: str | Path,
    *,
    chunks: list[Mapping[str, Any]],
    impact: Mapping[str, Any],
    expected_impact_fingerprint: str,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    with _registry_lock(root):
        current = delete_impact(
            root,
            chunks=chunks,
            chunk_key_value=str(impact.get("chunk_key") or ""),
            take_id=str(impact.get("take_id") or ""),
        )
        if current["impact_fingerprint"] != expected_impact_fingerprint:
            raise AudioTakeError(
                "audio_take_delete_impact_changed",
                "Take deletion impact changed after review.",
                context={"current_impact": current},
            )
        if not current["safe_to_delete"]:
            raise AudioTakeError(
                "audio_take_delete_blocked",
                "Take cannot be deleted because protected dependencies remain.",
                context={"blockers": current["blockers"]},
            )
        registry = materialize_registry(root, chunks)
        _require_registry_fingerprint(registry, current["registry_fingerprint"])
        take = _require_take(
            registry,
            chunk_key_value=current["chunk_key"],
            take_id=current["take_id"],
            expected_record_fingerprint=current["take_record_fingerprint"],
        )
        operation_id = _operation_id("audio_take_delete", current)
        operation_dir = _history_operation_dir(root, operation_id)
        operation_dir.mkdir(parents=True, exist_ok=False)
        before = copy.deepcopy(registry)
        backup = _move_to_backup(root=root, operation_dir=operation_dir, take=take)
        try:
            _remove_take_from_registry(registry, take["take_id"])
            written = _write_registry(root, registry)
            receipt = {
                "schema_version": 1,
                "operation": "delete_take",
                "operation_id": operation_id,
                "created_at_utc": _utc_now(),
                "status": "applied",
                "before_registry": before,
                "after_registry_fingerprint": written["registry_fingerprint"],
                "backups": [backup],
            }
            atomic_json_write(receipt, operation_dir / "receipt.json")
        except Exception:
            backup_path = confined_audio_path(root, backup["backup_relative_path"])
            original = confined_audio_path(root, backup["original_relative_path"])
            original.parent.mkdir(parents=True, exist_ok=True)
            if backup_path.is_file():
                os.replace(backup_path, original)
            raise
        return {
            "status": "deleted",
            "operation_id": operation_id,
            "take_id": take["take_id"],
            "registry_fingerprint": written["registry_fingerprint"],
        }


def cleanup_impact(
    root_dir: str | Path,
    *,
    chunks: list[Mapping[str, Any]],
    older_than_days: int,
    reclaim_at_least_bytes: int = 0,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    registry = registry_view(root, chunks)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, int(older_than_days)))
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for take_id, take in registry["takes"].items():
        try:
            created = datetime.fromisoformat(
                str(take["created_at_utc"]).replace("Z", "+00:00")
            )
        except ValueError:
            created = datetime.max.replace(tzinfo=timezone.utc)
        impact = delete_impact(
            root,
            chunks=chunks,
            chunk_key_value=take["chunk_key"],
            take_id=take_id,
        )
        if created > cutoff or not impact["safe_to_delete"]:
            skipped.append(
                {
                    "take_id": take_id,
                    "reason": "too_new" if created > cutoff else "protected",
                    "blockers": impact["blockers"],
                }
            )
            continue
        candidates.append(
            {
                "take_id": take_id,
                "chunk_key": take["chunk_key"],
                "created_at_utc": take["created_at_utc"],
                "size_bytes": int(take["artifact"].get("size_bytes") or 0),
                "take_record_fingerprint": take["record_fingerprint"],
            }
        )
    candidates.sort(key=lambda item: (item["created_at_utc"], item["take_id"]))
    if reclaim_at_least_bytes > 0:
        selected: list[dict[str, Any]] = []
        reclaimed = 0
        for item in candidates:
            selected.append(item)
            reclaimed += item["size_bytes"]
            if reclaimed >= reclaim_at_least_bytes:
                break
        candidates = selected
    total = sum(item["size_bytes"] for item in candidates)
    seed = {
        "schema_version": AUDIO_TAKE_SCHEMA_VERSION,
        "registry_fingerprint": registry["registry_fingerprint"],
        "older_than_days": max(0, int(older_than_days)),
        "reclaim_at_least_bytes": max(0, int(reclaim_at_least_bytes)),
        "candidates": candidates,
        "reclaimable_bytes": total,
    }
    return {
        **seed,
        "candidate_count": len(candidates),
        "skipped": skipped,
        "safe_to_apply": bool(candidates),
        "impact_fingerprint": fingerprint_value(seed),
    }


def apply_cleanup(
    root_dir: str | Path,
    *,
    chunks: list[Mapping[str, Any]],
    impact: Mapping[str, Any],
    expected_impact_fingerprint: str,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    with _registry_lock(root):
        current = cleanup_impact(
            root,
            chunks=chunks,
            older_than_days=int(impact.get("older_than_days") or 0),
            reclaim_at_least_bytes=int(impact.get("reclaim_at_least_bytes") or 0),
        )
        if current["impact_fingerprint"] != expected_impact_fingerprint:
            raise AudioTakeError(
                "audio_take_cleanup_impact_changed",
                "Take cleanup impact changed after review.",
                context={"current_impact": current},
            )
        if not current["safe_to_apply"]:
            raise AudioTakeError(
                "audio_take_cleanup_empty",
                "No eligible old Takes are available for cleanup.",
            )
        registry = materialize_registry(root, chunks)
        _require_registry_fingerprint(registry, current["registry_fingerprint"])
        operation_id = _operation_id("audio_take_cleanup", current)
        operation_dir = _history_operation_dir(root, operation_id)
        operation_dir.mkdir(parents=True, exist_ok=False)
        before = copy.deepcopy(registry)
        backups: list[dict[str, Any]] = []
        try:
            for item in current["candidates"]:
                take = _require_take(
                    registry,
                    chunk_key_value=item["chunk_key"],
                    take_id=item["take_id"],
                    expected_record_fingerprint=item["take_record_fingerprint"],
                )
                backups.append(
                    _move_to_backup(root=root, operation_dir=operation_dir, take=take)
                )
                _remove_take_from_registry(registry, take["take_id"])
            written = _write_registry(root, registry)
            receipt = {
                "schema_version": 1,
                "operation": "cleanup_takes",
                "operation_id": operation_id,
                "created_at_utc": _utc_now(),
                "status": "applied",
                "before_registry": before,
                "after_registry_fingerprint": written["registry_fingerprint"],
                "impact": copy.deepcopy(current),
                "backups": backups,
            }
            atomic_json_write(receipt, operation_dir / "receipt.json")
        except Exception:
            for backup in reversed(backups):
                backup_path = confined_audio_path(root, backup["backup_relative_path"])
                original = confined_audio_path(root, backup["original_relative_path"])
                original.parent.mkdir(parents=True, exist_ok=True)
                if backup_path.is_file():
                    os.replace(backup_path, original)
            raise
        return {
            "status": "cleaned",
            "operation_id": operation_id,
            "deleted_take_ids": [item["take_id"] for item in current["candidates"]],
            "reclaimed_bytes": current["reclaimable_bytes"],
            "registry_fingerprint": written["registry_fingerprint"],
        }


def undo_operation(
    root_dir: str | Path,
    *,
    operation_id: str,
    expected_registry_fingerprint: str,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    with _registry_lock(root):
        operation_dir = _history_operation_dir(root, operation_id)
        receipt_path = operation_dir / "receipt.json"
        if not receipt_path.is_file():
            raise AudioTakeError(
                "audio_take_operation_missing",
                "Take operation receipt does not exist.",
            )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("status") != "applied":
            raise AudioTakeError(
                "audio_take_operation_already_undone",
                "Take operation was already undone.",
            )
        registry = load_registry(root)
        _require_registry_fingerprint(registry, expected_registry_fingerprint)
        if registry["registry_fingerprint"] != receipt.get("after_registry_fingerprint"):
            raise AudioTakeError(
                "audio_take_undo_conflict",
                "Take registry changed after the operation and cannot be undone safely.",
            )
        chunks_path = root / "chunks.json"
        if receipt.get("before_chunks") is not None:
            try:
                current_chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AudioTakeError(
                    "audio_take_undo_conflict",
                    "Current chunks could not be verified for Take undo.",
                ) from exc
            if fingerprint_value(current_chunks) != receipt.get(
                "after_chunks_fingerprint"
            ):
                raise AudioTakeError(
                    "audio_take_undo_conflict",
                    "Chunks changed after the Take operation and cannot be undone safely.",
                )
        restored: list[str] = []
        created_audio_path = receipt.get("created_audio_path")
        if created_audio_path:
            created = confined_audio_path(root, str(created_audio_path))
            expected_created_sha = str(
                receipt.get("created_audio_sha256") or ""
            )
            if (
                not created.is_file()
                or not expected_created_sha
                or sha256_file(created) != expected_created_sha
            ):
                raise AudioTakeError(
                    "audio_take_undo_conflict",
                    "The child rendition audio is missing or changed and cannot be undone safely.",
                )
        binary_writes: dict[str, bytes] = {}
        deletes: list[str] = []
        for backup in receipt.get("backups") or []:
            backup_path = confined_audio_path(root, backup["backup_relative_path"])
            original = confined_audio_path(root, backup["original_relative_path"])
            if original.exists():
                raise AudioTakeError(
                    "audio_take_undo_conflict",
                    "A newer file occupies a Take path required by undo.",
                )
            if not backup_path.is_file() or sha256_file(backup_path) != backup["sha256"]:
                raise AudioTakeError(
                    "audio_take_backup_invalid",
                    "Take backup is missing or changed.",
                )
            binary_writes[original.relative_to(root).as_posix()] = backup_path.read_bytes()
            deletes.append(backup_path.relative_to(root).as_posix())
            restored.append(backup["take_id"])
        if created_audio_path:
            deletes.append(created.relative_to(root).as_posix())
        restored_registry = _with_registry_fingerprint(
            {
                **_registry_seed(receipt["before_registry"]),
                "updated_at_utc": _utc_now(),
            }
        )
        receipt["status"] = "undone"
        receipt["undone_at_utc"] = _utc_now()
        receipt["undo_registry_fingerprint"] = restored_registry[
            "registry_fingerprint"
        ]
        json_writes = {
            AUDIO_TAKE_REGISTRY_FILENAME: restored_registry,
            receipt_path.relative_to(root).as_posix(): receipt,
        }
        if receipt.get("before_chunks") is not None:
            json_writes[chunks_path.relative_to(root).as_posix()] = receipt["before_chunks"]
        apply_audio_transition(
            root,
            transition="undo_restoration",
            operation_id=f"undo-{fingerprint_value({'operation_id': operation_id, 'receipt': receipt})[:32]}",
            json_writes=json_writes,
            binary_writes=binary_writes,
            deletes=deletes,
        )
        return {
            "status": "undone",
            "operation_id": operation_id,
            "restored_take_ids": restored,
            "registry_fingerprint": restored_registry["registry_fingerprint"],
        }
