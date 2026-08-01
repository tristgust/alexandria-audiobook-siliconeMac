from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from generation_state import atomic_json_write, fingerprint_value


AUDIO_GENERATION_REQUEST_SCHEMA_VERSION = 1
AUDIO_GENERATION_REQUESTS_DIRNAME = "audio_generation_requests"
REQUEST_FILENAME = "request.json"
MAX_PENDING_REQUESTS = 1

TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled", "replaced"})
ACTIVE_STATES = frozenset(
    {
        "prepared",
        "queued_replacement",
        "running",
        "cancelling",
        "resumable",
    }
)

_LIFECYCLE_LOCK = threading.RLock()


class AudioGenerationLifecycleError(RuntimeError):
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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise AudioGenerationLifecycleError(
            "audio_request_field_invalid",
            f"{field} must be non-empty text.",
        )
    return normalized


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise AudioGenerationLifecycleError(
            "audio_request_field_invalid",
            f"{field} must be an integer.",
        )
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise AudioGenerationLifecycleError(
            "audio_request_field_invalid",
            f"{field} must be an integer.",
        ) from exc
    if normalized < minimum:
        raise AudioGenerationLifecycleError(
            "audio_request_field_invalid",
            f"{field} must be at least {minimum}.",
        )
    return normalized


def _requests_root(project_root: str | Path) -> Path:
    return (
        Path(project_root).expanduser().resolve()
        / AUDIO_GENERATION_REQUESTS_DIRNAME
    )


def request_path(project_root: str | Path, request_id: str) -> Path:
    safe = _text(request_id, "request_id")
    if not safe.startswith("audio_request_") or not all(
        character.isalnum() or character == "_" for character in safe
    ):
        raise AudioGenerationLifecycleError(
            "audio_request_id_invalid",
            "Audio generation request ID is invalid.",
        )
    return _requests_root(project_root) / safe / REQUEST_FILENAME


def _segment_path(
    project_root: str | Path,
    request_id: str,
    chunk_key: str,
    segment_id: str,
) -> Path:
    safe_chunk = hashlib.sha256(chunk_key.encode("utf-8")).hexdigest()[:20]
    safe_segment = hashlib.sha256(segment_id.encode("utf-8")).hexdigest()[:20]
    return (
        request_path(project_root, request_id).parent
        / "segments"
        / safe_chunk
        / f"{safe_segment}.wav"
    )


