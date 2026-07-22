from __future__ import annotations

import base64
import copy
import fnmatch
import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from generation_state import atomic_json_write, fingerprint_value
from roster_context import RosterContextError, load_project_roster_context
from voice_training_projects import (
    VoiceTrainingProjectError,
    read_voice_training_project,
)


MIGRATION_SCHEMA_VERSION = 1
MIGRATION_STATE_FILENAME = "migration_state.json"
MIGRATION_BACKUP_DIRNAME = "migration_backups"
_MIGRATION_LOCK = threading.RLock()
_MIGRATION_OPERATION_ID_PATTERN = re.compile(
    r"migration_[0-9a-f]{24}"
)
_ROLLBACK_OPERATION_ID_PATTERN = re.compile(
    r"rollback_[0-9a-f]{24}"
)


class MigrationError(RuntimeError):
    pass


class MigrationValidationError(MigrationError):
    pass


class MigrationConflictError(MigrationError):
    pass


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


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
        "sha256": _sha256_bytes(content),
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def _current_hash(path: Path) -> str | None:
    return _snapshot(path)["sha256"]


def _require_within_root(
    root: Path,
    path: Path,
    *,
    label: str,
) -> Path:
    resolved_root = root.expanduser().resolve()
    resolved_path = path.expanduser().resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise MigrationValidationError(
            f"{label} must remain inside the project root."
        ) from exc
    return resolved_path


def _relative_key(root: Path, path: Path) -> str:
    return _require_within_root(
        root,
        path,
        label="Migration path",
    ).relative_to(root.expanduser().resolve()).as_posix()


