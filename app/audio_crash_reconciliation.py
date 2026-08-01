from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import re
import stat
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Iterable, Mapping

from generation_state import atomic_json_write, fingerprint_value


AUDIO_TRANSITION_SCHEMA_VERSION: Final = 1
AUDIO_TRANSITION_JOURNAL_DIRNAME: Final = "audio_transition_journal"
AUDIO_DURABLE_TRANSITIONS: Final = (
    "internal_segment_generation",
    "segment_completion",
    "join",
    "immutable_take_installation",
    "chunks_metadata",
    "take_registry",
    "request_receipt_publication",
    "lifecycle_receipt_publication",
    "current_take_selection",
    "invalidation",
    "undo_restoration",
)


class InjectedAudioCrash(BaseException):
    pass


class JournalSchemaError(ValueError):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


_SAFE_OPERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_PROJECT_LOCKS_GUARD = threading.Lock()
_PROJECT_LOCKS: dict[str, threading.RLock] = {}
_PROJECT_LOCK_DEPTH = threading.local()


def _reset_project_locks_after_fork() -> None:
    global _PROJECT_LOCKS_GUARD, _PROJECT_LOCKS, _PROJECT_LOCK_DEPTH
    _PROJECT_LOCKS_GUARD = threading.Lock()
    _PROJECT_LOCKS = {}
    _PROJECT_LOCK_DEPTH = threading.local()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_project_locks_after_fork)


def _validate_operation_id(operation_id: str) -> str:
    if not _SAFE_OPERATION_ID.fullmatch(operation_id) or operation_id in {".", ".."}:
        raise ValueError("operation_id must be one safe opaque path component")
    return operation_id


@contextmanager
def _project_lock(root: Path):
    key = str(root)
    with _PROJECT_LOCKS_GUARD:
        process_lock = _PROJECT_LOCKS.setdefault(key, threading.RLock())
    with process_lock:
        depths = getattr(_PROJECT_LOCK_DEPTH, "depths", None)
        if depths is None:
            depths = {}
            _PROJECT_LOCK_DEPTH.depths = depths
        if depths.get(key, 0):
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
            return
        journal_root = root / AUDIO_TRANSITION_JOURNAL_DIRNAME
        if journal_root.is_symlink():
            raise ValueError("journal root must not be a symlink")
        journal_root.mkdir(parents=True, exist_ok=True)
        lock_path = journal_root / ".lock"
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            depths[key] = 1
            try:
                yield
            finally:
                depths.pop(key, None)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def audio_project_lock(project_root: str | Path):
    root = Path(project_root).expanduser().resolve()
    with _project_lock(root):
        yield


def _journal_path(root: Path, operation_id: str) -> Path:
    safe_id = _validate_operation_id(operation_id)
    unresolved_root = root / AUDIO_TRANSITION_JOURNAL_DIRNAME
    if unresolved_root.is_symlink():
        raise ValueError("journal root must not be a symlink")
    journal_root = unresolved_root.resolve()
    try:
        journal_root.relative_to(root)
    except ValueError as exc:
        raise ValueError("journal root escaped its project root") from exc
    operation_dir = journal_root / safe_id
    if operation_dir.is_symlink():
        raise ValueError("journal operation directory must not be a symlink")
    target = (operation_dir / "transition.json").resolve()
    try:
        target.relative_to(journal_root)
    except ValueError as exc:
        raise ValueError("journal path escaped its project root") from exc
    return target


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"


