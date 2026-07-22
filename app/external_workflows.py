from __future__ import annotations

import base64
import copy
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from audio_artifacts import (
    AudioArtifactError,
    audio_backup_map,
    backup_operation_audio,
    consume_operation_audio_backups,
    remove_restored_operation_audio,
    restore_operation_audio,
    validate_operation_audio_backups,
)
from audio_invalidation import (
    AudioInvalidationError,
    apply_audio_invalidation_transaction,
    attach_audio_backup_evidence,
    build_audio_validity_record,
    undo_audio_invalidation_transaction,
)
from annotated_script_import import (
    AnnotatedScriptImportConflictError,
    AnnotatedScriptImportValidationError,
    build_annotated_script_import_plan,
    inspect_annotated_script_import,
)
from chatgpt_handoff import (
    ChatGPTHandoffError,
    HandoffConflictError,
    MAX_RESULT_BYTES,
    create_handoff_bundle,
    inspect_handoff_bundle,
    validate_handoff_result,
)
from generation_state import atomic_json_write, fingerprint_value
from project import group_into_chunks
from task_bundles import (
    TASK_REGISTRY,
    create_task_bundle,
    get_task_definition,
    get_task_transfer_contract,
    inspect_completed_task_bundle,
    inspect_result_envelope,
    inspect_task_bundle,
)
from voice_aliases import VoiceAliasError, validate_voice_aliases


WORKFLOW_SCHEMA_VERSION = 1
HISTORY_SCHEMA_VERSION = 1
WORKFLOW_DIRNAME = "external_workflows"
SAFE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{7,95}$")
_WORKFLOW_LOCK = threading.RLock()


class ExternalWorkflowError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = copy.deepcopy(details or {})

    @property
    def status_code(self) -> int:
        return 409 if isinstance(self, ExternalWorkflowConflictError) else 400

    def as_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "details": copy.deepcopy(self.details),
        }


class ExternalWorkflowValidationError(ExternalWorkflowError):
    pass


class ExternalWorkflowConflictError(ExternalWorkflowError):
    pass


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _workflow_root(root: Path) -> Path:
    return root / WORKFLOW_DIRNAME


def _handoffs_root(root: Path) -> Path:
    return _workflow_root(root) / "handoffs"


def _candidates_root(root: Path) -> Path:
    return _workflow_root(root) / "candidates"


def _tasks_root(root: Path) -> Path:
    return _workflow_root(root) / "tasks"


def _task_dir(root: Path, task_id: str) -> Path:
    return _tasks_root(root) / _safe_id(task_id, "task_id")


def _history_root(root: Path) -> Path:
    return _workflow_root(root) / "import_history"


def _safe_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SAFE_ID_PATTERN.fullmatch(value):
        raise ExternalWorkflowValidationError(
            "invalid_identifier",
            f"{field} is not a valid Alexandria workflow identifier.",
        )
    return value


def _candidate_path(root: Path, candidate_id: str) -> Path:
    return _candidates_root(root) / f"{_safe_id(candidate_id, 'candidate_id')}.json"


def _handoff_dir(root: Path, handoff_id: str) -> Path:
    return _handoffs_root(root) / _safe_id(handoff_id, "handoff_id")


def _read_json(path: Path, *, default: Any = None) -> Any:
    if not path.exists():
        return copy.deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExternalWorkflowValidationError(
            "invalid_stored_json",
            f"Could not read {path.name}: {exc}",
        ) from exc


def _json_fingerprint(path: Path) -> str | None:
    value = _read_json(path, default=None)
    if value is None:
        return None
    return fingerprint_value(value)


def _snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "exists": False,
            "sha256": None,
            "content_base64": None,
        }
    content = path.read_bytes()
    return {
        "exists": True,
        "sha256": __import__("hashlib").sha256(content).hexdigest(),
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def _restore_snapshot(path: Path, snapshot: dict[str, Any], suffix: str) -> None:
    if not snapshot.get("exists"):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    encoded = snapshot.get("content_base64")
    if not isinstance(encoded, str):
        raise ExternalWorkflowValidationError(
            "invalid_history",
            f"Stored backup for {path.name} is incomplete.",
        )
    content = base64.b64decode(encoded)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + suffix)
    temporary.write_bytes(content)
    os.replace(temporary, path)


def _public_candidate(record: dict[str, Any]) -> dict[str, Any]:
    candidate = record["candidate"]
    return {
        "schema_version": record["schema_version"],
        "candidate_id": record["candidate_id"],
        "kind": record["kind"],
        "status": record["status"],
        "created_at_utc": record["created_at_utc"],
        "origin": copy.deepcopy(record.get("origin") or {}),
        "summary": copy.deepcopy(candidate["summary"]),
        "provenance": copy.deepcopy(candidate["provenance"]),
        "warnings": copy.deepcopy(candidate["warnings"]),
        "snapshot": copy.deepcopy(candidate["snapshot"]),
        "consequences": copy.deepcopy(candidate["consequences"]),
        "comparison": copy.deepcopy(candidate.get("comparison")),
        "import_fingerprint": candidate["import_fingerprint"],
        "application": copy.deepcopy(record.get("application")),
    }


def _load_candidate_record(root: Path, candidate_id: str) -> tuple[Path, dict[str, Any]]:
    path = _candidate_path(root, candidate_id)
    record = _read_json(path, default=None)
    if not isinstance(record, dict):
        raise ExternalWorkflowValidationError(
            "candidate_not_found",
            f"Import candidate {candidate_id!r} was not found.",
        )
    if (
        record.get("schema_version") != WORKFLOW_SCHEMA_VERSION
        or record.get("candidate_id") != candidate_id
        or record.get("kind") != "annotated_script"
        or not isinstance(record.get("candidate"), dict)
    ):
        raise ExternalWorkflowValidationError(
            "invalid_candidate_record",
            "The stored import candidate is invalid.",
        )
    return path, record


def _public_structured_result(record: dict[str, Any]) -> dict[str, Any]:
    candidate = record["candidate"]
    return {
        "schema_version": record["schema_version"],
        "candidate_id": record["candidate_id"],
        "kind": record["kind"],
        "status": record["status"],
        "created_at_utc": record["created_at_utc"],
        "task_id": candidate.get("task_id"),
        "handoff_id": candidate.get("handoff_id"),
        "task_type": candidate["task_type"],
        "task_label": candidate.get("task_label"),
        "target": copy.deepcopy(candidate.get("target")),
        "result_fingerprint": candidate["result_fingerprint"],
        "review": copy.deepcopy(candidate["review"]),
        "result": copy.deepcopy(candidate["result"]),
        "snapshot": copy.deepcopy(candidate["snapshot"]),
        "manifest_fingerprint": candidate.get("manifest_fingerprint"),
        "guidance": copy.deepcopy(candidate.get("guidance") or {}),
        "native_transfer": get_task_transfer_contract(candidate["task_type"]),
        "application": copy.deepcopy(record.get("application")),
    }


def _load_structured_result_record(
    root: Path,
    candidate_id: str,
) -> tuple[Path, dict[str, Any]]:
    path = _candidate_path(root, candidate_id)
    record = _read_json(path, default=None)
    if not isinstance(record, dict):
        raise ExternalWorkflowValidationError(
            "structured_result_not_found",
            f"Structured result {candidate_id!r} was not found.",
        )
    candidate = record.get("candidate")
    if (
        record.get("schema_version") != WORKFLOW_SCHEMA_VERSION
        or record.get("candidate_id") != candidate_id
        or record.get("kind") != "structured_result"
        or not isinstance(candidate, dict)
        or candidate.get("task_type") not in TASK_REGISTRY
        or TASK_REGISTRY[candidate.get("task_type")].transfer_policy
        in {"script_candidate", "line_direction_review"}
        or not isinstance(candidate.get("review"), dict)
        or not isinstance(candidate.get("snapshot"), dict)
    ):
        raise ExternalWorkflowValidationError(
            "invalid_structured_result_record",
            "The stored structured result is invalid.",
        )
    return path, record