def _operation_record_path(root: Path, path_text: str) -> Path:
    if not isinstance(path_text, str) or not path_text.strip():
        raise MigrationValidationError(
            "Migration operation contains an invalid file path."
        )
    relative = Path(path_text)
    if relative.is_absolute():
        raise MigrationValidationError(
            "Migration operation file paths must be project-relative."
        )
    return _require_within_root(
        root,
        root / relative,
        label="Migration operation file path",
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationValidationError(
            f"Could not read {path}: {exc}"
        ) from exc


def _json_object(path: Path, label: str) -> dict[str, Any]:
    value = _read_json(path)
    if not isinstance(value, dict):
        raise MigrationValidationError(
            f"{label} must be a JSON object."
        )
    return value


def _json_array(path: Path, label: str) -> list[Any]:
    value = _read_json(path)
    if not isinstance(value, list):
        raise MigrationValidationError(
            f"{label} must be a JSON array."
        )
    return value


def _count_files(path: Path, patterns: tuple[str, ...] = ("*",)) -> int:
    if not path.exists():
        return 0
    count = 0
    for _, _, filenames in os.walk(path):
        for filename in filenames:
            if any(fnmatch.fnmatch(filename, pattern) for pattern in patterns):
                count += 1
    return count


def _count_registry_candidates(root: Path) -> int:
    """Inspect project-owned JSON without crawling application code or environments."""
    owned_directories = {
        "scripts",
        "persona_refs",
        "voice_training_projects",
        "lora_datasets",
        "lora_models",
        "designed_voices",
        "clone_voices",
    }
    seen: set[Path] = set()
    count = 0
    for directory, directories, filenames in os.walk(root):
        directory_path = Path(directory)
        if directory_path == root:
            directories[:] = [name for name in directories if name in owned_directories]
        for filename in filenames:
            lowered = filename.casefold()
            if not (
                fnmatch.fnmatch(lowered, "*accent*.json")
                or fnmatch.fnmatch(lowered, "*registry*.json")
            ):
                continue
            path = directory_path / filename
            if path not in seen:
                seen.add(path)
                count += 1
    return count


def _inventory(root: Path) -> dict[str, Any]:
    return {
        "saved_scripts": _count_files(root / "scripts", ("*.json",)),
        "persona_references": _count_files(
            root / "persona_refs",
            ("*.json",),
        ),
        "voice_training_projects": _count_files(
            root / "voice_training_projects",
            ("project.json",),
        ),
        "lora_datasets": _count_files(root / "lora_datasets"),
        "lora_models": _count_files(root / "lora_models"),
        "designed_voices": _count_files(root / "designed_voices"),
        "clone_voices": _count_files(root / "clone_voices"),
        "generated_audio": _count_files(
            root / "voicelines",
            ("*.wav", "*.flac", "*.mp3", "*.m4a"),
        ),
        "accent_registry_candidates": _count_registry_candidates(root),
    }


def _plan_payload(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in plan.items()
        if key != "plan_fingerprint"
    }


def _validate_legacy_artifacts(
    *,
    root: Path,
    blockers: list[str],
    warnings: list[str],
    compatibility: dict[str, Any],
) -> None:
    script_path = root / "annotated_script.json"
    metadata_path = root / "annotated_script.meta.json"
    voice_config_path = root / "voice_config.json"
    roster_path = root / "character_roster.json"

    if script_path.exists():
        try:
            script = _json_array(
                script_path,
                "annotated_script.json",
            )
            compatibility["annotated_script"] = {
                "present": True,
                "entry_count": len(script),
                "text_rewrite_planned": False,
            }
        except MigrationValidationError as exc:
            blockers.append(str(exc))
    else:
        compatibility["annotated_script"] = {
            "present": False,
            "entry_count": 0,
            "text_rewrite_planned": False,
        }

    if metadata_path.exists():
        try:
            metadata = _json_object(
                metadata_path,
                "annotated_script.meta.json",
            )
            compatibility["script_metadata"] = {
                "present": True,
                "unknown_keys_preserved": sorted(metadata),
            }
        except MigrationValidationError as exc:
            blockers.append(str(exc))
    else:
        compatibility["script_metadata"] = {
            "present": False,
            "legacy_without_metadata_supported": True,
        }
        if script_path.exists():
            warnings.append(
                "Legacy annotated script has no metadata sidecar; it will be preserved unchanged."
            )

    if voice_config_path.exists():
        try:
            config = _json_object(
                voice_config_path,
                "voice_config.json",
            )
            compatibility["voice_config"] = {
                "present": True,
                "speaker_count": len(config),
                "unknown_fields_preserved": True,
            }
        except MigrationValidationError as exc:
            blockers.append(str(exc))
    else:
        compatibility["voice_config"] = {
            "present": False,
            "missing_supported": True,
        }

    persona_dir = root / "persona_refs"
    persona_without_visual = 0
    invalid_persona = []
    if persona_dir.exists():
        for path in sorted(persona_dir.glob("*.json")):
            try:
                reference = _json_object(path, path.name)
            except MigrationValidationError as exc:
                invalid_persona.append(str(exc))
                continue
            if "visual" not in reference:
                persona_without_visual += 1
    blockers.extend(invalid_persona)
    compatibility["persona_references"] = {
        "present": persona_dir.exists(),
        "without_visual_count": persona_without_visual,
        "visual_field_added": False,
    }

    if roster_path.exists():
        try:
            roster, _, _ = load_project_roster_context(
                root_dir=root,
            )
            compatibility["approved_roster"] = {
                "present": roster is not None,
                "roster_fingerprint": (
                    roster["roster_fingerprint"]
                    if roster is not None
                    else None
                ),
            }
        except RosterContextError as exc:
            blockers.append(
                "Approved roster is incompatible with the selected source: "
                + str(exc)
            )
    else:
        compatibility["approved_roster"] = {
            "present": False,
            "rosterless_installation_supported": True,
        }

    projects_root = root / "voice_training_projects"
    invalid_projects = []
    project_count = 0
    if projects_root.exists():
        for path in sorted(projects_root.glob("character_*/project.json")):
            project_count += 1
            try:
                read_voice_training_project(path)
            except (VoiceTrainingProjectError, FileNotFoundError) as exc:
                invalid_projects.append(
                    f"Voice-training project {path}: {exc}"
                )
    blockers.extend(invalid_projects)
    compatibility["voice_training_projects"] = {
        "present": projects_root.exists(),
        "project_count": project_count,
        "artifacts_rewritten": False,
    }


def build_migration_plan(
    *,
    root_dir: str | Path,
    config_path: str | Path,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    config_target = _require_within_root(
        root,
        Path(config_path),
        label="Configuration path",
    )
    actions: list[dict[str, Any]] = []
    blockers: list[str] = []
    warnings: list[str] = []
    compatibility: dict[str, Any] = {}

    if config_target.exists():
        try:
            config = _json_object(config_target, "Configuration root")
            llm = config.get("llm")
            if llm is None:
                compatibility["config"] = {
                    "present": True,
                    "llm_section_present": False,
                    "unknown_fields_preserved": True,
                }
                warnings.append(
                    "Legacy configuration has no llm section; it remains valid and unchanged until the user saves modern LLM settings."
                )
            elif not isinstance(llm, dict):
                blockers.append(
                    "Configuration llm field must be a JSON object before migration."
                )
            else:
                profiles = llm.get("profiles")
                compatibility["config"] = {
                    "present": True,
                    "llm_section_present": True,
                    "unknown_fields_preserved": True,
                }
                if profiles is None:
                    actions.append(
                        {
                            "action": "add_empty_llm_profiles",
                            "path": str(config_target),
                            "description": (
                                "Add llm.profiles as an empty object without changing existing LLM or TTS values."
                            ),
                            "destructive": False,
                        }
                    )
                elif not isinstance(profiles, dict):
                    blockers.append(
                        "Configuration llm.profiles must be a JSON object before migration."
                    )
                else:
                    compatibility["config"]["profiles_present"] = True
        except MigrationValidationError as exc:
            blockers.append(str(exc))
    else:
        compatibility["config"] = {
            "present": False,
            "missing_supported": True,
        }
        warnings.append(
            "No configuration file exists; defaults and Setup remain authoritative."
        )

    _validate_legacy_artifacts(
        root=root,
        blockers=blockers,
        warnings=warnings,
        compatibility=compatibility,
    )

    plan = {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "root_dir": str(root),
        "config_path": str(config_target),
        "actions": actions,
        "blockers": blockers,
        "warnings": warnings,
        "compatibility": compatibility,
        "inventory": _inventory(root),
        "destructive_action_count": sum(
            bool(action["destructive"])
            for action in actions
        ),
        "text_rewrite_planned": False,
        "automatic_artifact_deletion_planned": False,
        "plan_fingerprint": "",
    }
    plan["plan_fingerprint"] = fingerprint_value(
        _plan_payload(plan)
    )
    return plan


def _restore_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
    if not snapshot["exists"]:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    content = base64.b64decode(snapshot["content_base64"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".migration.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def apply_migration_plan(
    *,
    root_dir: str | Path,
    config_path: str | Path,
    expected_plan_fingerprint: str,
    confirm: bool,
    at_utc: str | None = None,
) -> dict[str, Any]:
    if confirm is not True:
        raise MigrationValidationError(
            "Migration requires explicit confirmation."
        )
    root = Path(root_dir).expanduser().resolve()
    config_target = _require_within_root(
        root,
        Path(config_path),
        label="Configuration path",
    )
    with _MIGRATION_LOCK:
        plan = build_migration_plan(
            root_dir=root,
            config_path=config_target,
        )
        if plan["plan_fingerprint"] != expected_plan_fingerprint:
            raise MigrationConflictError(
                "Migration plan changed after review. Refresh the dry run and retry."
            )
        if plan["blockers"]:
            raise MigrationValidationError(
                "Migration is blocked: " + " ".join(plan["blockers"])
            )
        timestamp = at_utc or utc_timestamp()
        operation_seed = {
            "plan_fingerprint": plan["plan_fingerprint"],
            "actions": plan["actions"],
            "at_utc": timestamp,
        }
        operation_id = "migration_" + fingerprint_value(
            operation_seed
        )[:24]
        state_path = root / MIGRATION_STATE_FILENAME
        touched = [state_path]
        if any(
            action["action"] == "add_empty_llm_profiles"
            for action in plan["actions"]
        ):
            touched.append(config_target)
        before = {
            _relative_key(root, path): _snapshot(path)
            for path in touched
        }
        state_key = _relative_key(root, state_path)
        backup_path = (
            root
            / MIGRATION_BACKUP_DIRNAME
            / operation_id
            / "operation.json"
        )
        try:
            for action in plan["actions"]:
                if action["action"] != "add_empty_llm_profiles":
                    raise MigrationValidationError(
                        f"Unsupported migration action: {action['action']}."
                    )
                config = _json_object(
                    config_target,
                    "Configuration root",
                )
                llm = config.get("llm")
                if not isinstance(llm, dict):
                    raise MigrationConflictError(
                        "Configuration llm section changed after planning."
                    )
                if "profiles" in llm:
                    raise MigrationConflictError(
                        "Configuration profiles changed after planning."
                    )
                llm["profiles"] = {}
                atomic_json_write(config, config_target)

            after_hashes = {
                _relative_key(root, path): _current_hash(path)
                for path in touched
                if path != state_path
            }
            record = {
                "schema_version": MIGRATION_SCHEMA_VERSION,
                "operation_id": operation_id,
                "applied_at_utc": timestamp,
                "plan_fingerprint": plan["plan_fingerprint"],
                "actions": copy.deepcopy(plan["actions"]),
                "files": {
                    path: {
                        "before": snapshot,
                        "after_sha256": after_hashes.get(path),
                    }
                    for path, snapshot in before.items()
                    if path != state_key
                },
                "previous_state": before[state_key],
                "text_rewritten": False,
                "artifacts_deleted": False,
            }
            atomic_json_write(record, backup_path)
            atomic_json_write(
                {
                    "schema_version": MIGRATION_SCHEMA_VERSION,
                    "last_operation_id": operation_id,
                    "applied_at_utc": timestamp,
                    "plan_fingerprint": plan["plan_fingerprint"],
                },
                state_path,
            )
            return {
                "operation": record,
                "status": build_migration_plan(
                    root_dir=root,
                    config_path=config_target,
                ),
            }
        except Exception:
            for path_text, snapshot in before.items():
                _restore_snapshot(
                    _operation_record_path(root, path_text),
                    snapshot,
                )
            try:
                backup_path.unlink()
                backup_path.parent.rmdir()
            except (FileNotFoundError, OSError):
                pass
            raise


def _migration_operation_summary(record: dict[str, Any]) -> dict[str, Any]:
    operation_id = str(record["operation_id"])
    if operation_id.startswith("rollback_"):
        restored_files = record.get("restored_files")
        if not isinstance(restored_files, list):
            raise MigrationValidationError(
                "Rollback operation restored_files must be an array."
            )
        rolls_back_operation_id = record.get("rolls_back_operation_id")
        if (
            not isinstance(rolls_back_operation_id, str)
            or _MIGRATION_OPERATION_ID_PATTERN.fullmatch(
                rolls_back_operation_id
            )
            is None
        ):
            raise MigrationValidationError(
                "Rollback operation references an invalid migration operation ID."
            )
        return {
            "operation_id": operation_id,
            "operation": "rollback",
            "at_utc": record.get("rolled_back_at_utc"),
            "action_count": len(restored_files),
            "changed_file_count": len(restored_files),
            "text_rewritten": False,
            "artifacts_deleted": False,
            "rolls_back_operation_id": rolls_back_operation_id,
            "rollback_available": False,
            "state": "complete",
        }

    actions = record.get("actions")
    files = record.get("files")
    if not isinstance(actions, list) or not isinstance(files, dict):
        raise MigrationValidationError(
            "Migration operation actions and files must be valid collections."
        )
    return {
        "operation_id": operation_id,
        "operation": "migration",
        "at_utc": record.get("applied_at_utc"),
        "action_count": len(actions),
        "changed_file_count": len(files),
        "text_rewritten": bool(record.get("text_rewritten")),
        "artifacts_deleted": bool(record.get("artifacts_deleted")),
        "rolls_back_operation_id": None,
        "rollback_available": True,
        "state": "applied",
    }


def list_migration_operations(
    *,
    root_dir: str | Path,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    history_root = root / MIGRATION_BACKUP_DIRNAME
    if not history_root.exists():
        operations: list[dict[str, Any]] = []
        invalid_records: list[dict[str, str]] = []
    elif not history_root.is_dir() or history_root.is_symlink():
        raise MigrationValidationError(
            "Migration history must be a normal directory inside the project root."
        )
    else:
        operations = []
        invalid_records = []
        for directory in sorted(history_root.iterdir(), key=lambda item: item.name):
            operation_id = directory.name
            try:
                if directory.is_symlink() or not directory.is_dir():
                    raise MigrationValidationError(
                        "Migration history entries must be normal directories."
                    )
                if _MIGRATION_OPERATION_ID_PATTERN.fullmatch(operation_id):
                    record = load_migration_operation(
                        root_dir=root,
                        operation_id=operation_id,
                    )
                elif _ROLLBACK_OPERATION_ID_PATTERN.fullmatch(operation_id):
                    path = directory / "operation.json"
                    if path.is_symlink() or not path.is_file():
                        raise MigrationValidationError(
                            "Rollback operation record was not found."
                        )
                    record = _json_object(path, "Rollback operation")
                    if record.get("schema_version") != MIGRATION_SCHEMA_VERSION:
                        raise MigrationValidationError(
                            "Unsupported rollback operation schema."
                        )
                    if record.get("operation_id") != operation_id:
                        raise MigrationValidationError(
                            "Rollback operation ID does not match its directory."
                        )
                    if record.get("operation") != "rollback":
                        raise MigrationValidationError(
                            "Rollback operation type is invalid."
                        )
                else:
                    raise MigrationValidationError(
                        "Migration history entry has an invalid operation ID."
                    )
                operations.append(_migration_operation_summary(record))
            except (MigrationValidationError, OSError) as exc:
                invalid_records.append(
                    {
                        "operation_id": operation_id,
                        "message": str(exc),
                    }
                )

    rolled_back = {
        item["rolls_back_operation_id"]
        for item in operations
        if item["operation"] == "rollback"
        and item.get("rolls_back_operation_id")
    }
    for item in operations:
        if item["operation"] == "migration" and item["operation_id"] in rolled_back:
            item["rollback_available"] = False
            item["state"] = "rolled_back"
    operations.sort(
        key=lambda item: (
            str(item.get("at_utc") or ""),
            str(item.get("operation_id") or ""),
        ),
        reverse=True,
    )
    invalid_records.sort(key=lambda item: item["operation_id"])
    history_payload = {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "operations": operations,
        "invalid_records": invalid_records,
    }
    return {
        **history_payload,
        "history_fingerprint": fingerprint_value(history_payload),
    }


def load_migration_operation(
    *,
    root_dir: str | Path,
    operation_id: str,
) -> dict[str, Any]:
    if (
        not isinstance(operation_id, str)
        or _MIGRATION_OPERATION_ID_PATTERN.fullmatch(
            operation_id.strip()
        )
        is None
    ):
        raise MigrationValidationError(
            "operation_id must be a valid migration operation ID."
        )
    root = Path(root_dir).expanduser().resolve()
    path = (
        root
        / MIGRATION_BACKUP_DIRNAME
        / operation_id.strip()
        / "operation.json"
    )
    if not path.exists():
        raise MigrationValidationError(
            f"Migration operation {operation_id!r} was not found."
        )
    record = _json_object(path, "Migration operation")
    if record.get("schema_version") != MIGRATION_SCHEMA_VERSION:
        raise MigrationValidationError(
            "Unsupported migration operation schema."
        )
    if record.get("operation_id") != operation_id.strip():
        raise MigrationValidationError(
            "Migration operation ID does not match its file path."
        )
    if not isinstance(record.get("files"), dict):
        raise MigrationValidationError(
            "Migration operation files must be an object."
        )
    for path_text, state in record["files"].items():
        _operation_record_path(root, path_text)
        if not isinstance(state, dict) or not isinstance(
            state.get("before"),
            dict,
        ):
            raise MigrationValidationError(
                "Migration operation contains an invalid file snapshot."
            )
    return record


def rollback_migration(
    *,
    root_dir: str | Path,
    operation_id: str,
    at_utc: str | None = None,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    with _MIGRATION_LOCK:
        record = load_migration_operation(
            root_dir=root,
            operation_id=operation_id,
        )
        for path_text, state in record["files"].items():
            target = _operation_record_path(root, path_text)
            current = _current_hash(target)
            if current != state["after_sha256"]:
                raise MigrationConflictError(
                    f"Cannot roll back because {path_text} changed after migration."
                )
        for path_text, state in record["files"].items():
            _restore_snapshot(
                _operation_record_path(root, path_text),
                state["before"],
            )
        state_path = root / MIGRATION_STATE_FILENAME
        _restore_snapshot(state_path, record["previous_state"])
        rollback_time = at_utc or utc_timestamp()
        rollback_id = "rollback_" + fingerprint_value(
            {
                "operation_id": operation_id,
                "at_utc": rollback_time,
            }
        )[:24]
        rollback_record = {
            "schema_version": MIGRATION_SCHEMA_VERSION,
            "operation_id": rollback_id,
            "operation": "rollback",
            "rolls_back_operation_id": operation_id,
            "rolled_back_at_utc": rollback_time,
            "restored_files": sorted(record["files"]),
        }
        atomic_json_write(
            rollback_record,
            root
            / MIGRATION_BACKUP_DIRNAME
            / rollback_id
            / "operation.json",
        )
        return rollback_record