def _snapshot(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"exists": False, "sha256": None, "content_base64": None}
    content = path.read_bytes()
    return {
        "exists": True,
        "sha256": hashlib.sha256(content).hexdigest(),
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def _intended_snapshot(value: Any) -> dict[str, Any]:
    content = _json_bytes(value)
    return {
        "exists": True,
        "sha256": hashlib.sha256(content).hexdigest(),
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def _bytes_snapshot(content: bytes) -> dict[str, Any]:
    return {
        "exists": True,
        "sha256": hashlib.sha256(content).hexdigest(),
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def _missing_snapshot() -> dict[str, Any]:
    return {"exists": False, "sha256": None, "content_base64": None}


def _confined(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    target.relative_to(root)
    return target


def _restore_snapshot(path: Path, snapshot: Mapping[str, Any]) -> None:
    if snapshot.get("exists"):
        content = base64.b64decode(str(snapshot["content_base64"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".audio-reconcile.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    else:
        path.unlink(missing_ok=True)


def _artifact_snapshot(root: Path, relative: str, expected: str | None) -> dict[str, Any]:
    path = _confined(root, relative)
    actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return {"relative_path": relative, "sha256": expected or actual}


def _configured_crash_point(transition: str, explicit: str | None) -> str | None:
    if explicit is not None:
        return explicit
    if os.environ.get("ALEXANDRIA_TEST_AUDIO_CRASH_INJECTION") != "1":
        return None
    configured = os.environ.get("ALEXANDRIA_AUDIO_CRASH_POINT", "")
    for point in ("before", "after"):
        if configured == f"{transition}:{point}":
            return point
    return None


def _record_fingerprint(record: Mapping[str, Any]) -> str:
    return fingerprint_value(
        {key: value for key, value in record.items() if key != "record_fingerprint"}
    )


def apply_audio_transition(
    project_root: str | Path,
    *,
    transition: str,
    operation_id: str,
    json_writes: Mapping[str, Any],
    binary_writes: Mapping[str, bytes] | None = None,
    deletes: Iterable[str] = (),
    required_artifacts: Mapping[str, str | None] | None = None,
    crash_point: str | None = None,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    if transition not in AUDIO_DURABLE_TRANSITIONS:
        raise ValueError(f"Unsupported durable audio transition: {transition}")
    journal_path = _journal_path(root, operation_id)
    with _project_lock(root):
        writes = {
            relative: {
                "before": _snapshot(_confined(root, relative)),
                "after": _intended_snapshot(value),
            }
            for relative, value in sorted(json_writes.items())
        }
        for relative, content in sorted((binary_writes or {}).items()):
            writes[relative] = {
                "before": _snapshot(_confined(root, relative)),
                "after": _bytes_snapshot(content),
            }
        for relative in sorted(deletes):
            writes[relative] = {
                "before": _snapshot(_confined(root, relative)),
                "after": _missing_snapshot(),
            }
        artifacts = [
            _artifact_snapshot(root, relative, expected)
            for relative, expected in sorted((required_artifacts or {}).items())
        ]
        record = {
            "schema_version": AUDIO_TRANSITION_SCHEMA_VERSION,
            "operation_id": operation_id,
            "transition": transition,
            "status": "applying",
            "created_at_utc": _utc_now(),
            "writes": writes,
            "required_artifacts": artifacts,
            "record_fingerprint": None,
        }
        record["record_fingerprint"] = _record_fingerprint(record)
        atomic_json_write(record, journal_path)
        crash_point = _configured_crash_point(transition, crash_point)
        if crash_point == "before":
            raise InjectedAudioCrash(f"before:{transition}")
        for relative, content in sorted((binary_writes or {}).items()):
            _restore_snapshot(_confined(root, relative), _bytes_snapshot(content))
        for relative, value in sorted(json_writes.items()):
            atomic_json_write(value, _confined(root, relative))
        for relative in sorted(deletes):
            _confined(root, relative).unlink(missing_ok=True)
        if crash_point == "after":
            raise InjectedAudioCrash(f"after:{transition}")
        record["status"] = "committed"
        record["committed_at_utc"] = _utc_now()
        record["record_fingerprint"] = _record_fingerprint(record)
        atomic_json_write(record, journal_path)
        return record


@contextmanager
def audio_mutation_guard(
    project_root: str | Path,
    *,
    transition: str,
    operation_id: str,
    watched_paths: Iterable[str],
):
    root = Path(project_root).expanduser().resolve()
    journal_path = _journal_path(root, operation_id)
    with _project_lock(root):
        writes = {
            relative: {"before": _snapshot(_confined(root, relative)), "after": None}
            for relative in sorted(watched_paths)
        }
        record = {
            "schema_version": AUDIO_TRANSITION_SCHEMA_VERSION,
            "operation_id": operation_id,
            "transition": transition,
            "status": "applying",
            "created_at_utc": _utc_now(),
            "writes": writes,
            "required_artifacts": [],
            "record_fingerprint": None,
        }
        record["record_fingerprint"] = _record_fingerprint(record)
        atomic_json_write(record, journal_path)
        crash_point = _configured_crash_point(transition, None)
        if crash_point == "before":
            raise InjectedAudioCrash(f"before:{transition}")
        outcome: dict[str, Any] = {"required_artifacts": {}}

        def prepare_binary_write(relative: str, content: bytes) -> None:
            if relative not in writes:
                raise ValueError(f"Unwatched durable audio path: {relative}")
            writes[relative]["after"] = _bytes_snapshot(content)
            record["record_fingerprint"] = _record_fingerprint(record)
            atomic_json_write(record, journal_path)

        def prepare_json_writes(json_writes: Mapping[str, Any]) -> None:
            for relative, value in json_writes.items():
                if relative not in writes:
                    raise ValueError(f"Unwatched durable audio path: {relative}")
                writes[relative]["after"] = _intended_snapshot(value)
            for relative, pair in writes.items():
                if pair["after"] is None:
                    pair["after"] = _snapshot(_confined(root, relative))
            record["required_artifacts"] = [
                _artifact_snapshot(root, relative, expected)
                for relative, expected in sorted(
                    outcome["required_artifacts"].items()
                )
            ]
            record["record_fingerprint"] = _record_fingerprint(record)
            atomic_json_write(record, journal_path)

        outcome["prepare_binary_write"] = prepare_binary_write
        outcome["prepare_json_writes"] = prepare_json_writes
        yield outcome
        for relative in writes:
            writes[relative]["after"] = _snapshot(_confined(root, relative))
        record["required_artifacts"] = [
            _artifact_snapshot(root, relative, expected)
            for relative, expected in sorted(outcome["required_artifacts"].items())
        ]
        record["record_fingerprint"] = _record_fingerprint(record)
        atomic_json_write(record, journal_path)
        if crash_point == "after":
            raise InjectedAudioCrash(f"after:{transition}")
        record["status"] = "committed"
        record["committed_at_utc"] = _utc_now()
        record["record_fingerprint"] = _record_fingerprint(record)
        atomic_json_write(record, journal_path)


def _validate_snapshot(value: object, field: str, *, allow_none: bool) -> None:
    if value is None and allow_none:
        return
    if not isinstance(value, dict):
        raise JournalSchemaError(field, f"journal field {field} must be an object")
    exists = value.get("exists")
    sha256 = value.get("sha256")
    content = value.get("content_base64")
    if not isinstance(exists, bool):
        raise JournalSchemaError(f"{field}.exists", "snapshot existence must be boolean")
    if not exists:
        if sha256 is not None or content is not None:
            raise JournalSchemaError(field, "missing snapshot must not contain bytes")
        return
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise JournalSchemaError(f"{field}.sha256", "snapshot SHA-256 is invalid")
    if not isinstance(content, str):
        raise JournalSchemaError(f"{field}.content_base64", "snapshot content is invalid")
    try:
        decoded = base64.b64decode(content, validate=True)
    except ValueError as exc:
        raise JournalSchemaError(f"{field}.content_base64", "snapshot content is invalid") from exc
    if hashlib.sha256(decoded).hexdigest() != sha256:
        raise JournalSchemaError(f"{field}.sha256", "snapshot content fingerprint differs")


def _validate_record(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise JournalSchemaError("record", "journal must be an object")
    for field, expected_type in (("operation_id", str), ("transition", str), ("status", str), ("writes", dict)):
        if not isinstance(value.get(field), expected_type):
            raise JournalSchemaError(field, f"journal field {field} has an invalid type")
    _validate_operation_id(value["operation_id"])
    if value.get("schema_version") != AUDIO_TRANSITION_SCHEMA_VERSION:
        raise JournalSchemaError("schema_version", "unsupported journal schema")
    if value["transition"] not in AUDIO_DURABLE_TRANSITIONS:
        raise JournalSchemaError("transition", "unsupported durable transition")
    if value["status"] not in {"applying", "committed", "rolled_back"}:
        raise JournalSchemaError("status", "unsupported journal status")
    for relative, pair in value["writes"].items():
        field = f"writes.{relative}"
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise JournalSchemaError(field, "journal write path is invalid")
        if not isinstance(pair, dict):
            raise JournalSchemaError(field, "journal write pair must be an object")
        _validate_snapshot(pair.get("before"), f"{field}.before", allow_none=False)
        _validate_snapshot(pair.get("after"), f"{field}.after", allow_none=True)
    artifacts = value.get("required_artifacts", [])
    if not isinstance(artifacts, list):
        raise JournalSchemaError("required_artifacts", "required artifacts must be a list")
    for index, artifact in enumerate(artifacts):
        field = f"required_artifacts.{index}"
        if not isinstance(artifact, dict):
            raise JournalSchemaError(field, "required artifact must be an object")
        relative = artifact.get("relative_path")
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise JournalSchemaError(f"{field}.relative_path", "required artifact path is invalid")
        sha256 = artifact.get("sha256")
        if sha256 is not None and (
            not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256)
        ):
            raise JournalSchemaError(f"{field}.sha256", "required artifact SHA-256 is invalid")
    fingerprint = value.get("record_fingerprint")
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise JournalSchemaError("record_fingerprint", "journal fingerprint is invalid")
    return value


def _snapshot_matches(path: Path, snapshot: Mapping[str, Any]) -> bool:
    current = _snapshot(path)
    return current["exists"] == snapshot.get("exists") and current["sha256"] == snapshot.get("sha256")


def _artifacts_valid(root: Path, artifacts: list[dict[str, Any]]) -> bool:
    for artifact in artifacts:
        path = _confined(root, str(artifact["relative_path"]))
        expected = artifact.get("sha256")
        if not path.is_file() or not expected:
            return False
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            return False
    return True


def _reconcile_audio_transitions_locked(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    journal_root = root / AUDIO_TRANSITION_JOURNAL_DIRNAME
    actions: list[dict[str, Any]] = []
    if not journal_root.is_dir():
        return {"repaired_count": 0, "rolled_back_count": 0, "unresolved_count": 0, "actions": []}
    for operation_dir in sorted(journal_root.iterdir(), key=lambda path: path.name):
        if operation_dir.is_symlink():
            actions.append({"operation_id": operation_dir.name, "action": "unresolved", "states": {}, "error_code": "journal_operation_symlink", "error_field": None})
            continue
        try:
            if not stat.S_ISDIR(operation_dir.lstat().st_mode):
                continue
        except OSError:
            actions.append({"operation_id": operation_dir.name, "action": "unresolved", "states": {}, "error_code": "journal_unreadable", "error_field": None})
            continue
        journal_path = operation_dir / "transition.json"
        try:
            journal_mode = journal_path.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError:
            actions.append({"operation_id": operation_dir.name, "action": "unresolved", "states": {}, "error_code": "journal_unreadable", "error_field": None})
            continue
        if stat.S_ISLNK(journal_mode):
            actions.append({"operation_id": operation_dir.name, "action": "unresolved", "states": {}, "error_code": "journal_file_symlink", "error_field": None})
            continue
        if not stat.S_ISREG(journal_mode):
            actions.append({"operation_id": operation_dir.name, "action": "unresolved", "states": {}, "error_code": "journal_unreadable", "error_field": None})
            continue
        try:
            record = _validate_record(json.loads(journal_path.read_text(encoding="utf-8")))
            if record.get("record_fingerprint") != _record_fingerprint(record):
                raise ValueError("journal fingerprint mismatch")
            if record.get("status") != "applying":
                continue
            writes = record["writes"]
            incomplete = any(pair.get("after") is None for pair in writes.values())
            states = {
                relative: (
                    "before" if _snapshot_matches(_confined(root, relative), pair["before"])
                    else "after" if pair.get("after") is not None and _snapshot_matches(_confined(root, relative), pair["after"])
                    else "unexpected"
                )
                for relative, pair in writes.items()
            }
            if incomplete:
                if "unexpected" in states.values():
                    actions.append({"operation_id": record.get("operation_id"), "action": "unresolved", "states": states, "orphan_evidence": True})
                    continue
                for relative, pair in writes.items():
                    _restore_snapshot(_confined(root, relative), pair["before"])
                record["status"] = "rolled_back"
                record["reconciled_at_utc"] = _utc_now()
                record["record_fingerprint"] = _record_fingerprint(record)
                atomic_json_write(record, journal_path)
                actions.append({"operation_id": record["operation_id"], "action": "rolled_back", "states": states, "orphan_evidence": True})
                continue
            if "unexpected" in states.values():
                actions.append({"operation_id": record.get("operation_id"), "action": "unresolved", "states": states})
                continue
            artifacts = list(record.get("required_artifacts") or [])
            action = "repaired" if _artifacts_valid(root, artifacts) else "rolled_back"
            selected = "after" if action == "repaired" else "before"
            for relative, pair in writes.items():
                _restore_snapshot(_confined(root, relative), pair[selected])
            record["status"] = "committed" if action == "repaired" else "rolled_back"
            record["reconciled_at_utc"] = _utc_now()
            record["record_fingerprint"] = _record_fingerprint(record)
            atomic_json_write(record, journal_path)
            actions.append({"operation_id": record.get("operation_id"), "action": action, "states": states})
        except JournalSchemaError as exc:
            actions.append({"operation_id": journal_path.parent.name, "action": "unresolved", "states": {}, "error_code": "journal_schema_invalid", "error_field": exc.field})
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            actions.append({"operation_id": journal_path.parent.name, "action": "unresolved", "states": {}, "error_code": "journal_unreadable", "error_field": None})
    return {
        "repaired_count": sum(item["action"] == "repaired" for item in actions),
        "rolled_back_count": sum(item["action"] == "rolled_back" for item in actions),
        "unresolved_count": sum(item["action"] == "unresolved" for item in actions),
        "actions": actions,
    }


def reconcile_audio_transitions(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    journal_root = root / AUDIO_TRANSITION_JOURNAL_DIRNAME
    if journal_root.is_symlink():
        raise ValueError("journal root must not be a symlink")
    if not journal_root.is_dir():
        return {"repaired_count": 0, "rolled_back_count": 0, "unresolved_count": 0, "actions": []}
    with _project_lock(root):
        return _reconcile_audio_transitions_locked(root)
