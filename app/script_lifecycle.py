from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from audio_artifacts import (
    AudioArtifactError,
    audio_backup_map,
    backup_operation_audio,
    restore_operation_audio,
)
from audio_invalidation import build_audio_validity_record
from generation_state import atomic_json_write, fingerprint_text, fingerprint_value
from project import group_into_chunks
from script_audit import audit_script_chunk


SCRIPT_LIFECYCLE_SCHEMA_VERSION = 1
SCRIPT_VERSION_SCHEMA_VERSION = 1
SCRIPT_LIFECYCLE_FILENAME = "script_lifecycle.json"
SCRIPT_VERSIONS_DIRNAME = "script_versions"
SCRIPT_LIFECYCLE_HISTORY_DIRNAME = "script_lifecycle_history"
SCRIPT_GENERATION_METHODS = frozenset(
    {"local", "chatgpt_task_bundle", "import_existing_script"}
)
SCRIPT_REVIEW_STATES = frozenset(
    {"not_started", "running", "resumable", "review_required", "blocked", "accepted", "stale", "failed"}
)
SCRIPT_DISCOVERY_HANDOFF_STATES = frozenset(
    {"not_eligible", "pending", "running", "resumable", "complete", "failed", "not_required"}
)
_SAFE_ID = re.compile(r"^[a-z][a-z0-9_\-]{7,127}$")
_LOCK = threading.RLock()