def store_structured_result_candidate(
    *,
    root_dir: str | Path,
    validated: dict[str, Any],
    handoff: dict[str, Any],
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    root = Path(root_dir)
    created = created_at_utc or utc_timestamp()
    task_type = str(validated["task_type"])
    input_payload = handoff.get("input") or {}
    target: dict[str, str] | None = None
    if task_type in {
        "persona_generation",
        "persona_refinement",
        "persona_reconciliation",
        "persona_audit",
        "persistent_voice_description_generation",
        "persistent_voice_description_refinement",
        "persistent_voice_description_audit",
    }:
        target = {
            "kind": "speaker",
            "value": str(input_payload.get("speaker") or "").strip(),
        }
    elif task_type == "visual_discovery":
        roster_entry = input_payload.get("roster_entry") or {}
        target_value = str(
            roster_entry.get("id")
            or roster_entry.get("canonical_name")
            or roster_entry.get("display_name")
            or ""
        ).strip()
        target = {
            "kind": "character",
            "value": target_value,
        }
    manifest = handoff.get("manifest") or {}
    candidate = {
        "handoff_id": validated["handoff_id"],
        "task_type": task_type,
        "target": target,
        "result_fingerprint": validated["result_fingerprint"],
        "review": copy.deepcopy(validated["review"]),
        "result": copy.deepcopy(validated["result"]),
        "snapshot": {
            "source_fingerprint": manifest.get("source_fingerprint"),
            "artifact_fingerprints": copy.deepcopy(
                manifest.get("artifact_fingerprints") or {}
            ),
        },
    }
    candidate_id = "structured_" + fingerprint_value(
        {
            "handoff_id": validated["handoff_id"],
            "result_fingerprint": validated["result_fingerprint"],
            "created_at_utc": created,
            "nonce": secrets.token_hex(8),
        }
    )[:24]
    record = {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "kind": "structured_result",
        "status": "inspected",
        "created_at_utc": created,
        "candidate": candidate,
        "application": None,
    }
    atomic_json_write(record, _candidate_path(root, candidate_id))
    return _public_structured_result(record)


def store_task_structured_result_candidate(
    *,
    root_dir: str | Path,
    completed: dict[str, Any],
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    root = Path(root_dir)
    created = created_at_utc or utc_timestamp()
    definition = get_task_definition(completed["task_type"])
    if definition.transfer_policy in {"script_candidate", "line_direction_review"}:
        raise ExternalWorkflowValidationError(
            "invalid_task_result_kind",
            "Script-shaped task results must enter Script review.",
        )
    candidate = {
        "task_id": completed["task_id"],
        "handoff_id": None,
        "task_type": completed["task_type"],
        "task_label": completed["task_label"],
        "target": copy.deepcopy(completed.get("target")),
        "manifest_fingerprint": completed["manifest_fingerprint"],
        "result_fingerprint": completed["result_fingerprint"],
        "review": copy.deepcopy(completed["review"]),
        "result": copy.deepcopy(completed["result"]),
        "guidance": copy.deepcopy(completed.get("guidance") or {}),
        "snapshot": {
            "source_fingerprint": completed.get("source_fingerprint"),
            "artifact_fingerprints": copy.deepcopy(
                completed.get("artifact_fingerprints") or {}
            ),
        },
    }
    candidate_id = "structured_" + fingerprint_value(
        {
            "task_id": completed["task_id"],
            "result_fingerprint": completed["result_fingerprint"],
            "created_at_utc": created,
            "nonce": secrets.token_hex(8),
        }
    )[:24]
    record = {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "kind": "structured_result",
        "status": "inspected",
        "created_at_utc": created,
        "candidate": candidate,
        "application": None,
    }
    atomic_json_write(record, _candidate_path(root, candidate_id))
    return _public_structured_result(record)


def _find_existing_task_candidate(
    root: Path,
    *,
    task_id: str,
    result_fingerprint: str,
) -> dict[str, Any] | None:
    candidates_root = _candidates_root(root)
    if not candidates_root.exists():
        return None
    for path in sorted(candidates_root.glob("*.json")):
        try:
            record = _read_json(path, default=None)
        except ExternalWorkflowValidationError:
            continue
        if not isinstance(record, dict):
            continue
        candidate = record.get("candidate") or {}
        origin = record.get("origin") or {}
        stored_task_id = candidate.get("task_id") or origin.get("task_id")
        stored_result = (
            candidate.get("result_fingerprint")
            or origin.get("result_fingerprint")
        )
        if stored_task_id != task_id or stored_result != result_fingerprint:
            continue
        if record.get("kind") == "structured_result":
            public = _public_structured_result(record)
        elif record.get("kind") == "annotated_script":
            public = _public_candidate(record)
        else:
            continue
        public["duplicate"] = True
        return public
    return None


def get_structured_result_candidate(
    *,
    root_dir: str | Path,
    candidate_id: str,
) -> dict[str, Any]:
    _, record = _load_structured_result_record(
        Path(root_dir),
        candidate_id,
    )
    return _public_structured_result(record)


def list_structured_result_candidates(
    *,
    root_dir: str | Path,
    task_type: str | None = None,
    status: str | None = None,
    source_fingerprint: str | None = None,
) -> list[dict[str, Any]]:
    """Return valid persisted structured candidates, newest first.

    This is intentionally read-only. It lets native review surfaces recover
    inspected Task Bundle results after navigation or application restart
    instead of depending on the transient response from the import request.
    """
    root = Path(root_dir)
    candidates_root = _candidates_root(root)
    if not candidates_root.exists():
        return []

    values: list[dict[str, Any]] = []
    for path in candidates_root.glob("*.json"):
        try:
            record = _read_json(path, default=None)
            if not isinstance(record, dict):
                continue
            candidate_id = record.get("candidate_id")
            if not isinstance(candidate_id, str):
                continue
            _, validated = _load_structured_result_record(root, candidate_id)
            public = _public_structured_result(validated)
        except (ExternalWorkflowValidationError, ExternalWorkflowConflictError):
            continue
        if task_type is not None and public.get("task_type") != task_type:
            continue
        if status is not None and public.get("status") != status:
            continue
        if source_fingerprint is not None:
            snapshot = public.get("snapshot") or {}
            if snapshot.get("source_fingerprint") != source_fingerprint:
                continue
        values.append(public)

    return sorted(
        values,
        key=lambda item: (
            str(item.get("created_at_utc") or ""),
            str(item.get("candidate_id") or ""),
        ),
        reverse=True,
    )


def mark_structured_result_transferred(
    *,
    root_dir: str | Path,
    candidate_id: str,
    expected_result_fingerprint: str,
    application: dict[str, Any],
) -> dict[str, Any]:
    root = Path(root_dir)
    path, record = _load_structured_result_record(root, candidate_id)
    if record["status"] != "inspected":
        raise ExternalWorkflowConflictError(
            "structured_result_already_transferred",
            "This structured result has already entered its native workflow.",
        )
    candidate = record["candidate"]
    if candidate["result_fingerprint"] != expected_result_fingerprint:
        raise ExternalWorkflowConflictError(
            "stale_structured_result",
            "The structured result changed before it could be transferred.",
        )
    updated = copy.deepcopy(record)
    updated["status"] = "transferred"
    updated["application"] = copy.deepcopy(application)
    atomic_json_write(updated, path)
    return _public_structured_result(updated)


def _comparison_summary(entries: Any) -> dict[str, Any] | None:
    if not isinstance(entries, list):
        return None
    speakers: set[str] = set()
    character_count = 0
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        speaker = entry.get("speaker")
        text = entry.get("text")
        if not isinstance(speaker, str) or not isinstance(text, str):
            return None
        speakers.add(speaker)
        character_count += len(text)
    return {
        "entry_count": len(entries),
        "speaker_count": len(speakers),
        "character_count": character_count,
    }


def _candidate_with_project_comparison(
    root: Path,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    augmented = copy.deepcopy(candidate)
    current_summary = _comparison_summary(
        _read_json(root / "annotated_script.json", default=None)
    )
    imported_summary = {
        "entry_count": int(augmented["summary"]["entry_count"]),
        "speaker_count": int(augmented["summary"]["speaker_count"]),
        "character_count": int(augmented["summary"]["character_count"]),
    }
    augmented["comparison"] = {
        "current": current_summary,
        "imported": imported_summary,
        "deltas": (
            {
                key: imported_summary[key] - current_summary[key]
                for key in imported_summary
            }
            if current_summary is not None
            else None
        ),
    }
    augmented.pop("import_fingerprint", None)
    augmented["import_fingerprint"] = fingerprint_value(augmented)
    return augmented


def store_annotated_script_candidate(
    *,
    root_dir: str | Path,
    candidate: dict[str, Any],
    origin: dict[str, Any] | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    root = Path(root_dir)
    created = created_at_utc or utc_timestamp()
    seed = {
        "import_fingerprint": candidate.get("import_fingerprint"),
        "created_at_utc": created,
        "nonce": secrets.token_hex(8),
    }
    candidate_id = "candidate_" + fingerprint_value(seed)[:24]
    record = {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "kind": "annotated_script",
        "status": "inspected",
        "created_at_utc": created,
        "origin": copy.deepcopy(origin or {}),
        "candidate": copy.deepcopy(candidate),
        "application": None,
    }
    atomic_json_write(record, _candidate_path(root, candidate_id))
    return _public_candidate(record)


def get_annotated_script_candidate(
    *,
    root_dir: str | Path,
    candidate_id: str,
) -> dict[str, Any]:
    _, record = _load_candidate_record(Path(root_dir), candidate_id)
    return _public_candidate(record)


def list_annotated_script_candidates(
    *,
    root_dir: str | Path,
    status: str | None = None,
) -> list[dict[str, Any]]:
    root = Path(root_dir)
    candidates_root = _candidates_root(root)
    if not candidates_root.exists():
        return []
    values: list[dict[str, Any]] = []
    for path in candidates_root.glob("*.json"):
        candidate_id = path.stem
        try:
            _, record = _load_candidate_record(root, candidate_id)
        except (ExternalWorkflowValidationError, ExternalWorkflowConflictError):
            continue
        public = _public_candidate(record)
        if status is not None and public.get("status") != status:
            continue
        values.append(public)
    return sorted(
        values,
        key=lambda item: (
            str(item.get("created_at_utc") or ""),
            str(item.get("candidate_id") or ""),
        ),
        reverse=True,
    )


def inspect_annotated_script_upload(
    *,
    root_dir: str | Path,
    import_path: str | Path,
    source_text: str | None,
    source_context: dict[str, Any] | None,
    current_script_fingerprint: str | None,
    checkpoint_status: str,
    generated_audio_count: int,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    try:
        candidate = inspect_annotated_script_import(
            import_path=import_path,
            source_text=source_text,
            current_script_fingerprint=current_script_fingerprint,
            checkpoint_status=checkpoint_status,
            generated_audio_count=generated_audio_count,
        )
    except AnnotatedScriptImportValidationError as exc:
        raise ExternalWorkflowValidationError(
            exc.code,
            str(exc),
            details=exc.details,
        ) from exc
    return store_annotated_script_candidate(
        root_dir=root_dir,
        candidate=_candidate_with_project_comparison(
            Path(root_dir),
            candidate,
        ),
        origin={
            "type": "annotated_script_upload",
            "filename": Path(import_path).name,
            "source": copy.deepcopy(source_context),
        },
        created_at_utc=created_at_utc,
    )


def create_stored_task_bundle(
    *,
    root_dir: str | Path,
    task_type: str,
    input_payload: dict[str, Any],
    application_version: str,
    source_fingerprint: str | None = None,
    artifact_fingerprints: dict[str, str] | None = None,
    target: dict[str, str] | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    root = Path(root_dir)
    pending = _tasks_root(root) / ("pending_" + secrets.token_hex(12))
    pending.mkdir(parents=True, exist_ok=False)
    try:
        bundle = create_task_bundle(
            output_dir=pending,
            task_type=task_type,
            input_payload=input_payload,
            application_version=application_version,
            source_fingerprint=source_fingerprint,
            artifact_fingerprints=artifact_fingerprints,
            target=target,
            bundle_name="task.alexandria-task.zip",
            created_at_utc=created_at_utc,
        )
        task_id = _safe_id(bundle["task_id"], "task_id")
        inspected = inspect_task_bundle(bundle["path"])
        record = {
            "schema_version": 2,
            "task_id": task_id,
            "created_at_utc": inspected["manifest"]["created_at_utc"],
            "task_type": bundle["task_type"],
            "task_label": bundle["task_label"],
            "native_destination": bundle["native_destination"],
            "target": copy.deepcopy(bundle.get("target")),
            "bundle_filename": "task.alexandria-task.zip",
            "manifest_fingerprint": bundle["manifest_fingerprint"],
            "status": "exported",
            "import": None,
        }
        atomic_json_write(record, pending / "record.json")
        destination = _task_dir(root, task_id)
        if destination.exists():
            existing = _read_json(destination / "record.json", default=None)
            if existing != record:
                raise ExternalWorkflowConflictError(
                    "task_identifier_collision",
                    "A different task already uses this identifier.",
                )
            shutil.rmtree(pending)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(pending, destination)
        return {
            "schema_version": 2,
            "task_id": task_id,
            "task_type": record["task_type"],
            "task_label": record["task_label"],
            "native_destination": record["native_destination"],
            "target": copy.deepcopy(record["target"]),
            "created_at_utc": record["created_at_utc"],
            "filename": bundle["filename"],
            "manifest_fingerprint": record["manifest_fingerprint"],
            "guidance": copy.deepcopy(bundle["guidance"]),
        }
    except (ChatGPTHandoffError, ExternalWorkflowError):
        if pending.exists():
            shutil.rmtree(pending, ignore_errors=True)
        raise
    except Exception:
        if pending.exists():
            shutil.rmtree(pending, ignore_errors=True)
        raise


def get_task_bundle_path(
    *,
    root_dir: str | Path,
    task_id: str,
) -> tuple[Path, dict[str, Any]]:
    root = Path(root_dir)
    directory = _task_dir(root, task_id)
    record = _read_json(directory / "record.json", default=None)
    if not isinstance(record, dict) or record.get("task_id") != task_id:
        raise ExternalWorkflowValidationError(
            "task_not_found",
            "The original exported task was not found in Alexandria's task library.",
        )
    bundle_path = directory / "task.alexandria-task.zip"
    try:
        inspected = inspect_task_bundle(bundle_path)
    except ChatGPTHandoffError as exc:
        raise ExternalWorkflowValidationError(exc.code, str(exc)) from exc
    if (
        inspected["manifest"]["task_id"] != task_id
        or inspected["manifest_fingerprint"]
        != record.get("manifest_fingerprint")
    ):
        raise ExternalWorkflowValidationError(
            "task_record_mismatch",
            "The stored task record no longer matches its bundle.",
        )
    return bundle_path, copy.deepcopy(record)


def list_task_library(
    *,
    root_dir: str | Path,
    current_source_fingerprint: str | None = None,
    current_artifact_fingerprints: dict[str, str] | None = None,
    status: str | None = None,
    query: str | None = None,
) -> list[dict[str, Any]]:
    """Return persisted Task Bundle records as a user-facing read model."""
    allowed_statuses = {
        "awaiting_import",
        "imported",
        "stale",
        "failed",
        "transferred",
    }
    if status is not None and status not in allowed_statuses:
        raise ExternalWorkflowValidationError(
            "invalid_task_library_status",
            "Task library status must be awaiting_import, imported, stale, failed, or transferred.",
        )
    search = (query or "").strip().casefold()
    root = Path(root_dir)
    tasks_root = _tasks_root(root)
    if not tasks_root.exists():
        return []

    current_artifacts = current_artifact_fingerprints or {}
    values: list[dict[str, Any]] = []
    for directory in sorted(tasks_root.iterdir()):
        if not directory.is_dir() or directory.name.startswith("pending_"):
            continue
        record_path = directory / "record.json"
        record = _read_json(record_path, default=None)
        if not isinstance(record, dict):
            values.append(
                {
                    "status": "failed",
                    "task_label": "Unreadable task",
                    "task_type": None,
                    "created_at_utc": None,
                    "native_destination": None,
                    "review_destination": None,
                    "target": None,
                    "download_url": None,
                    "error": "The stored task record is unreadable.",
                }
            )
            continue

        task_id = record.get("task_id")
        task_type = record.get("task_type")
        item = {
            "status": "failed",
            "task_label": record.get("task_label") or "Task",
            "task_type": task_type,
            "created_at_utc": record.get("created_at_utc"),
            "native_destination": record.get("native_destination"),
            "review_destination": None,
            "target": copy.deepcopy(record.get("target")),
            "download_url": None,
            "error": None,
        }
        try:
            if not isinstance(task_id, str) or not isinstance(task_type, str):
                raise ExternalWorkflowValidationError(
                    "invalid_task_record",
                    "The stored task record has no valid task identity.",
                )
            bundle_path, validated_record = get_task_bundle_path(
                root_dir=root,
                task_id=task_id,
            )
            inspected = inspect_task_bundle(bundle_path)
            manifest = inspected["manifest"]
            definition = get_task_definition(task_type)
            item["review_destination"] = definition.native_destination
            item["download_url"] = f"/api/tasks/{task_id}/download"

            stale = False
            source_snapshot = manifest.get("source_fingerprint")
            if (
                source_snapshot is not None
                and current_source_fingerprint is not None
                and source_snapshot != current_source_fingerprint
            ):
                stale = True
            for name, expected in (manifest.get("artifact_fingerprints") or {}).items():
                if current_artifacts.get(name) != expected:
                    stale = True
                    break

            public_status = "awaiting_import"
            if validated_record.get("status") == "imported":
                public_status = "imported"
                imported = validated_record.get("import") or {}
                candidate_id = imported.get("candidate_id")
                if isinstance(candidate_id, str):
                    candidate_record = _read_json(
                        _candidate_path(root, candidate_id),
                        default=None,
                    )
                    if (
                        isinstance(candidate_record, dict)
                        and candidate_record.get("status") in {"transferred", "applied"}
                    ):
                        public_status = "transferred"
            if stale and public_status != "transferred":
                public_status = "stale"
            item["status"] = public_status
        except (ExternalWorkflowValidationError, ChatGPTHandoffError) as exc:
            item["status"] = "failed"
            item["error"] = str(exc)

        haystack = " ".join(
            str(value or "")
            for value in (
                item.get("task_label"),
                item.get("task_type"),
                item.get("native_destination"),
                item.get("review_destination"),
                (item.get("target") or {}).get("value")
                if isinstance(item.get("target"), dict)
                else None,
            )
        ).casefold()
        if status is not None and item["status"] != status:
            continue
        if search and search not in haystack:
            continue
        values.append(item)

    return sorted(
        values,
        key=lambda item: str(item.get("created_at_utc") or ""),
        reverse=True,
    )


def _mark_task_imported(
    root: Path,
    *,
    task_id: str,
    candidate: dict[str, Any],
    at_utc: str,
) -> bool:
    try:
        _, record = get_task_bundle_path(root_dir=root, task_id=task_id)
    except ExternalWorkflowValidationError:
        return False
    path = _task_dir(root, task_id) / "record.json"
    updated = copy.deepcopy(record)
    updated["status"] = "imported"
    updated["import"] = {
        "candidate_id": candidate["candidate_id"],
        "candidate_kind": candidate["kind"],
        "result_fingerprint": (
            candidate.get("result_fingerprint")
            or (candidate.get("origin") or {}).get("result_fingerprint")
            or (candidate.get("application") or {}).get("result_fingerprint")
        ),
        "at_utc": at_utc,
    }
    atomic_json_write(updated, path)
    return True


def _legacy_completed_result(
    *,
    original_task_path: str | Path,
    result_path: str | Path,
    current_source_fingerprint: str | None,
    current_artifact_fingerprints: dict[str, str] | None,
) -> dict[str, Any]:
    try:
        handoff = inspect_handoff_bundle(original_task_path)
        validated = validate_handoff_result(
            bundle_path=original_task_path,
            result_path=result_path,
            current_source_fingerprint=current_source_fingerprint,
            current_artifact_fingerprints=current_artifact_fingerprints,
        )
    except ChatGPTHandoffError as exc:
        error_type = (
            ExternalWorkflowConflictError
            if exc.__class__.__name__.endswith("ConflictError")
            else ExternalWorkflowValidationError
        )
        raise error_type(exc.code, str(exc)) from exc
    definition = get_task_definition(validated["task_type"])
    manifest = handoff["manifest"]
    return {
        "schema_version": 1,
        "task_id": "task_" + fingerprint_value(manifest)[:32],
        "task_type": definition.task_type,
        "task_label": definition.label,
        "stage": definition.stage,
        "native_destination": definition.native_destination,
        "transfer_policy": definition.transfer_policy,
        "target": (
            {
                "kind": definition.target_kind,
                "value": str(
                    (handoff.get("input") or {}).get("speaker")
                    or ((handoff.get("input") or {}).get("roster_entry") or {}).get("id")
                    or ""
                ).strip(),
            }
            if definition.target_kind
            else None
        ),
        "source_fingerprint": manifest.get("source_fingerprint"),
        "artifact_fingerprints": copy.deepcopy(
            manifest.get("artifact_fingerprints") or {}
        ),
        "manifest_fingerprint": fingerprint_value(manifest),
        "result": copy.deepcopy(validated["result"]),
        "result_fingerprint": validated["result_fingerprint"],
        "result_filename": Path(result_path).name,
        "container": "legacy_v1_result",
        "guidance": {},
        "review": copy.deepcopy(validated["review"]),
    }


def _completed_task_metadata(path: Path) -> dict[str, Any] | None:
    if not path.is_file() or path.stat().st_size > MAX_RESULT_BYTES:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    metadata = value.get("alexandria_task")
    return copy.deepcopy(metadata) if isinstance(metadata, dict) else None


def _store_completed_task_result(
    *,
    root_dir: str | Path,
    completed: dict[str, Any],
    source_text: str | None,
    source_context: dict[str, Any] | None,
    current_script_fingerprint: str | None,
    checkpoint_status: str,
    generated_audio_count: int,
    created_at_utc: str | None,
) -> dict[str, Any]:
    root = Path(root_dir)
    duplicate = _find_existing_task_candidate(
        root,
        task_id=completed["task_id"],
        result_fingerprint=completed["result_fingerprint"],
    )
    if duplicate is not None:
        return duplicate
    definition = get_task_definition(completed["task_type"])
    created = created_at_utc or utc_timestamp()
    if definition.transfer_policy in {"script_candidate", "line_direction_review"}:
        workflow_root = _workflow_root(root)
        workflow_root.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="completed-task-",
            suffix=".json",
            dir=workflow_root,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            temporary.write_text(
                json.dumps(
                    completed["result"],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            candidate = inspect_annotated_script_import(
                import_path=temporary,
                source_text=source_text,
                current_script_fingerprint=current_script_fingerprint,
                checkpoint_status=checkpoint_status,
                generated_audio_count=generated_audio_count,
            )
        except AnnotatedScriptImportValidationError as exc:
            raise ExternalWorkflowValidationError(
                exc.code,
                str(exc),
                details=exc.details,
            ) from exc
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        public = store_annotated_script_candidate(
            root_dir=root,
            candidate=_candidate_with_project_comparison(root, candidate),
            origin={
                "type": "task_bundle_result",
                "task_id": completed["task_id"],
                "task_type": completed["task_type"],
                "task_label": completed["task_label"],
                "native_destination": completed["native_destination"],
                "target": copy.deepcopy(completed.get("target")),
                "manifest_fingerprint": completed["manifest_fingerprint"],
                "result_filename": completed["result_filename"],
                "result_fingerprint": completed["result_fingerprint"],
                "container": completed["container"],
                "source": copy.deepcopy(source_context),
            },
            created_at_utc=created,
        )
    else:
        public = store_task_structured_result_candidate(
            root_dir=root,
            completed=completed,
            created_at_utc=created,
        )
    public["duplicate"] = False
    public["task"] = {
        "task_type": completed["task_type"],
        "task_label": completed["task_label"],
        "native_destination": completed["native_destination"],
        "transfer_policy": completed["transfer_policy"],
        "target": copy.deepcopy(completed.get("target")),
        "container": completed["container"],
        "guidance": copy.deepcopy(completed.get("guidance") or {}),
    }
    try:
        _mark_task_imported(
            root,
            task_id=completed["task_id"],
            candidate=public,
            at_utc=created,
        )
    except Exception as exc:
        candidate_id = public.get("candidate_id")
        rollback_error = None
        if isinstance(candidate_id, str):
            try:
                _candidate_path(root, candidate_id).unlink()
            except FileNotFoundError:
                pass
            except OSError as rollback_exc:
                rollback_error = str(rollback_exc)
        raise ExternalWorkflowValidationError(
            "task_import_transaction_failed",
            "The completed task could not be committed; its candidate was rolled back.",
            details={"rollback_error": rollback_error},
        ) from exc
    return public


def inspect_completed_task_upload(
    *,
    root_dir: str | Path,
    completed_path: str | Path,
    original_task_path: str | Path | None,
    current_source_fingerprint: str | None,
    current_artifact_fingerprints: dict[str, str] | None,
    source_text: str | None,
    source_context: dict[str, Any] | None,
    current_script_fingerprint: str | None,
    checkpoint_status: str,
    generated_audio_count: int,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    completed_file = Path(completed_path)
    try:
        if zipfile.is_zipfile(completed_file):
            completed = inspect_completed_task_bundle(
                path=completed_file,
                current_source_fingerprint=current_source_fingerprint,
                current_artifact_fingerprints=current_artifact_fingerprints,
            )
        else:
            metadata = _completed_task_metadata(completed_file)
            if metadata is not None and metadata.get("schema_version") == 2:
                task_id = metadata.get("task_id")
                if not isinstance(task_id, str):
                    raise ExternalWorkflowValidationError(
                        "invalid_result_envelope",
                        "Completed task JSON has no valid task identity.",
                    )
                try:
                    task_bundle_path, _ = get_task_bundle_path(
                        root_dir=root_dir,
                        task_id=task_id,
                    )
                except ExternalWorkflowValidationError:
                    if original_task_path is None:
                        raise ExternalWorkflowConflictError(
                            "original_task_required",
                            "Choose the original Alexandria task ZIP so this JSON result can be verified.",
                        )
                    task_bundle_path = Path(original_task_path)
                completed = inspect_result_envelope(
                    envelope_path=completed_file,
                    task_bundle_path=task_bundle_path,
                    current_source_fingerprint=current_source_fingerprint,
                    current_artifact_fingerprints=current_artifact_fingerprints,
                )
            else:
                if original_task_path is None:
                    raise ExternalWorkflowConflictError(
                        "legacy_task_bundle_required",
                        "This appears to be a legacy result. Choose its original Alexandria handoff ZIP; no code or reference is required.",
                    )
                completed = _legacy_completed_result(
                    original_task_path=original_task_path,
                    result_path=completed_file,
                    current_source_fingerprint=current_source_fingerprint,
                    current_artifact_fingerprints=current_artifact_fingerprints,
                )
    except HandoffConflictError as exc:
        raise ExternalWorkflowConflictError(exc.code, str(exc)) from exc
    except ChatGPTHandoffError as exc:
        raise ExternalWorkflowValidationError(exc.code, str(exc)) from exc
    return _store_completed_task_result(
        root_dir=root_dir,
        completed=completed,
        source_text=source_text,
        source_context=source_context,
        current_script_fingerprint=current_script_fingerprint,
        checkpoint_status=checkpoint_status,
        generated_audio_count=generated_audio_count,
        created_at_utc=created_at_utc,
    )


def create_stored_handoff(
    *,
    root_dir: str | Path,
    task_type: str,
    stage_prompt: str,
    input_payload: dict[str, Any],
    output_schema: dict[str, Any],
    application_version: str,
    source_fingerprint: str | None = None,
    artifact_fingerprints: dict[str, str] | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    root = Path(root_dir)
    pending = _handoffs_root(root) / ("pending_" + secrets.token_hex(12))
    pending.mkdir(parents=True, exist_ok=False)
    try:
        bundle = create_handoff_bundle(
            output_dir=pending,
            task_type=task_type,
            stage_prompt=stage_prompt,
            input_payload=input_payload,
            output_schema=output_schema,
            application_version=application_version,
            source_fingerprint=source_fingerprint,
            artifact_fingerprints=artifact_fingerprints,
            bundle_name="handoff.zip",
            created_at_utc=created_at_utc,
        )
        handoff_id = _safe_id(bundle["manifest"]["handoff_id"], "handoff_id")
        record = {
            "schema_version": WORKFLOW_SCHEMA_VERSION,
            "handoff_id": handoff_id,
            "created_at_utc": bundle["manifest"]["created_at_utc"],
            "task_type": task_type,
            "bundle_filename": "handoff.zip",
            "manifest": copy.deepcopy(bundle["manifest"]),
        }
        atomic_json_write(record, pending / "record.json")
        destination = _handoff_dir(root, handoff_id)
        if destination.exists():
            existing = _read_json(destination / "record.json", default=None)
            if existing != record:
                raise ExternalWorkflowConflictError(
                    "handoff_identifier_collision",
                    "A different handoff already uses this identifier.",
                )
            shutil.rmtree(pending)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(pending, destination)
        return {
            "schema_version": WORKFLOW_SCHEMA_VERSION,
            "handoff_id": handoff_id,
            "task_type": task_type,
            "created_at_utc": record["created_at_utc"],
            "filename": f"alexandria-{task_type}-{handoff_id[-8:]}.zip",
            "manifest": copy.deepcopy(record["manifest"]),
        }
    except (ChatGPTHandoffError, ExternalWorkflowError):
        if pending.exists():
            shutil.rmtree(pending, ignore_errors=True)
        raise
    except Exception:
        if pending.exists():
            shutil.rmtree(pending, ignore_errors=True)
        raise


def get_handoff_bundle_path(
    *,
    root_dir: str | Path,
    handoff_id: str,
) -> tuple[Path, dict[str, Any]]:
    root = Path(root_dir)
    directory = _handoff_dir(root, handoff_id)
    record = _read_json(directory / "record.json", default=None)
    if not isinstance(record, dict) or record.get("handoff_id") != handoff_id:
        raise ExternalWorkflowValidationError(
            "handoff_not_found",
            f"ChatGPT handoff {handoff_id!r} was not found.",
        )
    bundle_path = directory / "handoff.zip"
    try:
        inspected = inspect_handoff_bundle(bundle_path)
    except ChatGPTHandoffError as exc:
        raise ExternalWorkflowValidationError(exc.code, str(exc)) from exc
    if inspected["manifest"] != record.get("manifest"):
        raise ExternalWorkflowValidationError(
            "handoff_record_mismatch",
            "The stored handoff record no longer matches its bundle.",
        )
    return bundle_path, copy.deepcopy(record)


def get_handoff_prompt(
    *,
    root_dir: str | Path,
    handoff_id: str,
) -> dict[str, Any]:
    bundle_path, record = get_handoff_bundle_path(
        root_dir=root_dir,
        handoff_id=handoff_id,
    )
    try:
        inspected = inspect_handoff_bundle(bundle_path)
    except ChatGPTHandoffError as exc:
        raise ExternalWorkflowValidationError(exc.code, str(exc)) from exc
    return {
        "handoff_id": handoff_id,
        "task_type": record["task_type"],
        "prompt": inspected["prompt"],
    }


def open_handoff_folder(
    *,
    root_dir: str | Path,
    handoff_id: str,
) -> dict[str, Any]:
    bundle_path, record = get_handoff_bundle_path(
        root_dir=root_dir,
        handoff_id=handoff_id,
    )
    if sys.platform != "darwin":
        raise ExternalWorkflowValidationError(
            "open_handoff_folder_unavailable",
            "Opening the handoff folder is supported only on macOS.",
        )
    completed = subprocess.run(
        ["open", str(bundle_path.parent)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        message = (completed.stderr or "").strip()
        raise ExternalWorkflowValidationError(
            "open_handoff_folder_failed",
            message or "macOS could not open the handoff folder.",
        )
    return {
        "handoff_id": handoff_id,
        "task_type": record["task_type"],
        "opened": True,
    }


def inspect_stored_handoff_result(
    *,
    root_dir: str | Path,
    handoff_id: str,
    result_path: str | Path,
    current_source_fingerprint: str | None,
    current_artifact_fingerprints: dict[str, str] | None,
    source_text: str | None,
    source_context: dict[str, Any] | None,
    current_script_fingerprint: str | None,
    checkpoint_status: str,
    generated_audio_count: int,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    bundle_path, record = get_handoff_bundle_path(
        root_dir=root_dir,
        handoff_id=handoff_id,
    )
    try:
        validated = validate_handoff_result(
            bundle_path=bundle_path,
            result_path=result_path,
            current_source_fingerprint=current_source_fingerprint,
            current_artifact_fingerprints=current_artifact_fingerprints,
        )
    except ChatGPTHandoffError as exc:
        error_type = (
            ExternalWorkflowConflictError
            if exc.__class__.__name__.endswith("ConflictError")
            else ExternalWorkflowValidationError
        )
        raise error_type(exc.code, str(exc)) from exc
    if validated["task_type"] not in {"script_generation", "script_review"}:
        try:
            handoff = inspect_handoff_bundle(bundle_path)
        except ChatGPTHandoffError as exc:
            raise ExternalWorkflowValidationError(
                exc.code,
                str(exc),
            ) from exc
        return store_structured_result_candidate(
            root_dir=root_dir,
            validated=validated,
            handoff=handoff,
            created_at_utc=created_at_utc,
        )
    try:
        candidate = inspect_annotated_script_import(
            import_path=result_path,
            source_text=source_text,
            current_script_fingerprint=current_script_fingerprint,
            checkpoint_status=checkpoint_status,
            generated_audio_count=generated_audio_count,
        )
    except AnnotatedScriptImportValidationError as exc:
        raise ExternalWorkflowValidationError(
            exc.code,
            str(exc),
            details=exc.details,
        ) from exc
    return store_annotated_script_candidate(
        root_dir=root_dir,
        candidate=_candidate_with_project_comparison(
            Path(root_dir),
            candidate,
        ),
        origin={
            "type": "chatgpt_handoff_result",
            "handoff_id": handoff_id,
            "task_type": record["task_type"],
            "result_filename": Path(result_path).name,
            "result_fingerprint": validated["result_fingerprint"],
            "source": copy.deepcopy(source_context),
        },
        created_at_utc=created_at_utc,
    )


def _new_chunks(entries: list[dict[str, str]]) -> list[dict[str, Any]]:
    chunks = group_into_chunks(entries)
    for index, chunk in enumerate(chunks):
        chunk["id"] = index
        chunk["status"] = "pending"
        chunk["audio_path"] = None
    return chunks


def _speaker_labels(entries: list[dict[str, str]]) -> list[str]:
    return sorted({entry["speaker"] for entry in entries})


def _metadata_for_candidate(
    *,
    record: dict[str, Any],
    operation_id: str,
    at_utc: str,
) -> dict[str, Any]:
    candidate = record["candidate"]
    entries = candidate["entries"]
    metadata = copy.deepcopy(candidate.get("metadata"))
    source_context = (record.get("origin") or {}).get("source")
    if not isinstance(source_context, dict):
        source_context = {}
    if metadata is None:
        provenance = candidate["provenance"]
        verified = provenance.get("status") == "verified"
        origin_type = (record.get("origin") or {}).get("type")
        source = {
            "basename": str(
                source_context.get("basename")
                or candidate.get("filename")
                or "external-import.json"
            ),
            "fingerprint": (
                provenance.get("source_fingerprint")
                if verified
                else None
            ),
            "verification_status": (
                "verified"
                if verified
                else "unverified"
            ),
            "character_count": int(source_context.get("character_count") or 0),
            "chunk_count": int(source_context.get("chunk_count") or 0),
        }
        identity = {
            "mode": "external_import",
            "backend": "external",
            "model_name": (
                "Ordinary ChatGPT handoff"
                if origin_type == "chatgpt_handoff_result"
                else "Imported annotated script"
            ),
            "provenance_status": provenance.get("status"),
            "origin_type": origin_type,
        }
        metadata = {
            "schema_version": 1,
            "generated_at_utc": at_utc,
            "source": source,
            "generation": {
                "fingerprint": fingerprint_value(identity),
                "effective_identity": identity,
            },
            "result": {
                "script_fingerprint": fingerprint_value(entries),
                "entry_count": len(entries),
                "speaker_labels": _speaker_labels(entries),
            },
            "resume": {
                "resumed": False,
                "previously_completed_chunks": 0,
            },
        }
    metadata["import"] = {
        "operation_id": operation_id,
        "candidate_id": record["candidate_id"],
        "imported_at_utc": at_utc,
        "filename": candidate["filename"],
        "provenance": copy.deepcopy(candidate["provenance"]),
        "origin": copy.deepcopy(record.get("origin") or {}),
    }
    return metadata


def _audio_validity(
    *,
    operation_id: str,
    old_chunks: list[dict[str, Any]],
    at_utc: str,
) -> dict[str, Any]:
    invalidations = []
    for index, chunk in enumerate(old_chunks):
        if chunk.get("status") != "done" and not chunk.get("audio_path"):
            continue
        invalidations.append(
            {
                "chunk_id": chunk.get("id", index),
                "speaker": chunk.get("speaker"),
                "audio_path": chunk.get("audio_path"),
                "reason": "annotated_script_replaced",
            }
        )
    return build_audio_validity_record(
        operation_id=operation_id,
        operation="annotated_script_import",
        at_utc=at_utc,
        invalidations=invalidations,
        default_reason="annotated_script_replaced",
        note=(
            "Invalidated production audio was moved to the import operation's "
            "content-addressed backup. The imported script rebuilt all chunks "
            "as pending; prior audio is not eligible for final output."
        ),
    )


def _attach_audio_backup_state(
    *,
    root: Path,
    changes: dict[Path, Any],
    records: list[dict[str, Any]],
    operation_id: str,
) -> None:
    mapping = audio_backup_map(records)
    chunks_path = root / "chunks.json"
    chunks = changes.get(chunks_path)
    if isinstance(chunks, list):
        for chunk in chunks:
            stale_path = chunk.get("stale_audio_path")
            backup = mapping.get(stale_path)
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
            ):
                chunk[field] = None

    validity_path = root / "audio_validity.json"
    validity = changes.get(validity_path)
    if isinstance(validity, dict):
        changes[validity_path] = attach_audio_backup_evidence(
            validity,
            mapping,
        )


def _transaction(
    *,
    root: Path,
    changes: dict[Path, Any],
    summary: dict[str, Any],
    audio_paths: list[str | None] | tuple[str | None, ...] = (),
) -> dict[str, Any]:
    del audio_paths
    operation_id = summary["operation_id"]
    resolved_root = root.expanduser().resolve()

    def transaction_writer(value: Any, path: str | Path) -> None:
        target = root / Path(path).expanduser().resolve().relative_to(
            resolved_root
        )
        atomic_json_write(value, target)

    return apply_audio_invalidation_transaction(
        project_root=root,
        operation_dir=_history_root(root) / operation_id,
        operation_id=operation_id,
        operation=summary["operation"],
        at_utc=summary["at_utc"],
        changes=changes,
        invalidations=(
            changes.get(root / "audio_validity.json", {}).get(
                "invalidated_chunks",
                [],
            )
            if isinstance(changes.get(root / "audio_validity.json"), dict)
            else []
        ),
        default_reason="annotated_script_replaced",
        note=(
            "Invalidated production audio was moved to the import operation's "
            "content-addressed backup. The imported script rebuilt all chunks "
            "as pending; prior audio is not eligible for final output."
        ),
        record_metadata=summary,
        record_schema_version=HISTORY_SCHEMA_VERSION,
        json_writer=transaction_writer,
    )


def _public_operation(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in record.items()
        if key not in {"files", "audio_backups"}
    } | {
        "files": sorted(record.get("files", {})),
        "audio_backup_count": len(record.get("audio_backups", [])),
    }


def apply_annotated_script_candidate(
    *,
    root_dir: str | Path,
    candidate_id: str,
    current_script_fingerprint: str | None = None,
    checkpoint_status: str | None = None,
    checkpoint_decision: str | None = None,
    expected_current_script_fingerprint: str | None = None,
    expected_current_metadata_fingerprint: str | None = None,
    expected_current_voice_config_fingerprint: str | None = None,
    expected_current_chunks_fingerprint: str | None = None,
    at_utc: str | None = None,
) -> dict[str, Any]:
    root = Path(root_dir)
    with _WORKFLOW_LOCK:
        candidate_path, record = _load_candidate_record(root, candidate_id)
        expected_files = (
            (
                "annotated_script.json",
                expected_current_script_fingerprint,
                "current_script_changed",
            ),
            (
                "annotated_script.meta.json",
                expected_current_metadata_fingerprint,
                "current_metadata_changed",
            ),
            (
                "voice_config.json",
                expected_current_voice_config_fingerprint,
                "current_voice_config_changed",
            ),
            (
                "chunks.json",
                expected_current_chunks_fingerprint,
                "current_chunks_changed",
            ),
        )
        for filename, expected, code in expected_files:
            if expected is None:
                continue
            actual = _json_fingerprint(root / filename)
            if actual != expected:
                raise ExternalWorkflowConflictError(
                    code,
                    f"{filename} changed after the import candidate was reviewed.",
                    details={
                        "expected_fingerprint": expected,
                        "current_fingerprint": actual,
                    },
                )
        if current_script_fingerprint is None:
            current_script_fingerprint = _json_fingerprint(
                root / "annotated_script.json"
            )
        if checkpoint_status is None:
            snapshot = record.get("candidate", {}).get("snapshot", {})
            checkpoint_status = str(snapshot.get("checkpoint_status") or "none")
        if record.get("status") != "inspected":
            raise ExternalWorkflowConflictError(
                "candidate_already_applied",
                "This import candidate is no longer pending review.",
            )
        if checkpoint_status == "running":
            raise ExternalWorkflowConflictError(
                "script_generation_running",
                "Stop Script generation before applying an external script.",
            )
        try:
            plan = build_annotated_script_import_plan(
                candidate=record["candidate"],
                current_script_fingerprint=current_script_fingerprint,
                checkpoint_status=checkpoint_status,
                checkpoint_decision=checkpoint_decision,
            )
        except AnnotatedScriptImportConflictError as exc:
            raise ExternalWorkflowConflictError(exc.code, str(exc)) from exc
        except AnnotatedScriptImportValidationError as exc:
            raise ExternalWorkflowValidationError(
                exc.code,
                str(exc),
                details=exc.details,
            ) from exc
        if plan["status"] == "cancelled":
            return {
                "status": "cancelled",
                "candidate_id": candidate_id,
                "warnings": copy.deepcopy(plan["warnings"]),
            }
        now = at_utc or utc_timestamp()
        operation_seed = {
            "plan_id": plan["plan_id"],
            "at_utc": now,
            "nonce": secrets.token_hex(8),
        }
        operation_id = "script_import_" + fingerprint_value(operation_seed)[:24]
        entries = copy.deepcopy(record["candidate"]["entries"])
        voice_config = copy.deepcopy(record["candidate"].get("voice_config"))
        if voice_config is not None:
            try:
                validate_voice_aliases(voice_config)
            except VoiceAliasError as exc:
                raise ExternalWorkflowValidationError(
                    "invalid_voice_aliases",
                    str(exc),
                ) from exc
        old_chunks = _read_json(root / "chunks.json", default=[])
        if not isinstance(old_chunks, list):
            raise ExternalWorkflowValidationError(
                "invalid_existing_chunks",
                "chunks.json must contain a JSON array before import.",
            )
        updated_record = copy.deepcopy(record)
        updated_record["status"] = "applied"
        updated_record["application"] = {
            "operation_id": operation_id,
            "applied_at_utc": now,
            "plan_id": plan["plan_id"],
            "checkpoint_decision": (
                next(
                    action["decision"]
                    for action in plan["actions"]
                    if action["action"] == "checkpoint"
                )
            ),
        }
        changes: dict[Path, Any] = {
            root / "annotated_script.json": entries,
            root / "annotated_script.meta.json": _metadata_for_candidate(
                record=record,
                operation_id=operation_id,
                at_utc=now,
            ),
            root / "chunks.json": _new_chunks(entries),
            root / "audio_validity.json": _audio_validity(
                operation_id=operation_id,
                old_chunks=old_chunks,
                at_utc=now,
            ),
            candidate_path: updated_record,
        }
        if voice_config is not None:
            changes[root / "voice_config.json"] = voice_config
        checkpoint_action = next(
            action
            for action in plan["actions"]
            if action["action"] == "checkpoint"
        )
        if checkpoint_action["decision"] == "discard":
            changes[root / "generation_state.json"] = None
        summary = {
            "operation_id": operation_id,
            "operation": "annotated_script_import",
            "candidate_id": candidate_id,
            "plan_id": plan["plan_id"],
            "at_utc": now,
            "checkpoint_status": checkpoint_status,
            "checkpoint_decision": checkpoint_action["decision"],
            "source_script_fingerprint": current_script_fingerprint,
            "result_script_fingerprint": fingerprint_value(entries),
            "entry_count": len(entries),
            "speaker_labels": _speaker_labels(entries),
            "provenance": copy.deepcopy(record["candidate"]["provenance"]),
            "warnings": copy.deepcopy(plan["warnings"]),
        }
        operation = _transaction(
            root=root,
            changes=changes,
            summary=summary,
            audio_paths=[chunk.get("audio_path") for chunk in old_chunks],
        )
        public_operation = _public_operation(operation)
        return {
            "status": "applied",
            "candidate": _public_candidate(updated_record),
            "operation": public_operation,
            "operation_id": operation_id,
            "script_fingerprint": fingerprint_value(entries),
            "metadata_fingerprint": _json_fingerprint(
                root / "annotated_script.meta.json"
            ),
            "voice_config_fingerprint": _json_fingerprint(
                root / "voice_config.json"
            ),
            "chunks_fingerprint": _json_fingerprint(root / "chunks.json"),
        }


def rollback_annotated_script_import(
    *,
    root_dir: str | Path,
    operation_id: str,
    expected_current_script_fingerprint: str | None = None,
    expected_current_metadata_fingerprint: str | None = None,
    expected_current_voice_config_fingerprint: str | None = None,
    expected_current_chunks_fingerprint: str | None = None,
    at_utc: str | None = None,
) -> dict[str, Any]:
    root = Path(root_dir)
    safe_operation_id = _safe_id(operation_id, "operation_id")
    with _WORKFLOW_LOCK:
        history_path = _history_root(root) / safe_operation_id / "operation.json"
        record = _read_json(history_path, default=None)
        if (
            not isinstance(record, dict)
            or record.get("schema_version") != HISTORY_SCHEMA_VERSION
            or record.get("operation_id") != safe_operation_id
            or record.get("operation") != "annotated_script_import"
            or not isinstance(record.get("files"), dict)
        ):
            raise ExternalWorkflowValidationError(
                "import_operation_not_found",
                f"Import operation {safe_operation_id!r} was not found.",
            )
        expected_files = (
            ("annotated_script.json", expected_current_script_fingerprint),
            ("annotated_script.meta.json", expected_current_metadata_fingerprint),
            ("voice_config.json", expected_current_voice_config_fingerprint),
            ("chunks.json", expected_current_chunks_fingerprint),
        )
        for filename, expected in expected_files:
            if expected is None:
                continue
            actual = _json_fingerprint(root / filename)
            if actual != expected:
                raise ExternalWorkflowConflictError(
                    "import_rollback_conflict",
                    f"Cannot roll back because {filename} changed after the import.",
                    details={
                        "expected_fingerprint": expected,
                        "current_fingerprint": actual,
                    },
                )
        if isinstance(record.get("audio_invalidation"), dict):
            now = at_utc or utc_timestamp()
            try:
                restored = undo_audio_invalidation_transaction(
                    project_root=root,
                    record_path=history_path,
                    undone_at_utc=now,
                    consume_backups=True,
                    mark_record_undone=True,
                )
            except AudioInvalidationError as exc:
                if exc.code == "audio_invalidation_undo_conflict":
                    message = str(exc).replace(
                        "Cannot undo because ",
                        "Cannot roll back because ",
                    ).replace(
                        "changed after the invalidation",
                        "changed after the import",
                    )
                    raise ExternalWorkflowConflictError(
                        "import_rollback_conflict",
                        message,
                    ) from exc
                raise ExternalWorkflowValidationError(
                    "invalid_history",
                    str(exc),
                ) from exc
            cleanup = restored["audio_backup_cleanup"]
            rollback_id = "rollback_" + fingerprint_value(
                {
                    "operation_id": safe_operation_id,
                    "at_utc": now,
                    "restored": restored["restored_files"],
                }
            )[:24]
            rollback = {
                "schema_version": HISTORY_SCHEMA_VERSION,
                "operation_id": rollback_id,
                "operation": "rollback_annotated_script_import",
                "rolls_back_operation_id": safe_operation_id,
                "at_utc": now,
                "restored_files": restored["restored_files"],
                "restored_audio_paths": restored["restored_audio_paths"],
                "audio_backup_cleanup_status": cleanup["status"],
                "consumed_audio_backup_paths": sorted(
                    cleanup["removed_paths"]
                ),
                "already_missing_audio_backup_paths": sorted(
                    cleanup["already_missing_paths"]
                ),
                "audio_backup_cleanup_failures": copy.deepcopy(
                    cleanup["failed_paths"]
                ),
                "audio_backups_consumed_at_utc": (
                    now
                    if cleanup["removed_paths"]
                    or cleanup["already_missing_paths"]
                    else None
                ),
                "result_script_fingerprint": _json_fingerprint(
                    root / "annotated_script.json"
                ),
            }
            atomic_json_write(
                rollback,
                _history_root(root) / rollback_id / "operation.json",
            )
            return copy.deepcopy(rollback)
        for relative, state in record["files"].items():
            path = root / relative
            current = _snapshot(path)["sha256"]
            if current != state.get("after_sha256"):
                raise ExternalWorkflowConflictError(
                    "import_rollback_conflict",
                    f"Cannot roll back because {relative} changed after the import.",
                )
        audio_backups = record.get("audio_backups", [])
        if not isinstance(audio_backups, list):
            raise ExternalWorkflowValidationError(
                "invalid_history",
                "Import operation audio backups are invalid.",
            )
        try:
            validate_operation_audio_backups(
                root_dir=root,
                records=audio_backups,
                require_original_absent=True,
            )
        except AudioArtifactError as exc:
            raise ExternalWorkflowConflictError(
                "import_audio_rollback_conflict",
                str(exc),
            ) from exc

        current_snapshots = {
            relative: _snapshot(root / relative)
            for relative in record["files"]
        }
        restored = []
        restored_audio = []
        try:
            for relative, state in record["files"].items():
                path = root / relative
                _restore_snapshot(path, state["before"], ".undo.tmp")
                restored.append(relative)
            restored_audio = restore_operation_audio(
                root_dir=root,
                records=audio_backups,
                require_original_absent=True,
                consume_backups=False,
            )
        except Exception:
            for relative, snapshot in current_snapshots.items():
                _restore_snapshot(
                    root / relative,
                    snapshot,
                    ".undo-recovery.tmp",
                )
            remove_restored_operation_audio(
                root_dir=root,
                records=audio_backups,
            )
            raise
        now = at_utc or utc_timestamp()
        if audio_backups:
            audio_cleanup = consume_operation_audio_backups(
                root_dir=root,
                records=audio_backups,
            )
        else:
            audio_cleanup = {
                "status": "not_needed",
                "removed_paths": [],
                "already_missing_paths": [],
                "failed_paths": [],
            }
        rollback_id = "rollback_" + fingerprint_value(
            {
                "operation_id": safe_operation_id,
                "at_utc": now,
                "restored": sorted(restored),
            }
        )[:24]
        rollback = {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "operation_id": rollback_id,
            "operation": "rollback_annotated_script_import",
            "rolls_back_operation_id": safe_operation_id,
            "at_utc": now,
            "restored_files": sorted(restored),
            "restored_audio_paths": sorted(restored_audio),
            "audio_backup_cleanup_status": audio_cleanup["status"],
            "consumed_audio_backup_paths": sorted(
                audio_cleanup["removed_paths"]
            ),
            "already_missing_audio_backup_paths": sorted(
                audio_cleanup["already_missing_paths"]
            ),
            "audio_backup_cleanup_failures": copy.deepcopy(
                audio_cleanup["failed_paths"]
            ),
            "audio_backups_consumed_at_utc": (
                now
                if audio_cleanup["removed_paths"]
                or audio_cleanup["already_missing_paths"]
                else None
            ),
            "result_script_fingerprint": _json_fingerprint(
                root / "annotated_script.json"
            ),
        }
        atomic_json_write(
            rollback,
            _history_root(root) / rollback_id / "operation.json",
        )
        return copy.deepcopy(rollback)