def segment_output_path(
    project_root: str | Path,
    request_id: str,
    chunk_key: str,
    segment_id: str,
) -> Path:
    path = _segment_path(project_root, request_id, chunk_key, segment_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AudioGenerationLifecycleError(
            "audio_request_missing",
            f"Audio generation request does not exist: {path.parent.name}.",
        ) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AudioGenerationLifecycleError(
            "audio_request_corrupt",
            f"Audio generation request could not be read: {exc}",
        ) from exc
    if not isinstance(value, dict):
        raise AudioGenerationLifecycleError(
            "audio_request_corrupt",
            "Audio generation request must contain a JSON object.",
        )
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalize_segment(value: Mapping[str, Any], *, chunk_key: str) -> dict[str, Any]:
    segment_id = _text(value.get("segment_id"), "segment_id")
    dependency = _text(
        value.get("dependency_fingerprint"),
        "segment dependency_fingerprint",
    )
    return {
        "segment_id": segment_id,
        "segment_index": _integer(value.get("segment_index"), "segment_index"),
        "source_start": _integer(value.get("source_start"), "source_start"),
        "source_end": _integer(value.get("source_end"), "source_end"),
        "generation_text_sha256": _text(
            value.get("generation_text_sha256"),
            "generation_text_sha256",
        ),
        "dependency_fingerprint": dependency,
        "chunk_key": chunk_key,
    }


def _normalize_chunk(value: Mapping[str, Any]) -> dict[str, Any]:
    chunk_key = _text(value.get("chunk_key"), "chunk_key")
    index = _integer(value.get("index"), "chunk index")
    dependency = _text(
        value.get("dependency_fingerprint"),
        "chunk dependency_fingerprint",
    )
    raw_segments = value.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        raise AudioGenerationLifecycleError(
            "audio_request_segments_invalid",
            f"{chunk_key} must declare at least one internal segment.",
        )
    segments = [
        _normalize_segment(item, chunk_key=chunk_key)
        for item in raw_segments
        if isinstance(item, Mapping)
    ]
    if len(segments) != len(raw_segments):
        raise AudioGenerationLifecycleError(
            "audio_request_segments_invalid",
            f"{chunk_key} contains an invalid segment declaration.",
        )
    segment_ids = [item["segment_id"] for item in segments]
    if len(set(segment_ids)) != len(segment_ids):
        raise AudioGenerationLifecycleError(
            "audio_request_segments_invalid",
            f"{chunk_key} contains duplicate segment IDs.",
        )
    ordered = sorted(segments, key=lambda item: item["segment_index"])
    if [item["segment_index"] for item in ordered] != list(range(len(ordered))):
        raise AudioGenerationLifecycleError(
            "audio_request_segments_invalid",
            f"{chunk_key} segment indices must be contiguous from zero.",
        )
    for previous, current in zip(ordered, ordered[1:]):
        if previous["source_end"] != current["source_start"]:
            raise AudioGenerationLifecycleError(
                "audio_request_segments_invalid",
                f"{chunk_key} segment source spans are not adjacent.",
            )
    return {
        "chunk_key": chunk_key,
        "index": index,
        "chunk_id": copy.deepcopy(value.get("chunk_id", index)),
        "dependency_fingerprint": dependency,
        "segment_plan_fingerprint": _text(
            value.get("segment_plan_fingerprint"),
            "segment_plan_fingerprint",
        ),
        "segments": ordered,
    }


def normalize_request_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AudioGenerationLifecycleError(
            "audio_request_manifest_invalid",
            "Audio generation request manifest must be an object.",
        )
    raw_chunks = value.get("chunks")
    if not isinstance(raw_chunks, list) or not raw_chunks:
        raise AudioGenerationLifecycleError(
            "audio_request_manifest_invalid",
            "Audio generation request must contain at least one chunk.",
        )
    chunks = [_normalize_chunk(item) for item in raw_chunks if isinstance(item, Mapping)]
    if len(chunks) != len(raw_chunks):
        raise AudioGenerationLifecycleError(
            "audio_request_manifest_invalid",
            "Audio generation request contains an invalid chunk declaration.",
        )
    chunk_keys = [item["chunk_key"] for item in chunks]
    indices = [item["index"] for item in chunks]
    if len(set(chunk_keys)) != len(chunk_keys) or len(set(indices)) != len(indices):
        raise AudioGenerationLifecycleError(
            "audio_request_manifest_invalid",
            "Audio generation request contains duplicate chunks.",
        )
    normalized = {
        "schema_version": AUDIO_GENERATION_REQUEST_SCHEMA_VERSION,
        "mode": _text(value.get("mode") or "parallel", "mode"),
        "operation_mode": str(value.get("operation_mode") or "legacy_batch"),
        "generation_seed": value.get("generation_seed"),
        "plan_fingerprint": value.get("plan_fingerprint"),
        "chunks_fingerprint": value.get("chunks_fingerprint"),
        "dependency_fingerprint": _text(
            value.get("dependency_fingerprint"),
            "request dependency_fingerprint",
        ),
        "execution": copy.deepcopy(dict(value.get("execution") or {})),
        "chunks": sorted(chunks, key=lambda item: item["index"]),
    }
    normalized["request_fingerprint"] = fingerprint_value(normalized)
    normalized["request_id"] = (
        "audio_request_" + normalized["request_fingerprint"][:24]
    )
    return normalized


def _new_chunk_progress(chunk: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "chunk_key": chunk["chunk_key"],
        "index": chunk["index"],
        "chunk_id": copy.deepcopy(chunk.get("chunk_id")),
        "dependency_fingerprint": chunk["dependency_fingerprint"],
        "segment_plan_fingerprint": chunk["segment_plan_fingerprint"],
        "state": "pending",
        "started_at": None,
        "finished_at": None,
        "error": None,
        "canonical_artifact": None,
        "segments": {
            item["segment_id"]: {
                **copy.deepcopy(item),
                "state": "pending",
                "started_at": None,
                "finished_at": None,
                "attempt_count": 0,
                "artifact": None,
                "error": None,
            }
            for item in chunk["segments"]
        },
    }


def _record_fingerprint(record: Mapping[str, Any]) -> str:
    public = copy.deepcopy(dict(record))
    public.pop("record_fingerprint", None)
    return fingerprint_value(public)


def _write_record(path: Path, record: dict[str, Any]) -> dict[str, Any]:
    record["record_fingerprint"] = _record_fingerprint(record)
    atomic_json_write(record, path)
    return copy.deepcopy(record)


def validate_request_record(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AudioGenerationLifecycleError(
            "audio_request_corrupt",
            "Audio generation request must be an object.",
        )
    record = copy.deepcopy(dict(value))
    if record.get("schema_version") != AUDIO_GENERATION_REQUEST_SCHEMA_VERSION:
        raise AudioGenerationLifecycleError(
            "audio_request_corrupt",
            "Unsupported audio generation request schema.",
        )
    expected = record.get("record_fingerprint")
    if not isinstance(expected, str) or expected != _record_fingerprint(record):
        raise AudioGenerationLifecycleError(
            "audio_request_corrupt",
            "Audio generation request fingerprint does not match its contents.",
        )
    state = str(record.get("state") or "")
    if state not in ACTIVE_STATES | TERMINAL_STATES:
        raise AudioGenerationLifecycleError(
            "audio_request_corrupt",
            f"Audio generation request has invalid state {state!r}.",
        )
    return record


def load_request(project_root: str | Path, request_id: str) -> dict[str, Any]:
    return validate_request_record(_read_json(request_path(project_root, request_id)))


def list_requests(project_root: str | Path) -> list[dict[str, Any]]:
    root = _requests_root(project_root)
    if not root.is_dir():
        return []
    records = []
    for path in sorted(root.glob(f"audio_request_*/{REQUEST_FILENAME}")):
        try:
            records.append(validate_request_record(_read_json(path)))
        except AudioGenerationLifecycleError:
            continue
    return sorted(
        records,
        key=lambda item: str(item.get("created_at") or ""),
    )


def _active_requests(project_root: str | Path) -> list[dict[str, Any]]:
    return [item for item in list_requests(project_root) if item["state"] in ACTIVE_STATES]


def _terminal_summary(record: Mapping[str, Any]) -> dict[str, int]:
    chunks = list((record.get("progress") or {}).values())
    return {
        "total": len(chunks),
        "completed": sum(item.get("state") == "completed" for item in chunks),
        "failed": sum(item.get("state") == "failed" for item in chunks),
        "cancelled": sum(item.get("state") == "cancelled" for item in chunks),
        "pending": sum(
            item.get("state") in {"pending", "running"} for item in chunks
        ),
    }


def prepare_request(
    project_root: str | Path,
    manifest: Mapping[str, Any],
    *,
    operation_id: str | None = None,
    replace_active: bool = False,
    max_pending: int = MAX_PENDING_REQUESTS,
    at_utc: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_request_manifest(manifest)
    request_id = normalized["request_id"]
    now = at_utc or utc_now()
    path = request_path(project_root, request_id)
    with _LIFECYCLE_LOCK:
        if path.is_file():
            existing = load_request(project_root, request_id)
            return {
                "record": existing,
                "created": False,
                "duplicate": True,
                "dispatch_required": existing["state"] == "resumable",
                "terminal": existing["state"] in TERMINAL_STATES,
            }
        active = [item for item in _active_requests(project_root) if item["request_id"] != request_id]
        predecessor = None
        if active:
            if not replace_active:
                raise AudioGenerationLifecycleError(
                    "audio_request_already_active",
                    "Another audio generation request is already active.",
                    context={"active_request_id": active[0]["request_id"]},
                )
            queued = [
                item
                for item in active
                if item["state"] == "queued_replacement"
            ]
            predecessors = [
                item
                for item in active
                if item["state"] != "queued_replacement"
                and not item.get("replaces_request_id")
            ]
            if (
                len(queued) >= max(1, int(max_pending))
                or any(item.get("replacement_request_id") for item in predecessors)
                or not predecessors
            ):
                raise AudioGenerationLifecycleError(
                    "audio_request_backpressure",
                    "The bounded audio replacement queue is full.",
                    context={
                        "active_request_ids": [item["request_id"] for item in active],
                        "limit": max(1, int(max_pending)),
                    },
                )
            predecessor = predecessors[0]
            predecessor["cancel_requested"] = True
            predecessor["cancel_requested_at"] = now
            predecessor["replacement_request_id"] = request_id
            predecessor["updated_at"] = now
            predecessor_unowned = bool(
                predecessor["state"] in {"prepared", "resumable"}
                and not predecessor.get("owner_token")
            )
            if predecessor_unowned:
                predecessor["state"] = "replaced"
                predecessor["finished_at"] = now
                predecessor["terminal_reason"] = "replacement_requested"
                for chunk in predecessor["progress"].values():
                    if chunk["state"] != "completed":
                        chunk["state"] = "cancelled"
                        chunk["finished_at"] = now
                predecessor["terminal_summary"] = _terminal_summary(
                    predecessor
                )
                predecessor["terminal_receipt_fingerprint"] = (
                    fingerprint_value(
                        {
                            "request_id": predecessor["request_id"],
                            "request_fingerprint": predecessor[
                                "request_fingerprint"
                            ],
                            "state": predecessor["state"],
                            "summary": predecessor["terminal_summary"],
                            "finished_at": predecessor["finished_at"],
                        }
                    )
                )
            else:
                predecessor["state"] = "cancelling"
            _write_record(
                request_path(project_root, predecessor["request_id"]),
                predecessor,
            )
        else:
            predecessor_unowned = False
        record = {
            "schema_version": AUDIO_GENERATION_REQUEST_SCHEMA_VERSION,
            "request_id": request_id,
            "request_fingerprint": normalized["request_fingerprint"],
            "operation_id": operation_id,
            "state": (
                "queued_replacement"
                if predecessor and not predecessor_unowned
                else "prepared"
            ),
            "manifest": normalized,
            "progress": {
                chunk["chunk_key"]: _new_chunk_progress(chunk)
                for chunk in normalized["chunks"]
            },
            "attempt_count": 0,
            "owner_token": None,
            "owner_process_id": None,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "finished_at": None,
            "interrupted_at": None,
            "cancel_requested": False,
            "cancel_requested_at": None,
            "replaces_request_id": predecessor["request_id"] if predecessor else None,
            "replacement_request_id": None,
            "terminal_reason": None,
            "last_error": None,
            "terminal_summary": None,
            "terminal_receipt_fingerprint": None,
            "record_fingerprint": None,
        }
        path.parent.mkdir(parents=True, exist_ok=False)
        created = _write_record(path, record)
        return {
            "record": created,
            "created": True,
            "duplicate": False,
            "dispatch_required": predecessor is None or predecessor_unowned,
            "terminal": False,
        }


def _predecessor_terminal(project_root: str | Path, record: Mapping[str, Any]) -> bool:
    predecessor = record.get("replaces_request_id")
    if not predecessor:
        return True
    try:
        return load_request(project_root, str(predecessor))["state"] in TERMINAL_STATES
    except AudioGenerationLifecycleError:
        return False


def claim_request(
    project_root: str | Path,
    request_id: str,
    *,
    expected_request_fingerprint: str,
    owner_process_id: int | None = None,
    at_utc: str | None = None,
) -> dict[str, Any]:
    now = at_utc or utc_now()
    with _LIFECYCLE_LOCK:
        record = load_request(project_root, request_id)
        if record["request_fingerprint"] != expected_request_fingerprint:
            raise AudioGenerationLifecycleError(
                "audio_request_dependency_changed",
                "Audio generation request dependencies changed before claim.",
            )
        if record["state"] in TERMINAL_STATES:
            return record
        if record["state"] == "queued_replacement" and not _predecessor_terminal(
            project_root, record
        ):
            raise AudioGenerationLifecycleError(
                "audio_request_predecessor_active",
                "Replacement audio generation cannot start until the prior request is terminal.",
            )
        if record["state"] not in {"prepared", "resumable", "queued_replacement"}:
            raise AudioGenerationLifecycleError(
                "audio_request_claim_conflict",
                f"Audio generation request cannot be claimed from {record['state']} state.",
            )
        token = secrets.token_hex(16)
        record.update(
            {
                "state": "running",
                "owner_token": token,
                "owner_process_id": owner_process_id if owner_process_id is not None else os.getpid(),
                "attempt_count": int(record.get("attempt_count") or 0) + 1,
                "started_at": record.get("started_at") or now,
                "updated_at": now,
                "interrupted_at": None,
                "terminal_reason": None,
                "last_error": None,
            }
        )
        return _write_record(request_path(project_root, request_id), record)


def _require_owner(
    record: Mapping[str, Any],
    owner_token: str,
    *,
    allow_cancelling: bool = False,
) -> None:
    if record.get("owner_token") != owner_token:
        raise AudioGenerationLifecycleError(
            "audio_request_owner_stale",
            "This audio generation worker no longer owns the request.",
        )
    allowed = {"running"}
    if allow_cancelling:
        allowed.add("cancelling")
    if record.get("state") not in allowed:
        raise AudioGenerationLifecycleError(
            "audio_request_not_running",
            f"Audio generation request is {record.get('state')}, not running.",
        )


def should_cancel(
    project_root: str | Path,
    request_id: str,
    owner_token: str,
) -> bool:
    try:
        record = load_request(project_root, request_id)
    except AudioGenerationLifecycleError:
        return True
    return bool(
        record.get("owner_token") != owner_token
        or record.get("cancel_requested")
        or record.get("state") in {"cancelling", "cancelled", "replaced"}
    )


def request_context(
    project_root: str | Path,
    request_id: str,
    owner_token: str,
    chunk_key: str,
) -> dict[str, Any]:
    record = load_request(project_root, request_id)
    _require_owner(record, owner_token)
    if chunk_key not in record["progress"]:
        raise AudioGenerationLifecycleError(
            "audio_request_chunk_missing",
            f"Audio generation request does not contain {chunk_key}.",
        )
    return {
        "project_root": str(Path(project_root).expanduser().resolve()),
        "request_id": request_id,
        "request_fingerprint": record["request_fingerprint"],
        "owner_token": owner_token,
        "chunk_key": chunk_key,
        "chunk_dependency_fingerprint": record["progress"][chunk_key][
            "dependency_fingerprint"
        ],
    }


def record_chunk_started(
    project_root: str | Path,
    request_id: str,
    owner_token: str,
    chunk_key: str,
    *,
    at_utc: str | None = None,
) -> dict[str, Any]:
    now = at_utc or utc_now()
    with _LIFECYCLE_LOCK:
        record = load_request(project_root, request_id)
        _require_owner(record, owner_token)
        chunk = record["progress"].get(chunk_key)
        if not isinstance(chunk, dict):
            raise AudioGenerationLifecycleError(
                "audio_request_chunk_missing",
                f"Audio generation request does not contain {chunk_key}.",
            )
        if chunk["state"] == "completed":
            return record
        chunk["state"] = "running"
        chunk["started_at"] = chunk.get("started_at") or now
        chunk["finished_at"] = None
        chunk["error"] = None
        record["updated_at"] = now
        return _write_record(request_path(project_root, request_id), record)


def _segment_record(
    record: Mapping[str, Any],
    chunk_key: str,
    segment_id: str,
) -> dict[str, Any]:
    chunk = (record.get("progress") or {}).get(chunk_key)
    if not isinstance(chunk, Mapping):
        raise AudioGenerationLifecycleError(
            "audio_request_chunk_missing",
            f"Audio generation request does not contain {chunk_key}.",
        )
    segment = (chunk.get("segments") or {}).get(segment_id)
    if not isinstance(segment, Mapping):
        raise AudioGenerationLifecycleError(
            "audio_request_segment_missing",
            f"Audio generation request does not contain {segment_id} for {chunk_key}.",
        )
    return copy.deepcopy(dict(segment))


def record_segment_started(
    project_root: str | Path,
    request_id: str,
    owner_token: str,
    chunk_key: str,
    segment_id: str,
    *,
    expected_dependency_fingerprint: str,
    at_utc: str | None = None,
) -> dict[str, Any]:
    now = at_utc or utc_now()
    with _LIFECYCLE_LOCK:
        record = load_request(project_root, request_id)
        _require_owner(record, owner_token)
        segment = _segment_record(record, chunk_key, segment_id)
        if segment["dependency_fingerprint"] != expected_dependency_fingerprint:
            raise AudioGenerationLifecycleError(
                "audio_request_dependency_changed",
                f"Segment {segment_id} dependency changed before generation.",
            )
        target = record["progress"][chunk_key]["segments"][segment_id]
        if target["state"] == "completed":
            return record
        target["state"] = "running"
        target["started_at"] = target.get("started_at") or now
        target["attempt_count"] = int(target.get("attempt_count") or 0) + 1
        target["error"] = None
        record["updated_at"] = now
        return _write_record(request_path(project_root, request_id), record)


def completed_segment_artifact(
    project_root: str | Path,
    request_id: str,
    chunk_key: str,
    segment_id: str,
    *,
    expected_dependency_fingerprint: str,
) -> dict[str, Any] | None:
    record = load_request(project_root, request_id)
    segment = _segment_record(record, chunk_key, segment_id)
    if (
        segment.get("state") != "completed"
        or segment.get("dependency_fingerprint") != expected_dependency_fingerprint
        or not isinstance(segment.get("artifact"), Mapping)
    ):
        return None
    artifact = dict(segment["artifact"])
    path = Path(str(artifact.get("path") or "")).expanduser().resolve()
    expected_path = _segment_path(project_root, request_id, chunk_key, segment_id).resolve()
    if path != expected_path or not path.is_file():
        return None
    if _file_sha256(path) != artifact.get("sha256"):
        return None
    if path.stat().st_size != artifact.get("size_bytes"):
        return None
    return copy.deepcopy(artifact)


def record_segment_completed(
    project_root: str | Path,
    request_id: str,
    owner_token: str,
    chunk_key: str,
    segment_id: str,
    *,
    expected_dependency_fingerprint: str,
    artifact_path: str | Path,
    sample_rate: int,
    sample_count: int,
    metadata: Mapping[str, Any] | None = None,
    at_utc: str | None = None,
) -> dict[str, Any]:
    now = at_utc or utc_now()
    source = Path(artifact_path).expanduser().resolve()
    expected_path = _segment_path(project_root, request_id, chunk_key, segment_id).resolve()
    if source != expected_path or not source.is_file():
        raise AudioGenerationLifecycleError(
            "audio_request_segment_artifact_invalid",
            "Segment artifact must exist at its request-owned path.",
        )
    artifact = {
        "path": str(source),
        "sha256": _file_sha256(source),
        "size_bytes": source.stat().st_size,
        "sample_rate": _integer(sample_rate, "sample_rate", minimum=1),
        "sample_count": _integer(sample_count, "sample_count", minimum=1),
        "metadata": copy.deepcopy(dict(metadata or {})),
    }
    with _LIFECYCLE_LOCK:
        record = load_request(project_root, request_id)
        _require_owner(record, owner_token, allow_cancelling=True)
        if record.get("cancel_requested"):
            raise AudioGenerationLifecycleError(
                "audio_request_cancelled",
                "Cancelled audio generation cannot publish a segment artifact.",
            )
        segment = _segment_record(record, chunk_key, segment_id)
        if segment["dependency_fingerprint"] != expected_dependency_fingerprint:
            raise AudioGenerationLifecycleError(
                "audio_request_dependency_changed",
                f"Segment {segment_id} dependency changed before publication.",
            )
        target = record["progress"][chunk_key]["segments"][segment_id]
        target.update(
            {
                "state": "completed",
                "finished_at": now,
                "artifact": artifact,
                "error": None,
            }
        )
        record["updated_at"] = now
        return _write_record(request_path(project_root, request_id), record)


def record_segment_failed(
    project_root: str | Path,
    request_id: str,
    owner_token: str,
    chunk_key: str,
    segment_id: str,
    *,
    error: str,
    at_utc: str | None = None,
) -> dict[str, Any]:
    now = at_utc or utc_now()
    with _LIFECYCLE_LOCK:
        record = load_request(project_root, request_id)
        _require_owner(record, owner_token, allow_cancelling=True)
        target = record["progress"][chunk_key]["segments"][segment_id]
        target.update(
            {
                "state": "failed",
                "finished_at": now,
                "error": str(error),
            }
        )
        chunk = record["progress"][chunk_key]
        chunk["state"] = "failed"
        chunk["finished_at"] = now
        chunk["error"] = str(error)
        record["last_error"] = str(error)
        record["updated_at"] = now
        return _write_record(request_path(project_root, request_id), record)


def guard_publication(
    project_root: str | Path,
    request_id: str,
    owner_token: str,
    chunk_key: str,
    *,
    current_request_fingerprint: str,
    current_chunk_dependency_fingerprint: str,
) -> dict[str, Any]:
    record = load_request(project_root, request_id)
    _require_owner(record, owner_token)
    if record.get("cancel_requested"):
        raise AudioGenerationLifecycleError(
            "audio_request_cancelled",
            "Cancelled audio generation cannot publish canonical audio.",
        )
    if record["request_fingerprint"] != current_request_fingerprint:
        raise AudioGenerationLifecycleError(
            "audio_request_dependency_changed",
            "Audio generation request identity changed before publication.",
        )
    chunk = record["progress"].get(chunk_key)
    if not isinstance(chunk, Mapping):
        raise AudioGenerationLifecycleError(
            "audio_request_chunk_missing",
            f"Audio generation request does not contain {chunk_key}.",
        )
    if chunk["dependency_fingerprint"] != current_chunk_dependency_fingerprint:
        raise AudioGenerationLifecycleError(
            "audio_request_dependency_changed",
            f"{chunk_key} dependencies changed before publication.",
        )
    segments = list((chunk.get("segments") or {}).values())
    if not segments or any(item.get("state") != "completed" for item in segments):
        raise AudioGenerationLifecycleError(
            "audio_request_segments_incomplete",
            f"{chunk_key} cannot publish before every segment is complete.",
        )
    return copy.deepcopy(dict(chunk))


def record_chunk_completed(
    project_root: str | Path,
    request_id: str,
    owner_token: str,
    chunk_key: str,
    *,
    canonical_artifact: Mapping[str, Any],
    at_utc: str | None = None,
) -> dict[str, Any]:
    now = at_utc or utc_now()
    with _LIFECYCLE_LOCK:
        record = load_request(project_root, request_id)
        _require_owner(record, owner_token)
        if record.get("cancel_requested"):
            raise AudioGenerationLifecycleError(
                "audio_request_cancelled",
                "Cancelled audio generation cannot complete a chunk.",
            )
        chunk = record["progress"][chunk_key]
        if any(
            item.get("state") != "completed"
            for item in chunk["segments"].values()
        ):
            raise AudioGenerationLifecycleError(
                "audio_request_segments_incomplete",
                f"{chunk_key} has incomplete segment progress.",
            )
        chunk.update(
            {
                "state": "completed",
                "finished_at": now,
                "canonical_artifact": copy.deepcopy(dict(canonical_artifact)),
                "error": None,
            }
        )
        record["updated_at"] = now
        return _write_record(request_path(project_root, request_id), record)


def publish_chunk(
    project_root: str | Path,
    request_id: str,
    owner_token: str,
    chunk_key: str,
    *,
    current_request_fingerprint: str,
    current_chunk_dependency_fingerprint: str,
    publisher: Callable[[], Mapping[str, Any]],
    at_utc: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Publish canonical chunk state while cancellation is excluded.

    The lifecycle lock is deliberately held across the bounded canonical
    publication callback. A cancellation that acquired the lock first prevents
    publication. A cancellation that arrives later observes the chunk already
    completed and can only cancel remaining work.
    """
    now = at_utc or utc_now()
    with _LIFECYCLE_LOCK:
        record = load_request(project_root, request_id)
        _require_owner(record, owner_token)
        if record.get("cancel_requested"):
            raise AudioGenerationLifecycleError(
                "audio_request_cancelled",
                "Cancelled audio generation cannot publish canonical audio.",
            )
        if record["request_fingerprint"] != current_request_fingerprint:
            raise AudioGenerationLifecycleError(
                "audio_request_dependency_changed",
                "Audio generation request identity changed before publication.",
            )
        chunk = record["progress"].get(chunk_key)
        if not isinstance(chunk, dict):
            raise AudioGenerationLifecycleError(
                "audio_request_chunk_missing",
                f"Audio generation request does not contain {chunk_key}.",
            )
        if chunk["dependency_fingerprint"] != current_chunk_dependency_fingerprint:
            raise AudioGenerationLifecycleError(
                "audio_request_dependency_changed",
                f"{chunk_key} dependencies changed before publication.",
            )
        if any(
            item.get("state") != "completed"
            for item in chunk["segments"].values()
        ):
            raise AudioGenerationLifecycleError(
                "audio_request_segments_incomplete",
                f"{chunk_key} cannot publish before every segment is complete.",
            )
        artifact = copy.deepcopy(dict(publisher()))
        chunk.update(
            {
                "state": "completed",
                "finished_at": now,
                "canonical_artifact": artifact,
                "error": None,
            }
        )
        record["updated_at"] = now
        updated = _write_record(request_path(project_root, request_id), record)
        return artifact, updated


def record_chunk_failed(
    project_root: str | Path,
    request_id: str,
    owner_token: str,
    chunk_key: str,
    *,
    error: str,
    at_utc: str | None = None,
) -> dict[str, Any]:
    now = at_utc or utc_now()
    with _LIFECYCLE_LOCK:
        record = load_request(project_root, request_id)
        _require_owner(record, owner_token, allow_cancelling=True)
        chunk = record["progress"][chunk_key]
        chunk["state"] = "failed"
        chunk["finished_at"] = now
        chunk["error"] = str(error)
        record["last_error"] = str(error)
        record["updated_at"] = now
        return _write_record(request_path(project_root, request_id), record)


def request_cancel(
    project_root: str | Path,
    request_id: str,
    *,
    at_utc: str | None = None,
    reason: str = "operator_cancelled",
) -> dict[str, Any]:
    now = at_utc or utc_now()
    with _LIFECYCLE_LOCK:
        record = load_request(project_root, request_id)
        if record["state"] in TERMINAL_STATES:
            return record
        record["cancel_requested"] = True
        record["cancel_requested_at"] = now
        record["terminal_reason"] = reason
        record["updated_at"] = now
        if record["state"] in {"prepared", "resumable", "queued_replacement"}:
            record["state"] = "cancelled"
            record["finished_at"] = now
            for chunk in record["progress"].values():
                if chunk["state"] != "completed":
                    chunk["state"] = "cancelled"
                    chunk["finished_at"] = now
        else:
            record["state"] = "cancelling"
        record["terminal_summary"] = _terminal_summary(record)
        if record["state"] in TERMINAL_STATES:
            record["terminal_receipt_fingerprint"] = fingerprint_value(
                {
                    "request_id": record["request_id"],
                    "request_fingerprint": record["request_fingerprint"],
                    "state": record["state"],
                    "summary": record["terminal_summary"],
                    "finished_at": record["finished_at"],
                }
            )
        return _write_record(request_path(project_root, request_id), record)


def finalize_request(
    project_root: str | Path,
    request_id: str,
    owner_token: str,
    *,
    error: str | None = None,
    at_utc: str | None = None,
) -> dict[str, Any]:
    now = at_utc or utc_now()
    with _LIFECYCLE_LOCK:
        record = load_request(project_root, request_id)
        _require_owner(record, owner_token, allow_cancelling=True)
        summary = _terminal_summary(record)
        if record.get("cancel_requested") or record["state"] == "cancelling":
            state = "replaced" if record.get("replacement_request_id") else "cancelled"
            for chunk in record["progress"].values():
                if chunk["state"] in {"pending", "running"}:
                    chunk["state"] = "cancelled"
                    chunk["finished_at"] = now
            summary = _terminal_summary(record)
            reason = record.get("terminal_reason") or (
                "replacement_requested" if state == "replaced" else "operator_cancelled"
            )
        elif error:
            state = "failed"
            reason = "worker_error"
            record["last_error"] = str(error)
        elif summary["completed"] == summary["total"] and summary["total"] > 0:
            state = "succeeded"
            reason = "all_chunks_completed"
        else:
            state = "failed"
            reason = "partial_completion"
            if not record.get("last_error"):
                record["last_error"] = (
                    "Audio generation ended without completing every planned chunk."
                )
        record.update(
            {
                "state": state,
                "owner_token": None,
                "owner_process_id": None,
                "updated_at": now,
                "finished_at": now,
                "terminal_reason": reason,
                "terminal_summary": summary,
            }
        )
        record["terminal_receipt_fingerprint"] = fingerprint_value(
            {
                "request_id": record["request_id"],
                "request_fingerprint": record["request_fingerprint"],
                "state": state,
                "reason": reason,
                "summary": summary,
                "finished_at": now,
                "chunk_artifacts": {
                    key: value.get("canonical_artifact")
                    for key, value in record["progress"].items()
                },
            }
        )
        return _write_record(request_path(project_root, request_id), record)


def reconcile_interrupted_requests(
    project_root: str | Path,
    *,
    at_utc: str | None = None,
) -> list[dict[str, Any]]:
    now = at_utc or utc_now()
    changed = []
    with _LIFECYCLE_LOCK:
        for record in list_requests(project_root):
            if record["state"] in {"prepared", "running"}:
                record["state"] = "resumable"
                record["interrupted_at"] = now
                record["updated_at"] = now
                record["owner_token"] = None
                record["owner_process_id"] = None
                for chunk in record["progress"].values():
                    if chunk["state"] == "running":
                        chunk["state"] = "pending"
                    for segment in chunk["segments"].values():
                        if segment["state"] == "running":
                            segment["state"] = "pending"
                changed.append(
                    _write_record(
                        request_path(project_root, record["request_id"]),
                        record,
                    )
                )
            elif record["state"] == "cancelling":
                record["cancel_requested"] = True
                record["state"] = (
                    "replaced" if record.get("replacement_request_id") else "cancelled"
                )
                record["finished_at"] = now
                record["updated_at"] = now
                record["owner_token"] = None
                record["owner_process_id"] = None
                for chunk in record["progress"].values():
                    if chunk["state"] in {"pending", "running"}:
                        chunk["state"] = "cancelled"
                        chunk["finished_at"] = now
                record["terminal_summary"] = _terminal_summary(record)
                record["terminal_receipt_fingerprint"] = fingerprint_value(
                    {
                        "request_id": record["request_id"],
                        "request_fingerprint": record["request_fingerprint"],
                        "state": record["state"],
                        "summary": record["terminal_summary"],
                        "finished_at": now,
                    }
                )
                changed.append(
                    _write_record(
                        request_path(project_root, record["request_id"]),
                        record,
                    )
                )
    return changed


def pending_replacement(
    project_root: str | Path,
    predecessor_request_id: str,
) -> dict[str, Any] | None:
    predecessor = load_request(project_root, predecessor_request_id)
    replacement_id = predecessor.get("replacement_request_id")
    if not replacement_id:
        return None
    replacement = load_request(project_root, str(replacement_id))
    if replacement["state"] == "queued_replacement" and predecessor["state"] in TERMINAL_STATES:
        return replacement
    return None