class ScriptLifecycleError(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        detail: str,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.context = dict(context or {})

    def as_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.detail,
            "context": self.context,
        }


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _safe_id(value: Any, field: str) -> str:
    text = _text(value)
    if text is None or not _SAFE_ID.fullmatch(text):
        raise ScriptLifecycleError(
            status_code=422,
            code=f"invalid_{field}",
            detail=f"{field.replace('_', ' ').title()} is invalid.",
        )
    return text


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "sha256": None, "content_base64": None}
    if not path.is_file():
        raise ScriptLifecycleError(
            status_code=409,
            code="script_artifact_not_file",
            detail=f"{path.name} is not a regular file.",
        )
    content = path.read_bytes()
    return {
        "exists": True,
        "sha256": _sha256_bytes(content),
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def _restore_snapshot(path: Path, snapshot: Mapping[str, Any]) -> None:
    if snapshot.get("exists") is not True:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    encoded = _text(snapshot.get("content_base64"))
    if encoded is None:
        raise ScriptLifecycleError(
            status_code=409,
            code="script_history_incomplete",
            detail=f"Stored history for {path.name} is incomplete.",
        )
    _atomic_bytes_write(base64.b64decode(encoded), path)


def _atomic_bytes_write(content: bytes, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_json(path: Path, *, required: bool = False) -> Any:
    if not path.exists():
        if required:
            raise ScriptLifecycleError(
                status_code=409,
                code="script_artifact_missing",
                detail=f"{path.name} is missing.",
            )
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ScriptLifecycleError(
            status_code=409,
            code="script_artifact_invalid_json",
            detail=f"{path.name} is invalid JSON: {exc}",
        ) from exc


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCRIPT_LIFECYCLE_SCHEMA_VERSION,
        "updated_at_utc": None,
        "review": {
            "status": "review_required",
            "script_fingerprint": None,
            "reviewed_at_utc": None,
            "reason": None,
        },
        "accepted_version_id": None,
        "versions": [],
        "discovery_handoff": {
            "status": "not_eligible",
            "accepted_version_id": None,
            "attempt_count": 0,
            "updated_at_utc": None,
            "last_error": None,
        },
        "history": [],
    }


def _state_fingerprint(state: Mapping[str, Any]) -> str:
    normalized = copy.deepcopy(dict(state))
    normalized.pop("state_fingerprint", None)
    return fingerprint_value(normalized)


def _normalize_state(value: Any) -> dict[str, Any]:
    if value is None:
        state = _empty_state()
        state["state_fingerprint"] = _state_fingerprint(state)
        return state
    if not isinstance(value, Mapping):
        raise ScriptLifecycleError(
            status_code=409,
            code="script_lifecycle_invalid",
            detail="script_lifecycle.json must contain a JSON object.",
        )
    if value.get("schema_version") != SCRIPT_LIFECYCLE_SCHEMA_VERSION:
        raise ScriptLifecycleError(
            status_code=409,
            code="script_lifecycle_version_unsupported",
            detail="The Script lifecycle schema version is unsupported.",
            context={"schema_version": value.get("schema_version")},
        )
    versions: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    for index, item_value in enumerate(_list(value.get("versions"))):
        item = dict(_mapping(item_value))
        version_id = _text(item.get("version_id"))
        if version_id is None or version_id in identifiers:
            raise ScriptLifecycleError(
                status_code=409,
                code="script_lifecycle_versions_invalid",
                detail=f"Script lifecycle version entry {index} is invalid.",
            )
        identifiers.add(version_id)
        versions.append(item)
    review = dict(_mapping(value.get("review")))
    discovery = dict(_mapping(value.get("discovery_handoff")))
    if _text(review.get("status")) not in {"review_required", "accepted", "rejected"}:
        raise ScriptLifecycleError(
            status_code=409,
            code="script_lifecycle_review_invalid",
            detail="The Script lifecycle review state is invalid.",
        )
    if _text(discovery.get("status")) not in SCRIPT_DISCOVERY_HANDOFF_STATES:
        raise ScriptLifecycleError(
            status_code=409,
            code="script_lifecycle_handoff_invalid",
            detail="The Script discovery handoff state is invalid.",
        )
    state = {
        "schema_version": SCRIPT_LIFECYCLE_SCHEMA_VERSION,
        "updated_at_utc": _text(value.get("updated_at_utc")),
        "review": review,
        "accepted_version_id": _text(value.get("accepted_version_id")),
        "versions": versions,
        "discovery_handoff": discovery,
        "history": [
            dict(item)
            for item in _list(value.get("history"))
            if isinstance(item, Mapping)
        ],
    }
    expected = _text(value.get("state_fingerprint"))
    actual = _state_fingerprint(state)
    if expected is not None and expected != actual:
        raise ScriptLifecycleError(
            status_code=409,
            code="script_lifecycle_fingerprint_invalid",
            detail="The Script lifecycle state fingerprint does not match its content.",
            context={"actual_state_fingerprint": actual},
        )
    state["state_fingerprint"] = actual
    return state


def load_script_lifecycle(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    return _normalize_state(_read_json(target))


def _write_state(state: Mapping[str, Any], path: Path) -> dict[str, Any]:
    normalized = _normalize_state(
        {
            **dict(state),
            "schema_version": SCRIPT_LIFECYCLE_SCHEMA_VERSION,
            "state_fingerprint": None,
        }
    )
    normalized["state_fingerprint"] = _state_fingerprint(normalized)
    atomic_json_write(normalized, path)
    return normalized


def _assert_state_fingerprint(
    state: Mapping[str, Any],
    expected_state_fingerprint: str | None,
) -> None:
    if expected_state_fingerprint is None:
        return
    current = _text(state.get("state_fingerprint")) or _state_fingerprint(state)
    if expected_state_fingerprint != current:
        raise ScriptLifecycleError(
            status_code=409,
            code="stale_script_lifecycle",
            detail="The Script lifecycle changed after this view was loaded.",
            context={"current_state_fingerprint": current},
        )


def _validate_entries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ScriptLifecycleError(
            status_code=409,
            code="script_structure_invalid",
            detail="The authoritative Script must be a non-empty JSON array.",
        )
    entries: list[dict[str, Any]] = []
    for index, item_value in enumerate(value):
        item = _mapping(item_value)
        if not all(isinstance(item.get(field), str) for field in ("speaker", "text", "instruct")):
            raise ScriptLifecycleError(
                status_code=409,
                code="script_structure_invalid",
                detail=f"Script entry {index} must contain string speaker, text, and instruct fields.",
                context={"entry_index": index},
            )
        if not _text(item.get("speaker")) or not _text(item.get("text")):
            raise ScriptLifecycleError(
                status_code=409,
                code="script_structure_invalid",
                detail=f"Script entry {index} has an empty speaker or text field.",
                context={"entry_index": index},
            )
        entries.append(dict(item))
    return entries


def _detect_generation_method(metadata: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    generation = _mapping(metadata.get("generation"))
    identity = _mapping(generation.get("effective_identity"))
    import_value = _mapping(metadata.get("import"))
    origin = _mapping(import_value.get("origin"))
    provenance = _mapping(import_value.get("provenance"))
    mode = _text(identity.get("mode"))
    origin_type = _text(identity.get("origin_type")) or _text(origin.get("type"))
    if mode == "external_import":
        if origin_type in {
            "chatgpt_handoff_result",
            "task_bundle_result",
            "completed_task_bundle",
        }:
            method = "chatgpt_task_bundle"
        else:
            method = "import_existing_script"
    else:
        method = "local"
    if method not in SCRIPT_GENERATION_METHODS:
        raise ScriptLifecycleError(
            status_code=409,
            code="script_generation_method_invalid",
            detail="The Script generation method could not be resolved.",
        )
    return method, {
        "method": method,
        "mode": mode,
        "backend": identity.get("backend"),
        "model_name": identity.get("model_name"),
        "origin_type": origin_type,
        "candidate_id": import_value.get("candidate_id"),
        "operation_id": import_value.get("operation_id"),
        "provenance_status": (
            provenance.get("status")
            or identity.get("provenance_status")
            or _mapping(metadata.get("source")).get("verification_status")
        ),
    }


def _metadata_fingerprints(
    metadata: Mapping[str, Any],
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    script_fingerprint = fingerprint_value(entries)
    result = _mapping(metadata.get("result"))
    metadata_script_fingerprint = _text(result.get("script_fingerprint"))
    if metadata_script_fingerprint and metadata_script_fingerprint != script_fingerprint:
        raise ScriptLifecycleError(
            status_code=409,
            code="script_metadata_fingerprint_mismatch",
            detail="The Script metadata fingerprint does not match annotated_script.json.",
            context={
                "script_fingerprint": script_fingerprint,
                "metadata_script_fingerprint": metadata_script_fingerprint,
            },
        )
    return {
        "script": script_fingerprint,
        "metadata": fingerprint_value(dict(metadata)),
        "generation": _text(_mapping(metadata.get("generation")).get("fingerprint")),
        "metadata_source": _text(_mapping(metadata.get("source")).get("fingerprint")),
    }


def _audit_source(
    source_text: str,
    entries: list[dict[str, Any]],
    *,
    allow_reviewed_differences: bool = False,
    expected_audit_fingerprint: str | None = None,
) -> dict[str, Any]:
    try:
        audit = audit_script_chunk(source_text, entries).to_dict()
    except Exception as exc:
        raise ScriptLifecycleError(
            status_code=409,
            code="script_audit_failed",
            detail=f"The Script audit could not run: {type(exc).__name__}: {exc}",
        ) from exc
    audit_fingerprint = fingerprint_value(audit)
    audit["audit_fingerprint"] = audit_fingerprint
    if audit.get("passed") is not True:
        blocking = [
            item
            for item in _list(audit.get("issues"))
            if isinstance(item, Mapping) and item.get("severity") == "blocking"
        ]
        if not allow_reviewed_differences:
            raise ScriptLifecycleError(
                status_code=409,
                code="script_acceptance_blocked",
                detail="The Script differs from the selected source or has speaker-attribution conflicts.",
                context={
                    "blocking_count": int(audit.get("blocking_count") or len(blocking)),
                    "blocking_issues": [dict(item) for item in blocking[:50]],
                    "metrics": dict(_mapping(audit.get("metrics"))),
                    "audit_fingerprint": audit_fingerprint,
                    "reviewed_override_available": True,
                },
            )
        if expected_audit_fingerprint != audit_fingerprint:
            raise ScriptLifecycleError(
                status_code=409,
                code="script_review_override_stale",
                detail="The Script or source differences changed after review.",
                context={"current_audit_fingerprint": audit_fingerprint},
            )
        audit["reviewed_override"] = True
    else:
        audit["reviewed_override"] = False
    return audit


def _version_receipt_fingerprint(receipt: Mapping[str, Any]) -> str:
    normalized = copy.deepcopy(dict(receipt))
    normalized.pop("receipt_fingerprint", None)
    return fingerprint_value(normalized)


def _version_paths(root: Path, version_id: str) -> dict[str, Path]:
    directory = root / SCRIPT_VERSIONS_DIRNAME / version_id
    return {
        "directory": directory,
        "script": directory / "annotated_script.json",
        "metadata": directory / "annotated_script.meta.json",
        "receipt": directory / "receipt.json",
    }


def _load_version(root: Path, version_id: str) -> tuple[dict[str, Any], dict[str, Path]]:
    safe = _safe_id(version_id, "version_id")
    paths = _version_paths(root, safe)
    receipt_value = _read_json(paths["receipt"], required=True)
    if not isinstance(receipt_value, Mapping):
        raise ScriptLifecycleError(
            status_code=409,
            code="script_version_invalid",
            detail="The Script version receipt is invalid.",
        )
    receipt = dict(receipt_value)
    if (
        receipt.get("schema_version") != SCRIPT_VERSION_SCHEMA_VERSION
        or receipt.get("version_id") != safe
    ):
        raise ScriptLifecycleError(
            status_code=409,
            code="script_version_invalid",
            detail="The Script version receipt is incompatible.",
        )
    expected = _text(receipt.get("receipt_fingerprint"))
    actual = _version_receipt_fingerprint(receipt)
    if expected != actual:
        raise ScriptLifecycleError(
            status_code=409,
            code="script_version_receipt_invalid",
            detail="The Script version receipt fingerprint is invalid.",
            context={"actual_receipt_fingerprint": actual},
        )
    for field in ("script", "metadata"):
        path = paths[field]
        if not path.is_file():
            raise ScriptLifecycleError(
                status_code=409,
                code="script_version_snapshot_missing",
                detail=f"The Script version snapshot is missing {path.name}.",
            )
        expected_hash = _text(_mapping(receipt.get("snapshot"))[f"{field}_sha256"])
        actual_hash = _sha256_bytes(path.read_bytes())
        if expected_hash != actual_hash:
            raise ScriptLifecycleError(
                status_code=409,
                code="script_version_snapshot_invalid",
                detail=f"The Script version snapshot changed: {path.name}.",
            )
    return receipt, paths


def _public_version(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "version_id": value.get("version_id"),
        "accepted_at_utc": value.get("accepted_at_utc"),
        "generation_method": value.get("generation_method"),
        "script_fingerprint": value.get("script_fingerprint"),
        "source_fingerprint": value.get("source_fingerprint"),
        "metadata_fingerprint": value.get("metadata_fingerprint"),
        "generation_fingerprint": value.get("generation_fingerprint"),
        "provenance_status": value.get("provenance_status"),
        "receipt_fingerprint": value.get("receipt_fingerprint"),
        "origin": copy.deepcopy(value.get("origin") or {}),
        "audit": copy.deepcopy(value.get("audit") or {}),
    }


def _current_artifacts(
    *,
    script_path: Path,
    metadata_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any], bytes, bytes]:
    script_bytes = script_path.read_bytes() if script_path.is_file() else b""
    metadata_bytes = metadata_path.read_bytes() if metadata_path.is_file() else b""
    entries = _validate_entries(_read_json(script_path, required=True))
    metadata_value = _read_json(metadata_path, required=True)
    if not isinstance(metadata_value, Mapping):
        raise ScriptLifecycleError(
            status_code=409,
            code="script_metadata_invalid",
            detail="annotated_script.meta.json must contain a JSON object.",
        )
    return entries, dict(metadata_value), script_bytes, metadata_bytes


def _process_contract(generation_status: Mapping[str, Any]) -> dict[str, Any]:
    process = dict(_mapping(generation_status.get("process")))
    checkpoint = _mapping(generation_status.get("checkpoint"))
    raw_checkpoint = _text(checkpoint.get("status")) or "none"
    if process.get("running"):
        state = "running"
        action = None
    elif checkpoint.get("resumable") is True or raw_checkpoint in {"compatible", "finalization_pending"}:
        state = "resumable"
        action = {
            "id": "resume_script_generation",
            "label": "Resume generation",
            "native_destination": "script",
            "target_id": "script:generation",
            "endpoint": "/api/generate_script",
        }
    else:
        state = "idle"
        action = None
    return {
        "state": state,
        "running": bool(process.get("running")),
        "resumable": state == "resumable",
        "checkpoint_status": raw_checkpoint,
        "safe_action": action,
        "process": process,
    }


def inspect_script_lifecycle(
    *,
    root_dir: str | Path,
    script_path: str | Path,
    metadata_path: str | Path,
    lifecycle_path: str | Path,
    generation_status: Mapping[str, Any],
    source_fingerprint: str | None,
    source_available: bool,
    import_candidate_count: int = 0,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    script = Path(script_path)
    metadata = Path(metadata_path)
    lifecycle = Path(lifecycle_path)
    state = load_script_lifecycle(lifecycle)
    process = _process_contract(generation_status)
    result = _mapping(generation_status.get("result"))
    script_exists = script.is_file()
    metadata_exists = metadata.is_file()
    blockers: list[dict[str, Any]] = []
    method = None
    provenance = None
    fingerprints = {
        "source": source_fingerprint,
        "script": None,
        "metadata": None,
        "generation": None,
        "accepted_receipt": None,
    }
    accepted_current = False
    accepted_version = None
    artifact_error = None
    artifact_entry_count = 0

    if script_exists and metadata_exists:
        try:
            entries, metadata_value, _, _ = _current_artifacts(
                script_path=script,
                metadata_path=metadata,
            )
            method, provenance = _detect_generation_method(metadata_value)
            artifact_entry_count = len(entries)
            fingerprints.update(_metadata_fingerprints(metadata_value, entries))
        except ScriptLifecycleError as exc:
            artifact_error = exc
            blockers.append(
                {
                    "code": exc.code,
                    "title": "Script artifacts are invalid",
                    "explanation": exc.detail,
                    "native_destination": "script",
                    "target_id": "script:review",
                    "blocking": True,
                    "context": exc.context,
                }
            )

    accepted_id = _text(state.get("accepted_version_id"))
    if accepted_id is not None:
        matching = next(
            (
                item
                for item in _list(state.get("versions"))
                if item.get("version_id") == accepted_id
            ),
            None,
        )
        if isinstance(matching, Mapping):
            accepted_version = dict(matching)
            fingerprints["accepted_receipt"] = matching.get("receipt_fingerprint")
            receipt_valid = True
            try:
                receipt, _ = _load_version(root, accepted_id)
                receipt_valid = bool(
                    receipt.get("receipt_fingerprint")
                    == matching.get("receipt_fingerprint")
                    and receipt.get("script_fingerprint")
                    == matching.get("script_fingerprint")
                    and receipt.get("metadata_fingerprint")
                    == matching.get("metadata_fingerprint")
                    and receipt.get("source_fingerprint")
                    == matching.get("source_fingerprint")
                )
            except ScriptLifecycleError as exc:
                receipt_valid = False
                blockers.append(
                    {
                        "code": exc.code,
                        "title": "Accepted Script receipt is invalid",
                        "explanation": exc.detail,
                        "native_destination": "maintenance",
                        "target_id": f"script-version:{accepted_id}",
                        "blocking": True,
                        "context": exc.context,
                    }
                )
            accepted_current = bool(
                receipt_valid
                and artifact_error is None
                and fingerprints.get("script") == matching.get("script_fingerprint")
                and fingerprints.get("metadata") == matching.get("metadata_fingerprint")
                and source_fingerprint == matching.get("source_fingerprint")
                and _mapping(state.get("review")).get("status") == "accepted"
            )

    if process["running"]:
        lifecycle_state = "running"
        primary_action = None
        summary = "Script generation is running."
    elif process["resumable"]:
        lifecycle_state = "resumable"
        primary_action = process["safe_action"]
        summary = "Saved Script generation can be resumed."
    elif not source_available:
        lifecycle_state = "blocked"
        primary_action = {
            "id": "select_project_source",
            "label": "Select source",
            "native_destination": "projects",
            "target_id": "project:source",
        }
        summary = "Script is blocked until a readable source is selected."
        blockers.append(
            {
                "code": "script_source_unavailable",
                "title": "Source is unavailable",
                "explanation": "Select a readable source before generating or accepting a Script.",
                "native_destination": "projects",
                "target_id": "project:source",
                "blocking": True,
            }
        )
    elif artifact_error is not None:
        lifecycle_state = "blocked"
        primary_action = {
            "id": "review_script",
            "label": "Review Script",
            "native_destination": "script",
            "target_id": "script:review",
        }
        summary = "Script artifacts require correction."
    elif not script_exists:
        if import_candidate_count > 0:
            lifecycle_state = "review_required"
            primary_action = {
                "id": "review_imported_script",
                "label": "Review imported Script",
                "native_destination": "script",
                "target_id": "script:import-review",
            }
            summary = "An imported Script candidate is ready for review."
        else:
            lifecycle_state = "not_started"
            primary_action = {
                "id": "generate_script",
                "label": "Generate Script",
                "native_destination": "script",
                "target_id": "script:generation",
                "endpoint": "/api/generate_script",
            }
            summary = "No authoritative Script exists yet."
    elif accepted_current:
        lifecycle_state = "accepted"
        primary_action = {
            "id": "open_cast",
            "label": "Open Cast",
            "native_destination": "cast",
            "target_id": None,
        }
        summary = "The current Script is validated and explicitly accepted."
    elif accepted_version is not None:
        lifecycle_state = "stale"
        primary_action = {
            "id": "review_script",
            "label": "Review Script",
            "native_destination": "script",
            "target_id": "script:review",
        }
        summary = "The current Script differs from the accepted Script version."
        blockers.append(
            {
                "code": "script_acceptance_stale",
                "title": "Script acceptance is stale",
                "explanation": "Review and accept the current Script because its bytes or source dependency changed.",
                "native_destination": "script",
                "target_id": "script:review",
                "blocking": True,
                "dependency_fingerprint": accepted_version.get("receipt_fingerprint"),
            }
        )
    else:
        lifecycle_state = "review_required"
        primary_action = {
            "id": "review_script",
            "label": "Review Script",
            "native_destination": "script",
            "target_id": "script:review",
        }
        summary = "A Script is available and requires validation and acceptance."

    if lifecycle_state not in SCRIPT_REVIEW_STATES:
        raise ScriptLifecycleError(
            status_code=500,
            code="script_lifecycle_state_invalid",
            detail=f"Derived unsupported Script lifecycle state: {lifecycle_state}",
        )
    discovery = dict(_mapping(state.get("discovery_handoff")))
    return {
        "schema_version": SCRIPT_LIFECYCLE_SCHEMA_VERSION,
        "state": lifecycle_state,
        "summary": summary,
        "generation_method": method,
        "provenance": provenance,
        "source_available": bool(source_available),
        "artifact": {
            "script_exists": script_exists,
            "metadata_exists": metadata_exists,
            "entry_count": artifact_entry_count,
        },
        "fingerprints": fingerprints,
        "accepted": accepted_current,
        "accepted_version_id": accepted_id if accepted_current else None,
        "accepted_version": _public_version(accepted_version) if accepted_version else None,
        "review": dict(_mapping(state.get("review"))),
        "primary_action": primary_action,
        "blockers": blockers,
        "blocker_count": sum(bool(item.get("blocking")) for item in blockers),
        "process": process,
        "discovery_handoff": discovery,
        "character_discovery_eligible": bool(
            accepted_current
            and discovery.get("accepted_version_id") == accepted_id
            and discovery.get("status") in {
                "pending",
                "running",
                "resumable",
                "complete",
                "not_required",
                "failed",
            }
        ),
        "versions": [_public_version(item) for item in reversed(_list(state.get("versions")))],
        "state_fingerprint": state.get("state_fingerprint"),
        "technical_details": {
            "project_path": str(root),
            "lifecycle_path": str(lifecycle),
        },
    }


def accept_current_script(
    *,
    root_dir: str | Path,
    script_path: str | Path,
    metadata_path: str | Path,
    lifecycle_path: str | Path,
    source_text: str,
    source_fingerprint: str,
    expected_script_fingerprint: str,
    expected_metadata_fingerprint: str,
    expected_source_fingerprint: str,
    expected_state_fingerprint: str | None = None,
    allow_reviewed_source_differences: bool = False,
    expected_audit_fingerprint: str | None = None,
    origin: Mapping[str, Any] | None = None,
    at_utc: str | None = None,
) -> dict[str, Any]:
    if source_fingerprint != expected_source_fingerprint:
        raise ScriptLifecycleError(
            status_code=409,
            code="stale_script_source",
            detail="The selected source changed before Script acceptance.",
            context={"current_source_fingerprint": source_fingerprint},
        )
    root = Path(root_dir).expanduser().resolve()
    script = Path(script_path)
    metadata = Path(metadata_path)
    lifecycle = Path(lifecycle_path)
    now = at_utc or utc_timestamp()
    with _LOCK:
        state = load_script_lifecycle(lifecycle)
        _assert_state_fingerprint(state, expected_state_fingerprint)
        entries, metadata_value, script_bytes, metadata_bytes = _current_artifacts(
            script_path=script,
            metadata_path=metadata,
        )
        fingerprints = _metadata_fingerprints(metadata_value, entries)
        if fingerprints["script"] != expected_script_fingerprint:
            raise ScriptLifecycleError(
                status_code=409,
                code="stale_script_review",
                detail="The Script changed before acceptance.",
                context={"current_script_fingerprint": fingerprints["script"]},
            )
        if fingerprints["metadata"] != expected_metadata_fingerprint:
            raise ScriptLifecycleError(
                status_code=409,
                code="stale_script_metadata",
                detail="The Script metadata changed before acceptance.",
                context={"current_metadata_fingerprint": fingerprints["metadata"]},
            )
        metadata_source = fingerprints.get("metadata_source")
        if metadata_source and metadata_source != source_fingerprint:
            raise ScriptLifecycleError(
                status_code=409,
                code="script_source_fingerprint_mismatch",
                detail="The Script metadata belongs to a different source.",
                context={
                    "metadata_source_fingerprint": metadata_source,
                    "current_source_fingerprint": source_fingerprint,
                },
            )
        method, provenance = _detect_generation_method(metadata_value)
        if allow_reviewed_source_differences and method not in {
            "import_existing_script",
            "chatgpt_task_bundle",
        }:
            raise ScriptLifecycleError(
                status_code=422,
                code="script_review_override_not_allowed",
                detail="Reviewed source differences may only be accepted for an imported Script.",
            )
        audit = _audit_source(
            source_text,
            entries,
            allow_reviewed_differences=allow_reviewed_source_differences,
            expected_audit_fingerprint=expected_audit_fingerprint,
        )
        existing = next(
            (
                item
                for item in _list(state.get("versions"))
                if item.get("script_fingerprint") == fingerprints["script"]
                and item.get("metadata_fingerprint") == fingerprints["metadata"]
                and item.get("source_fingerprint") == source_fingerprint
            ),
            None,
        )
        if isinstance(existing, Mapping):
            version_id = str(existing["version_id"])
            stored_receipt, _ = _load_version(root, version_id)
            if (
                stored_receipt.get("receipt_fingerprint")
                != existing.get("receipt_fingerprint")
                or stored_receipt.get("script_fingerprint")
                != fingerprints["script"]
                or stored_receipt.get("metadata_fingerprint")
                != fingerprints["metadata"]
                or stored_receipt.get("source_fingerprint")
                != source_fingerprint
            ):
                raise ScriptLifecycleError(
                    status_code=409,
                    code="script_version_receipt_mismatch",
                    detail="The matching accepted Script version receipt is inconsistent with the current artifacts.",
                )
            updated = copy.deepcopy(state)
            updated["updated_at_utc"] = now
            updated["accepted_version_id"] = version_id
            updated["review"] = {
                "status": "accepted",
                "script_fingerprint": fingerprints["script"],
                "reviewed_at_utc": now,
                "reason": None,
            }
            previous_handoff = dict(_mapping(state.get("discovery_handoff")))
            updated["discovery_handoff"] = (
                previous_handoff
                if previous_handoff.get("accepted_version_id") == version_id
                and previous_handoff.get("status") in SCRIPT_DISCOVERY_HANDOFF_STATES
                else {
                    "status": "pending",
                    "accepted_version_id": version_id,
                    "attempt_count": 0,
                    "updated_at_utc": now,
                    "last_error": None,
                }
            )
            updated_state = _write_state(updated, lifecycle)
            return {
                "status": "accepted",
                "idempotent": True,
                "version": _public_version(existing),
                "state_fingerprint": updated_state["state_fingerprint"],
                "discovery_handoff": updated_state["discovery_handoff"],
            }

        version_id = "script_version_" + fingerprint_value(
            {
                "script": fingerprints["script"],
                "metadata": fingerprints["metadata"],
                "source": source_fingerprint,
                "accepted_at_utc": now,
                "nonce": secrets.token_hex(8),
            }
        )[:24]
        paths = _version_paths(root, version_id)
        versions_root = paths["directory"].parent
        versions_root.mkdir(parents=True, exist_ok=True)
        staging = versions_root / f".{version_id}.pending-{secrets.token_hex(4)}"
        receipt = {
            "schema_version": SCRIPT_VERSION_SCHEMA_VERSION,
            "version_id": version_id,
            "accepted_at_utc": now,
            "generation_method": method,
            "script_fingerprint": fingerprints["script"],
            "source_fingerprint": source_fingerprint,
            "metadata_fingerprint": fingerprints["metadata"],
            "generation_fingerprint": fingerprints["generation"],
            "provenance_status": (
                "reviewed_source_differences"
                if audit.get("reviewed_override")
                else "verified_at_acceptance"
            ),
            "provenance": provenance,
            "origin": copy.deepcopy(dict(origin or {})),
            "audit": {
                "passed": audit.get("passed") is True,
                "reviewed_override": bool(audit.get("reviewed_override")),
                "audit_fingerprint": audit.get("audit_fingerprint"),
                "blocking_count": int(audit.get("blocking_count") or 0),
                "warning_count": int(audit.get("warning_count") or 0),
                "metrics": copy.deepcopy(dict(_mapping(audit.get("metrics")))),
                "reviewed_issues": [
                    copy.deepcopy(dict(item))
                    for item in _list(audit.get("issues"))[:50]
                ] if audit.get("reviewed_override") else [],
            },
            "snapshot": {
                "script_sha256": _sha256_bytes(script_bytes),
                "metadata_sha256": _sha256_bytes(metadata_bytes),
            },
        }
        receipt["receipt_fingerprint"] = _version_receipt_fingerprint(receipt)
        public_version = {
            "version_id": version_id,
            "accepted_at_utc": now,
            "generation_method": method,
            "script_fingerprint": fingerprints["script"],
            "source_fingerprint": source_fingerprint,
            "metadata_fingerprint": fingerprints["metadata"],
            "generation_fingerprint": fingerprints["generation"],
            "provenance_status": receipt["provenance_status"],
            "receipt_fingerprint": receipt["receipt_fingerprint"],
            "origin": copy.deepcopy(dict(origin or {})),
            "audit": copy.deepcopy(receipt["audit"]),
        }
        published = False
        try:
            staging.mkdir()
            _atomic_bytes_write(script_bytes, staging / "annotated_script.json")
            _atomic_bytes_write(metadata_bytes, staging / "annotated_script.meta.json")
            atomic_json_write(receipt, staging / "receipt.json")
            staged_receipt = json.loads((staging / "receipt.json").read_text(encoding="utf-8"))
            if _version_receipt_fingerprint(staged_receipt) != staged_receipt.get("receipt_fingerprint"):
                raise ScriptLifecycleError(
                    status_code=500,
                    code="script_version_staging_invalid",
                    detail="The staged Script version receipt failed validation.",
                )
            if _sha256_bytes((staging / "annotated_script.json").read_bytes()) != receipt["snapshot"]["script_sha256"]:
                raise ScriptLifecycleError(
                    status_code=500,
                    code="script_version_staging_invalid",
                    detail="The staged Script bytes failed validation.",
                )
            if _sha256_bytes((staging / "annotated_script.meta.json").read_bytes()) != receipt["snapshot"]["metadata_sha256"]:
                raise ScriptLifecycleError(
                    status_code=500,
                    code="script_version_staging_invalid",
                    detail="The staged Script metadata failed validation.",
                )
            os.replace(staging, paths["directory"])
            published = True
            updated = copy.deepcopy(state)
            updated["updated_at_utc"] = now
            updated["accepted_version_id"] = version_id
            updated["versions"] = _list(state.get("versions")) + [public_version]
            updated["review"] = {
                "status": "accepted",
                "script_fingerprint": fingerprints["script"],
                "reviewed_at_utc": now,
                "reason": (
                    "Reviewed source differences accepted"
                    if audit.get("reviewed_override")
                    else None
                ),
            }
            updated["discovery_handoff"] = {
                "status": "pending",
                "accepted_version_id": version_id,
                "attempt_count": 0,
                "updated_at_utc": now,
                "last_error": None,
            }
            updated["history"] = _list(state.get("history")) + [
                {
                    "event": "accepted",
                    "version_id": version_id,
                    "at_utc": now,
                    "script_fingerprint": fingerprints["script"],
                }
            ]
            updated_state = _write_state(updated, lifecycle)
        except Exception:
            if staging.exists():
                import shutil

                shutil.rmtree(staging, ignore_errors=True)
            if published and paths["directory"].exists():
                import shutil

                shutil.rmtree(paths["directory"], ignore_errors=True)
            raise
        return {
            "status": "accepted",
            "idempotent": False,
            "version": _public_version(public_version),
            "state_fingerprint": updated_state["state_fingerprint"],
            "discovery_handoff": updated_state["discovery_handoff"],
        }


def reject_current_script(
    *,
    lifecycle_path: str | Path,
    current_script_fingerprint: str,
    reason: str,
    expected_state_fingerprint: str | None = None,
    at_utc: str | None = None,
) -> dict[str, Any]:
    reason_value = _text(reason)
    if reason_value is None:
        raise ScriptLifecycleError(
            status_code=422,
            code="script_rejection_reason_required",
            detail="A rejection reason is required.",
        )
    if len(reason_value) > 2000:
        raise ScriptLifecycleError(
            status_code=422,
            code="script_rejection_reason_too_long",
            detail="The rejection reason must be 2,000 characters or fewer.",
        )
    lifecycle = Path(lifecycle_path)
    now = at_utc or utc_timestamp()
    with _LOCK:
        state = load_script_lifecycle(lifecycle)
        _assert_state_fingerprint(state, expected_state_fingerprint)
        updated = copy.deepcopy(state)
        updated["updated_at_utc"] = now
        updated["accepted_version_id"] = None
        updated["review"] = {
            "status": "rejected",
            "script_fingerprint": current_script_fingerprint,
            "reviewed_at_utc": now,
            "reason": reason_value,
        }
        updated["discovery_handoff"] = {
            "status": "not_eligible",
            "accepted_version_id": None,
            "attempt_count": 0,
            "updated_at_utc": now,
            "last_error": None,
        }
        updated["history"] = _list(state.get("history")) + [
            {
                "event": "rejected",
                "at_utc": now,
                "script_fingerprint": current_script_fingerprint,
                "reason": reason_value,
            }
        ]
        written = _write_state(updated, lifecycle)
    return {
        "status": "rejected",
        "review": written["review"],
        "state_fingerprint": written["state_fingerprint"],
    }


def mark_discovery_handoff(
    *,
    lifecycle_path: str | Path,
    accepted_version_id: str,
    status: str,
    error: str | None = None,
    expected_state_fingerprint: str | None = None,
    at_utc: str | None = None,
) -> dict[str, Any]:
    version_id = _safe_id(accepted_version_id, "accepted_version_id")
    if status not in SCRIPT_DISCOVERY_HANDOFF_STATES - {"not_eligible"}:
        raise ScriptLifecycleError(
            status_code=422,
            code="script_discovery_handoff_state_invalid",
            detail="The requested character-discovery handoff state is invalid.",
        )
    lifecycle = Path(lifecycle_path)
    now = at_utc or utc_timestamp()
    with _LOCK:
        state = load_script_lifecycle(lifecycle)
        _assert_state_fingerprint(state, expected_state_fingerprint)
        if state.get("accepted_version_id") != version_id:
            raise ScriptLifecycleError(
                status_code=409,
                code="stale_script_discovery_handoff",
                detail="The accepted Script version changed before the discovery handoff update.",
                context={"current_accepted_version_id": state.get("accepted_version_id")},
            )
        previous = _mapping(state.get("discovery_handoff"))
        updated = copy.deepcopy(state)
        updated["updated_at_utc"] = now
        updated["discovery_handoff"] = {
            "status": status,
            "accepted_version_id": version_id,
            "attempt_count": int(previous.get("attempt_count") or 0) + (1 if status in {"running", "failed"} else 0),
            "updated_at_utc": now,
            "last_error": _text(error),
        }
        updated["history"] = _list(state.get("history")) + [
            {
                "event": "character_discovery_handoff",
                "at_utc": now,
                "version_id": version_id,
                "status": status,
                "error": _text(error),
            }
        ]
        written = _write_state(updated, lifecycle)
    return {
        "status": status,
        "discovery_handoff": written["discovery_handoff"],
        "state_fingerprint": written["state_fingerprint"],
    }


def _pending_chunks(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chunks = group_into_chunks(entries)
    return [
        {
            "id": index,
            "speaker": chunk.get("speaker"),
            "text": chunk.get("text"),
            "instruct": chunk.get("instruct", ""),
            **(
                {"pause_after": chunk.get("pause_after")}
                if chunk.get("pause_after") is not None
                else {}
            ),
            "status": "pending",
            "audio_path": None,
            "audio_state": "missing",
            "audio_fingerprint": None,
            "audio_sha256": None,
            "audio_size_bytes": None,
            "audio_duration_ms": None,
            "audio_format": None,
        }
        for index, chunk in enumerate(chunks)
    ]


def rollback_script_version(
    *,
    root_dir: str | Path,
    script_path: str | Path,
    metadata_path: str | Path,
    chunks_path: str | Path,
    audio_validity_path: str | Path,
    lifecycle_path: str | Path,
    version_id: str,
    current_source_fingerprint: str,
    expected_current_script_fingerprint: str,
    expected_state_fingerprint: str | None,
    at_utc: str | None = None,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    script = Path(script_path).expanduser().resolve()
    metadata = Path(metadata_path).expanduser().resolve()
    chunks = Path(chunks_path).expanduser().resolve()
    validity = Path(audio_validity_path).expanduser().resolve()
    lifecycle = Path(lifecycle_path).expanduser().resolve()
    safe_version_id = _safe_id(version_id, "version_id")
    now = at_utc or utc_timestamp()
    with _LOCK:
        state = load_script_lifecycle(lifecycle)
        _assert_state_fingerprint(state, expected_state_fingerprint)
        current_entries = _validate_entries(_read_json(script, required=True))
        current_fingerprint = fingerprint_value(current_entries)
        if current_fingerprint != expected_current_script_fingerprint:
            raise ScriptLifecycleError(
                status_code=409,
                code="stale_script_rollback",
                detail="The current Script changed before rollback.",
                context={"current_script_fingerprint": current_fingerprint},
            )
        receipt, version_paths = _load_version(root, safe_version_id)
        if receipt.get("source_fingerprint") != current_source_fingerprint:
            raise ScriptLifecycleError(
                status_code=409,
                code="script_version_source_mismatch",
                detail="The selected Script version belongs to a different source.",
                context={
                    "version_source_fingerprint": receipt.get("source_fingerprint"),
                    "current_source_fingerprint": current_source_fingerprint,
                },
            )
        target_entries = _validate_entries(_read_json(version_paths["script"], required=True))
        target_metadata = _read_json(version_paths["metadata"], required=True)
        if not isinstance(target_metadata, Mapping):
            raise ScriptLifecycleError(
                status_code=409,
                code="script_version_metadata_invalid",
                detail="The selected Script version metadata is invalid.",
            )
        old_chunks = _read_json(chunks) or []
        if not isinstance(old_chunks, list):
            raise ScriptLifecycleError(
                status_code=409,
                code="chunks_invalid",
                detail="chunks.json must contain a JSON array before Script rollback.",
            )
        operation_id = "script_rollback_" + fingerprint_value(
            {
                "version_id": safe_version_id,
                "current_script_fingerprint": current_fingerprint,
                "at_utc": now,
                "nonce": secrets.token_hex(8),
            }
        )[:24]
        operation_dir = root / SCRIPT_LIFECYCLE_HISTORY_DIRNAME / operation_id
        touched = [script, metadata, chunks, validity, lifecycle]
        before = {
            path.relative_to(root).as_posix(): _snapshot(path)
            for path in touched
        }
        audio_paths = [
            chunk.get("audio_path")
            for chunk in old_chunks
            if isinstance(chunk, Mapping)
        ]
        audio_backups: list[dict[str, Any]] = []
        written: list[Path] = []
        try:
            audio_backups = backup_operation_audio(
                root_dir=root,
                operation_dir=operation_dir,
                relative_paths=audio_paths,
            )
            backup_by_path = audio_backup_map(audio_backups)
            invalidations = []
            for index, chunk_value in enumerate(old_chunks):
                chunk = _mapping(chunk_value)
                audio_path = _text(chunk.get("audio_path"))
                if chunk.get("status") != "done" and audio_path is None:
                    continue
                backup = backup_by_path.get(audio_path or "")
                invalidations.append(
                    {
                        "chunk_id": chunk.get("id", index),
                        "speaker": chunk.get("speaker"),
                        "canonical_audio_path": audio_path,
                        "backup_audio_path": backup.get("backup_path") if backup else None,
                        "audio_sha256": backup.get("sha256") if backup else None,
                        "audio_size_bytes": backup.get("size_bytes") if backup else None,
                        "reason": "script_version_rollback",
                    }
                )
            updated_state = copy.deepcopy(state)
            updated_state["updated_at_utc"] = now
            updated_state["accepted_version_id"] = safe_version_id
            updated_state["review"] = {
                "status": "accepted",
                "script_fingerprint": receipt["script_fingerprint"],
                "reviewed_at_utc": now,
                "reason": None,
            }
            updated_state["discovery_handoff"] = {
                "status": "pending",
                "accepted_version_id": safe_version_id,
                "attempt_count": 0,
                "updated_at_utc": now,
                "last_error": None,
            }
            updated_state["history"] = _list(state.get("history")) + [
                {
                    "event": "rolled_back",
                    "operation_id": operation_id,
                    "version_id": safe_version_id,
                    "at_utc": now,
                    "previous_script_fingerprint": current_fingerprint,
                    "result_script_fingerprint": receipt["script_fingerprint"],
                }
            ]
            _atomic_bytes_write(version_paths["script"].read_bytes(), script)
            written.append(script)
            _atomic_bytes_write(version_paths["metadata"].read_bytes(), metadata)
            written.append(metadata)
            atomic_json_write(_pending_chunks(target_entries), chunks)
            written.append(chunks)
            atomic_json_write(
                build_audio_validity_record(
                    operation_id=operation_id,
                    operation="script_version_rollback",
                    at_utc=now,
                    invalidations=invalidations,
                    default_reason="script_version_rollback",
                    note=(
                        "Prior production audio was moved to content-addressed rollback "
                        "backup and is not eligible for final output."
                    ),
                ),
                validity,
            )
            written.append(validity)
            written_state = _write_state(updated_state, lifecycle)
            written.append(lifecycle)
            after = {
                path.relative_to(root).as_posix(): _snapshot(path)
                for path in touched
            }
            operation_record = {
                "schema_version": 1,
                "operation_id": operation_id,
                "operation": "script_version_rollback",
                "at_utc": now,
                "version_id": safe_version_id,
                "source_script_fingerprint": current_fingerprint,
                "result_script_fingerprint": receipt["script_fingerprint"],
                "audio_backups": copy.deepcopy(audio_backups),
                "files": {
                    relative: {
                        "before": before[relative],
                        "after_sha256": after[relative]["sha256"],
                    }
                    for relative in before
                },
            }
            atomic_json_write(operation_record, operation_dir / "operation.json")
        except Exception:
            for path in reversed(written):
                relative = path.relative_to(root).as_posix()
                _restore_snapshot(path, before[relative])
            try:
                restore_operation_audio(
                    root_dir=root,
                    records=audio_backups,
                    require_original_absent=False,
                    consume_backups=True,
                )
            except Exception:
                pass
            raise
        return {
            "status": "rolled_back",
            "operation_id": operation_id,
            "version": _public_version(receipt),
            "invalidated_audio_count": len(invalidations),
            "audio_backup_count": len(audio_backups),
            "state_fingerprint": written_state["state_fingerprint"],
            "discovery_handoff": written_state["discovery_handoff"],
        }
