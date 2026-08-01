from __future__ import annotations

import base64
import copy
import json
import os
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
from character_roster import (
    build_draft_roster,
    stable_entry_id,
)
from character_roster_actions import build_approved_roster
from character_visuals import (
    PROFILE_BUCKETS,
    build_visual_dossier,
    load_persona_reference,
)
from generation_state import (
    atomic_json_write,
    fingerprint_text,
    fingerprint_value,
)
from synthesis_windows import synthesis_receipt_reset_fields
from project import group_into_chunks
from roster_context import load_project_roster_context
from roster_mutation_lock import APPROVED_ROSTER_MUTATION_LOCK
from script_voice_mapping import (
    build_script_voice_index,
    resolve_script_voice_name,
)
from speaker_management_add import (
    SpeakerAddConflictError,
    SpeakerAddValidationError,
    prepare_speaker_add,
)
from speaker_management_add_contract import SpeakerAddContext
from voice_training_projects import (
    compute_voice_training_project_fingerprint,
    read_voice_training_project,
    validate_voice_training_project,
    voice_training_project_path,
)


HISTORY_SCHEMA_VERSION = 1
_HISTORY_DIRNAME = "speaker_management_history"
_AUDIO_VALIDITY_FILENAME = "audio_validity.json"
_MANAGEMENT_LOCK = APPROVED_ROSTER_MUTATION_LOCK


class SpeakerManagementError(RuntimeError):
    pass


class SpeakerManagementConflictError(SpeakerManagementError):
    pass


class SpeakerManagementValidationError(SpeakerManagementError):
    pass


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SpeakerManagementValidationError(
            f"{label} must be non-empty text."
        )
    return value.strip()


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SpeakerManagementValidationError(
            f"{label} must be a JSON object."
        )
    return value


def _require_index_list(value: Any, label: str) -> list[int]:
    if not isinstance(value, list) or not value:
        raise SpeakerManagementValidationError(
            f"{label} must be a non-empty JSON array."
        )
    result = []
    for item in value:
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            raise SpeakerManagementValidationError(
                f"{label} must contain non-negative integers."
            )
        result.append(item)
    if len(result) != len(set(result)):
        raise SpeakerManagementValidationError(
            f"{label} must not contain duplicates."
        )
    return sorted(result)


def _ordered_unique(values: list[Any]) -> list[Any]:
    result = []
    seen = set()
    for value in values:
        key = fingerprint_value(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(copy.deepcopy(value))
    return result


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return copy.deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SpeakerManagementValidationError(
            f"Could not read {path.name}: {exc}"
        ) from exc


def _script_path(root: Path) -> Path:
    return root / "annotated_script.json"


def _metadata_path(root: Path) -> Path:
    return root / "annotated_script.meta.json"


def _chunks_path(root: Path) -> Path:
    return root / "chunks.json"


def _voice_config_path(root: Path) -> Path:
    return root / "voice_config.json"


def _roster_path(root: Path) -> Path:
    return root / "character_roster.json"


def _generation_state_path(root: Path) -> Path:
    return root / "generation_state.json"


def _history_root(root: Path) -> Path:
    return root / _HISTORY_DIRNAME


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


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
        "sha256": fingerprint_text(content.decode("latin-1")),
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def _snapshot_hash(snapshot: dict[str, Any]) -> str | None:
    return snapshot.get("sha256")


def _current_hash(path: Path) -> str | None:
    return _snapshot_hash(_snapshot(path))


def _entry_map(roster: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["id"]: entry for entry in roster["entries"]}


def _require_entry(
    roster: dict[str, Any],
    entry_id: Any,
) -> dict[str, Any]:
    safe_id = _require_text(entry_id, "entry_id")
    entry = _entry_map(roster).get(safe_id)
    if entry is None:
        raise SpeakerManagementValidationError(
            f"Approved roster entry {safe_id!r} was not found."
        )
    return entry


def _speaker(entry: dict[str, Any]) -> str:
    value = entry.get("speaker") or entry.get("type") or ""
    return value.strip() if isinstance(value, str) else str(value)


def _set_speaker(entry: dict[str, Any], speaker: str) -> None:
    if "speaker" in entry or "type" not in entry:
        entry["speaker"] = speaker
    else:
        entry["type"] = speaker


def _script_fingerprint(script: list[dict[str, Any]]) -> str:
    return fingerprint_value(script)


def inspect_speaker_lines(
    *,
    root_dir: str | Path,
    speaker: str | None = None,
) -> dict[str, Any]:
    root = Path(root_dir)
    script = _read_json(_script_path(root), default=[])
    if not isinstance(script, list):
        raise SpeakerManagementValidationError(
            "annotated_script.json must be a JSON array."
        )
    selected = []
    counts: dict[str, int] = {}
    for index, entry in enumerate(script):
        if not isinstance(entry, dict):
            raise SpeakerManagementValidationError(
                f"Script entry {index} must be a JSON object."
            )
        label = _speaker(entry)
        counts[label] = counts.get(label, 0) + 1
        if speaker is None or label == speaker:
            selected.append(
                {
                    "index": index,
                    "speaker": label,
                    "text": entry.get("text", ""),
                    "instruct": entry.get("instruct", ""),
                }
            )
    return {
        "script_fingerprint": _script_fingerprint(script),
        "entry_count": len(script),
        "speaker_counts": dict(sorted(counts.items())),
        "lines": selected,
    }


def _rebuild_roster(
    roster: dict[str, Any],
    *,
    entries: list[dict[str, Any]],
    source_text: str,
    operation: str,
    at_utc: str,
) -> dict[str, Any]:
    ids = {entry["id"] for entry in entries}
    duplicate_candidates = []
    for candidate in roster.get("duplicate_candidates", []):
        candidate_ids = [
            entry_id
            for entry_id in candidate["entry_ids"]
            if entry_id in ids
        ]
        if len(candidate_ids) == 2 and candidate_ids[0] != candidate_ids[1]:
            updated = copy.deepcopy(candidate)
            updated["entry_ids"] = candidate_ids
            duplicate_candidates.append(updated)

    for entry in entries:
        entry["possible_duplicate_ids"] = [
            entry_id
            for entry_id in entry.get("possible_duplicate_ids", [])
            if entry_id in ids and entry_id != entry["id"]
        ]
        entry["mistaken_merge_risk"] = bool(
            entry["possible_duplicate_ids"]
        )
        if (
            entry["resolution_status"] == "duplicate_candidate"
            and not entry["possible_duplicate_ids"]
        ):
            entry["resolution_status"] = (
                "resolved" if entry["canonical_name"] else "unresolved"
            )

    unresolved = []
    for entry in entries:
        if entry["resolution_status"] not in {"unresolved", "unnamed"}:
            continue
        for question in entry.get("unresolved_questions") or [
            "This identity requires explicit review."
        ]:
            unresolved.append(
                {
                    "entry_id": entry["id"],
                    "question": question,
                    "confidence": entry["confidence"],
                }
            )

    warnings = list(roster.get("warnings", []))
    warnings.append(
        f"Speaker management operation {operation} applied at {at_utc}."
    )
    draft = build_draft_roster(
        source=roster["source"],
        discovery=roster["discovery"],
        entries=entries,
        unresolved=unresolved,
        duplicate_candidates=duplicate_candidates,
        excluded_entities=roster.get("excluded_entities", []),
        warnings=_ordered_unique(warnings),
        source_text=source_text,
    )
    return build_approved_roster(
        draft,
        expected_fingerprint=draft["draft_fingerprint"],
        source_fingerprint=roster["source"]["fingerprint"],
        source_text=source_text,
        acknowledged_unresolved=bool(unresolved),
        approved_at_utc=at_utc,
    )


def _rename_script_speaker(
    script: list[dict[str, Any]],
    *,
    old: str,
    new: str,
) -> list[int]:
    changed = []
    for index, entry in enumerate(script):
        if _speaker(entry) == old:
            _set_speaker(entry, new)
            changed.append(index)
    return changed


def _reassign_script_indices(
    script: list[dict[str, Any]],
    *,
    indices: list[int],
    new_speaker: str,
    expected_speaker: str | None = None,
) -> list[int]:
    changed = []
    for index in indices:
        if index >= len(script):
            raise SpeakerManagementValidationError(
                f"Script entry index {index} is out of range."
            )
        current = _speaker(script[index])
        if expected_speaker is not None and current != expected_speaker:
            raise SpeakerManagementConflictError(
                f"Script entry {index} is {current!r}, not {expected_speaker!r}."
            )
        if current != new_speaker:
            _set_speaker(script[index], new_speaker)
            changed.append(index)
    return changed


def _voice_config_rename(
    voice_config: dict[str, Any],
    *,
    old: str,
    new: str,
    resolution: str | None,
) -> None:
    old_value = voice_config.get(old)
    new_value = voice_config.get(new)
    if old_value is not None and new_value is not None and old != new:
        if resolution not in {"old", "new", "clear"}:
            raise SpeakerManagementConflictError(
                "Both speaker names have voice configurations. Set "
                "voice_resolution to 'old', 'new', or 'clear'."
            )
        if resolution == "old":
            voice_config[new] = copy.deepcopy(old_value)
        elif resolution == "clear":
            voice_config.pop(new, None)
    elif old_value is not None and old != new:
        voice_config[new] = copy.deepcopy(old_value)

    if old != new and old in voice_config:
        voice_config[old] = {"alias_of": new}
    for name, config in list(voice_config.items()):
        if not isinstance(config, dict):
            continue
        if config.get("alias_of") == old:
            config["alias_of"] = new
        if config.get("alias") == old:
            config["alias"] = new


def _merge_roster_entries(
    primary: dict[str, Any],
    secondary: dict[str, Any],
) -> dict[str, Any]:
    merged = copy.deepcopy(primary)
    for field in (
        "titles",
        "aliases",
        "nicknames",
        "pronouns",
        "species",
        "relationships",
        "additional_evidence_locations",
        "unresolved_questions",
        "voice_clues",
        "sample_lines",
    ):
        merged[field] = _ordered_unique(
            [*merged.get(field, []), *secondary.get(field, [])]
        )
    for value in (
        secondary.get("canonical_name"),
        secondary.get("display_name"),
    ):
        if (
            isinstance(value, str)
            and value.strip()
            and value.casefold()
            not in {
                merged.get("canonical_name", "").casefold(),
                merged.get("display_name", "").casefold(),
            }
        ):
            merged["aliases"] = _ordered_unique(
                [*merged["aliases"], value]
            )
    merged["evidence"] = _ordered_unique(
        [*merged.get("evidence", []), *secondary.get("evidence", [])]
    )
    merged["possible_duplicate_ids"] = _ordered_unique(
        [
            entry_id
            for entry_id in (
                *merged.get("possible_duplicate_ids", []),
                *secondary.get("possible_duplicate_ids", []),
            )
            if entry_id not in {primary["id"], secondary["id"]}
        ]
    )
    merged["mistaken_merge_risk"] = bool(
        merged["possible_duplicate_ids"]
    )
    merged["confidence"] = max(
        float(primary.get("confidence", 0.0)),
        float(secondary.get("confidence", 0.0)),
    )
    if merged.get("canonical_name"):
        merged["resolution_status"] = "resolved"
        merged["unresolved_questions"] = []
    return merged


def _merge_visuals(
    first: dict[str, Any],
    second: dict[str, Any],
    *,
    source_text: str,
) -> dict[str, Any]:
    profile = {bucket: [] for bucket in PROFILE_BUCKETS}
    for bucket in PROFILE_BUCKETS:
        profile[bucket] = _ordered_unique(
            [
                *first.get("profile", {}).get(bucket, []),
                *second.get("profile", {}).get(bucket, []),
            ]
        )
    return build_visual_dossier(
        observations=_ordered_unique(
            [
                *first.get("observations", []),
                *second.get("observations", []),
            ]
        ),
        profile=profile,
        variants=_ordered_unique(
            [*first.get("variants", []), *second.get("variants", [])]
        ),
        conflicts=_ordered_unique(
            [*first.get("conflicts", []), *second.get("conflicts", [])]
        ),
        unknowns=_ordered_unique(
            [*first.get("unknowns", []), *second.get("unknowns", [])]
        ),
        source_text=source_text,
    )


def _persona_refs(
    root: Path,
) -> list[tuple[Path, dict[str, Any]]]:
    directory = root / "persona_refs"
    if not directory.exists():
        return []
    result = []
    for path in sorted(directory.glob("*.json")):
        try:
            result.append((path, load_persona_reference(path)))
        except Exception as exc:
            raise SpeakerManagementValidationError(
                f"Could not inspect persona reference {path.name}: {exc}"
            ) from exc
    return result


def _persona_updates_for_rename(
    root: Path,
    *,
    entry_id: str,
    old_name: str,
    new_name: str,
    roster_fingerprint: str,
) -> dict[Path, Any]:
    changes = {}
    for path, reference in _persona_refs(root):
        owned = reference.get("roster_entry_id") == entry_id
        legacy = reference.get("name") == old_name
        if not owned and not legacy:
            if reference.get("visual_roster_fingerprint") is not None:
                reference["visual_roster_fingerprint"] = roster_fingerprint
                changes[path] = reference
            continue
        reference["name"] = new_name
        reference["aliases"] = _ordered_unique(
            [*reference.get("aliases", []), old_name]
        )
        reference["roster_entry_id"] = entry_id
        if reference.get("visual_roster_fingerprint") is not None:
            reference["visual_roster_fingerprint"] = roster_fingerprint
        changes[path] = reference
    return changes


def _persona_updates_for_merge(
    root: Path,
    *,
    primary: dict[str, Any],
    secondary: dict[str, Any],
    roster_fingerprint: str,
    source_text: str,
    at_utc: str,
) -> dict[Path, Any]:
    refs = _persona_refs(root)
    primary_item = next(
        (
            item
            for item in refs
            if item[1].get("roster_entry_id") == primary["id"]
            or item[1].get("name") == primary["canonical_name"]
        ),
        None,
    )
    secondary_item = next(
        (
            item
            for item in refs
            if item[1].get("roster_entry_id") == secondary["id"]
            or item[1].get("name") == secondary["canonical_name"]
        ),
        None,
    )
    changes: dict[Path, Any] = {}
    if primary_item is not None:
        primary_path, primary_ref = primary_item
    elif secondary_item is not None:
        primary_path = secondary_item[0]
        primary_ref = copy.deepcopy(secondary_item[1])
    else:
        primary_path = root / "persona_refs" / (
            primary["id"] + ".json"
        )
        primary_ref = {
            "name": primary["canonical_name"],
            "aliases": [],
            "features": [],
            "personality": [],
            "voice_clues": [],
            "relationships": [],
            "sample_lines": [],
            "observations": [],
        }
    primary_ref = copy.deepcopy(primary_ref)
    if secondary_item is not None:
        secondary_path, secondary_ref = secondary_item
        for field in (
            "aliases",
            "features",
            "personality",
            "voice_clues",
            "relationships",
            "sample_lines",
            "observations",
        ):
            primary_ref[field] = _ordered_unique(
                [
                    *primary_ref.get(field, []),
                    *secondary_ref.get(field, []),
                ]
            )
        if "visual" in secondary_ref:
            if "visual" in primary_ref:
                primary_ref["visual"] = _merge_visuals(
                    primary_ref["visual"],
                    secondary_ref["visual"],
                    source_text=source_text,
                )
            else:
                primary_ref["visual"] = copy.deepcopy(
                    secondary_ref["visual"]
                )
        if secondary_path != primary_path:
            retired = copy.deepcopy(secondary_ref)
            retired["merged_into_roster_entry_id"] = primary["id"]
            retired["superseded_at_utc"] = at_utc
            changes[secondary_path] = retired
    primary_ref["name"] = primary["canonical_name"]
    primary_ref["aliases"] = _ordered_unique(
        [
            *primary_ref.get("aliases", []),
            secondary["canonical_name"],
            secondary["display_name"],
        ]
    )
    primary_ref["roster_entry_id"] = primary["id"]
    if primary_ref.get("visual_roster_fingerprint") is not None:
        primary_ref["visual_roster_fingerprint"] = roster_fingerprint
    changes[primary_path] = primary_ref
    for path, reference in refs:
        if path in changes:
            continue
        if reference.get("visual_roster_fingerprint") is not None:
            reference["visual_roster_fingerprint"] = roster_fingerprint
            changes[path] = reference
    return changes


def _refresh_voice_projects(
    root: Path,
    *,
    roster: dict[str, Any],
    source_text: str,
    merged_secondary_id: str | None = None,
    merge_resolution: str | None = None,
    primary_id: str | None = None,
    at_utc: str,
) -> dict[Path, Any]:
    projects_root = root / "voice_training_projects"
    if not projects_root.exists():
        return {}
    entries = _entry_map(roster)
    changes: dict[Path, Any] = {}
    retired_root = projects_root / "_retired"
    for path in sorted(projects_root.glob("*/project.json")):
        entry_id = path.parent.name
        entry = entries.get(entry_id)
        if entry is not None and entry.get("resolution_status") == "resolved":
            continue
        project = read_voice_training_project(path)
        retired_path = (
            retired_root
            / entry_id
            / at_utc.replace(":", "-")
            / "project.json"
        )
        retired = copy.deepcopy(project)
        retired["retired_at_utc"] = at_utc
        retired["retirement_reason"] = (
            "identity_excluded"
            if entry is None
            else "identity_marked_unresolved"
        )
        changes[retired_path] = retired
        changes[path] = None
    secondary_project = None
    secondary_path = None
    if merged_secondary_id is not None:
        secondary_path = voice_training_project_path(
            projects_root,
            merged_secondary_id,
        )
        if secondary_path.exists():
            secondary_project = read_voice_training_project(secondary_path)

    primary_path = (
        voice_training_project_path(projects_root, primary_id)
        if primary_id is not None
        else None
    )
    primary_project = (
        read_voice_training_project(primary_path)
        if primary_path is not None and primary_path.exists()
        else None
    )
    if primary_project is not None and secondary_project is not None:
        if merge_resolution not in {"primary", "secondary", "clear"}:
            raise SpeakerManagementConflictError(
                "Both merged identities have voice-training projects. Set "
                "voice_project_resolution to 'primary', 'secondary', or 'clear'."
            )
    if secondary_project is not None and primary_id is not None:
        if primary_project is None and merge_resolution != "clear":
            primary_project = copy.deepcopy(secondary_project)
        elif merge_resolution == "secondary":
            primary_project = copy.deepcopy(secondary_project)
        retired_path = (
            retired_root
            / merged_secondary_id
            / at_utc.replace(":", "-")
            / "project.json"
        )
        changes[retired_path] = secondary_project
        changes[secondary_path] = None
    if merge_resolution == "clear" and primary_path is not None:
        if primary_project is not None:
            retired_path = (
                retired_root
                / primary_id
                / at_utc.replace(":", "-")
                / "project.json"
            )
            changes[retired_path] = primary_project
        changes[primary_path] = None
        primary_project = None

    active_projects: dict[str, tuple[Path, dict[str, Any]]] = {}
    for entry_id in entries:
        path = voice_training_project_path(projects_root, entry_id)
        if path == primary_path and primary_project is not None:
            active_projects[entry_id] = (path, primary_project)
        elif path.exists() and path not in changes:
            active_projects[entry_id] = (
                path,
                read_voice_training_project(path),
            )
    if primary_id is not None and primary_project is not None:
        active_projects[primary_id] = (primary_path, primary_project)

    for entry_id, (path, project) in active_projects.items():
        entry = entries[entry_id]
        updated = copy.deepcopy(project)
        updated["character"].update(
            {
                "id": entry_id,
                "canonical_name": entry["canonical_name"],
                "display_name": entry["display_name"],
                "entity_kind": entry["entity_kind"],
                "speaking_status": entry["speaking_status"],
                "resolution_status": entry["resolution_status"],
                "source_fingerprint": roster["source"]["fingerprint"],
                "roster_fingerprint": roster["roster_fingerprint"],
            }
        )
        updated["updated_at_utc"] = at_utc
        updated["project_fingerprint"] = "0" * 64
        updated["project_fingerprint"] = (
            compute_voice_training_project_fingerprint(updated)
        )
        changes[path] = validate_voice_training_project(updated)
    return changes


def _reconcile_chunks(
    old_chunks: list[dict[str, Any]],
    script: list[dict[str, Any]],
    *,
    operation_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped = group_into_chunks(script)
    exact: dict[str, list[dict[str, Any]]] = {}
    content_only: dict[str, list[dict[str, Any]]] = {}
    for chunk in old_chunks:
        exact_key = fingerprint_value(
            {
                "speaker": chunk.get("speaker"),
                "text": chunk.get("text"),
                "instruct": chunk.get("instruct", ""),
                "pause_after": chunk.get("pause_after"),
            }
        )
        content_key = fingerprint_value(
            {
                "text": chunk.get("text"),
                "instruct": chunk.get("instruct", ""),
                "pause_after": chunk.get("pause_after"),
            }
        )
        exact.setdefault(exact_key, []).append(chunk)
        content_only.setdefault(content_key, []).append(chunk)

    audio_invalidations = []
    reconciled = []
    for index, chunk in enumerate(grouped):
        updated = copy.deepcopy(chunk)
        updated["id"] = index
        exact_key = fingerprint_value(
            {
                "speaker": updated.get("speaker"),
                "text": updated.get("text"),
                "instruct": updated.get("instruct", ""),
                "pause_after": updated.get("pause_after"),
            }
        )
        candidates = exact.get(exact_key, [])
        if candidates:
            old = candidates.pop(0)
            for field in (
                "status",
                "audio_path",
                "audio_state",
                "stale_audio_path",
                "audio_fingerprint",
                "audio_sha256",
                "audio_size_bytes",
                "audio_duration_ms",
                "audio_format",
                "invalidated_by_operation",
                "error",
                "duration",
                *synthesis_receipt_reset_fields().keys(),
            ):
                if field in old:
                    updated[field] = copy.deepcopy(old[field])
        else:
            content_key = fingerprint_value(
                {
                    "text": updated.get("text"),
                    "instruct": updated.get("instruct", ""),
                    "pause_after": updated.get("pause_after"),
                }
            )
            old_candidates = content_only.get(content_key, [])
            old = old_candidates.pop(0) if old_candidates else None
            updated["status"] = "pending"
            updated["audio_path"] = None
            previous_audio = None
            if old is not None:
                previous_audio = old.get("audio_path") or old.get("stale_audio_path")
            updated["audio_state"] = "stale" if previous_audio else "pending"
            updated["audio_fingerprint"] = None
            updated["audio_sha256"] = None
            updated["audio_size_bytes"] = None
            updated["audio_duration_ms"] = None
            updated["audio_format"] = None
            updated.update(synthesis_receipt_reset_fields())
            if previous_audio:
                updated["stale_audio_path"] = previous_audio
                updated["invalidated_by_operation"] = operation_id
                audio_invalidations.append(
                    {
                        "old_chunk_id": old.get("id"),
                        "new_chunk_id": index,
                        "audio_path": previous_audio,
                        "reason": "speaker or chunk grouping changed",
                    }
                )
        reconciled.append(updated)
    return reconciled, audio_invalidations


def _update_metadata(
    metadata: dict[str, Any] | None,
    *,
    script: list[dict[str, Any]],
    operation_id: str,
    at_utc: str,
) -> dict[str, Any] | None:
    if metadata is None:
        return None
    updated = copy.deepcopy(metadata)
    result = updated.setdefault("result", {})
    result["script_fingerprint"] = fingerprint_value(script)
    result["entry_count"] = len(script)
    result["speaker_labels"] = sorted(
        {_speaker(entry) or "UNKNOWN" for entry in script}
    )
    management = updated.setdefault("speaker_management", {})
    management["last_operation_id"] = operation_id
    management["updated_at_utc"] = at_utc
    return updated


def _audio_validity(
    *,
    operation_id: str,
    operation: str,
    invalidations: list[dict[str, Any]],
    at_utc: str,
) -> dict[str, Any]:
    return build_audio_validity_record(
        operation_id=operation_id,
        operation=operation,
        at_utc=at_utc,
        invalidations=invalidations,
        default_reason="speaker or chunk grouping changed",
        note=(
            "Invalidated production audio was moved to this speaker operation's "
            "content-addressed backup. Chunks must be regenerated before final merge."
        ),
    )


def _prepare_operation(
    *,
    root: Path,
    operation: str,
    payload: dict[str, Any],
    expected_script_fingerprint: str,
    at_utc: str,
) -> tuple[dict[Path, Any], dict[str, Any]]:
    script = _read_json(_script_path(root), default=None)
    if not isinstance(script, list):
        raise SpeakerManagementValidationError(
            "annotated_script.json is required and must be a JSON array."
        )
    current_fingerprint = _script_fingerprint(script)
    if expected_script_fingerprint != current_fingerprint:
        raise SpeakerManagementConflictError(
            "The script changed after this operation was loaded. Refresh and retry."
        )
    approved_roster, source_text, _ = load_project_roster_context(
        root_dir=root,
    )
    if approved_roster is None or source_text is None:
        raise SpeakerManagementValidationError(
            "An approved character roster and selected source are required."
        )
    roster = copy.deepcopy(approved_roster)
    roster_entries = copy.deepcopy(roster["entries"])
    working_roster = {
        **copy.deepcopy(roster),
        "entries": roster_entries,
    }
    entries_by_id = {entry["id"]: entry for entry in roster_entries}
    working_script = copy.deepcopy(script)
    script_speakers, line_speakers, speaker_counts = (
        build_script_voice_index(working_script)
    )
    script_mapping_by_id = {
        entry["id"]: resolve_script_voice_name(
            entry,
            speakers=script_speakers,
            line_speakers=line_speakers,
            speaker_counts=speaker_counts,
        )
        for entry in roster_entries
    }

    def mapped_script_voice(
        entry: dict[str, Any],
        *,
        required: bool = True,
    ) -> str:
        mapping = script_mapping_by_id.get(entry["id"], {})
        value = str(mapping.get("script_voice_name") or "").strip()
        if value:
            return value
        if not required:
            return str(entry.get("canonical_name") or "").strip()
        candidates = mapping.get("script_voice_candidates") or []
        if mapping.get("script_voice_mapping") == "ambiguous":
            raise SpeakerManagementConflictError(
                f"{entry['display_name']} matches more than one Script voice "
                f"label: {', '.join(candidates)}. Resolve the identity mapping "
                "before changing speakers."
            )
        raise SpeakerManagementValidationError(
            f"{entry['display_name']} has no linked Script voice label."
        )

    def mapped_expected_speaker(value: Any) -> Any:
        if not isinstance(value, str) or not value.strip():
            return value
        requested = value.strip().casefold()
        for entry in roster_entries:
            mapping = script_mapping_by_id.get(entry["id"], {})
            labels = {
                str(entry.get("canonical_name") or "").casefold(),
                str(entry.get("display_name") or "").casefold(),
                str(mapping.get("script_voice_name") or "").casefold(),
            }
            if requested in labels:
                return mapping.get("script_voice_name") or value
        return value

    voice_config = _read_json(_voice_config_path(root), default={})
    if not isinstance(voice_config, dict):
        raise SpeakerManagementValidationError(
            "voice_config.json must be a JSON object."
        )
    voice_config = copy.deepcopy(voice_config)
    changed_indices: list[int] = []
    affected_speakers: list[str] = []
    merged_secondary_id = None
    primary_id = None
    voice_project_resolution = None
    renamed_old_canonical_name = None

    if operation == "add":
        try:
            add_result = prepare_speaker_add(
                payload=payload,
                context=SpeakerAddContext(
                    working_script=working_script,
                    roster=roster,
                    roster_entries=roster_entries,
                    script_mapping_by_id=script_mapping_by_id,
                    voice_config=voice_config,
                ),
            )
        except SpeakerAddConflictError as exc:
            raise SpeakerManagementConflictError(str(exc)) from exc
        except SpeakerAddValidationError as exc:
            raise SpeakerManagementValidationError(str(exc)) from exc
        roster_entries = add_result.roster_entries
        working_roster["entries"] = roster_entries
        voice_config = add_result.voice_config
        affected_speakers = list(add_result.affected_speakers)

    elif operation == "resolve":
        entry = _require_entry(
            working_roster,
            payload.get("entry_id"),
        )
        if not str(entry.get("canonical_name") or "").strip():
            raise SpeakerManagementValidationError(
                "An unnamed identity must be renamed before it can be resolved."
            )
        entry["resolution_status"] = "resolved"
        entry["unresolved_questions"] = []
        target_voice = mapped_script_voice(entry, required=False)
        affected_speakers = _ordered_unique(
            [target_voice, entry["canonical_name"], entry["display_name"]]
        )

    elif operation == "rename":
        entry = _require_entry(
            working_roster,
            payload.get("entry_id"),
        )
        old_name = entry["canonical_name"]
        renamed_old_canonical_name = old_name
        old_script_voice = mapped_script_voice(entry)
        new_name = _require_text(payload.get("new_name"), "new_name")
        new_display = str(payload.get("display_name") or new_name).strip()
        if any(
            other["id"] != entry["id"]
            and other["canonical_name"].casefold() == new_name.casefold()
            for other in roster_entries
        ):
            raise SpeakerManagementConflictError(
                f"Another roster entry already uses {new_name!r}."
            )
        preserve_old = payload.get("preserve_old_as_alias", True)
        entry["canonical_name"] = new_name
        entry["display_name"] = new_display
        if payload.get("resolve") is True:
            entry["resolution_status"] = "resolved"
            entry["unresolved_questions"] = []
        if preserve_old and old_name.casefold() != new_name.casefold():
            entry["aliases"] = _ordered_unique(
                [
                    *entry.get("aliases", []),
                    old_name,
                    *(
                        [old_script_voice]
                        if old_script_voice.casefold()
                        not in {old_name.casefold(), new_name.casefold()}
                        else []
                    ),
                ]
            )
        changed_indices = _rename_script_speaker(
            working_script,
            old=old_script_voice,
            new=new_name,
        )
        _voice_config_rename(
            voice_config,
            old=old_script_voice,
            new=new_name,
            resolution=payload.get("voice_resolution"),
        )
        affected_speakers = _ordered_unique(
            [old_script_voice, old_name, new_name]
        )

    elif operation == "add_alias":
        entry = _require_entry(
            working_roster,
            payload.get("entry_id"),
        )
        alias = _require_text(payload.get("alias"), "alias")
        if alias.casefold() in {
            other["canonical_name"].casefold()
            for other in roster_entries
            if other["id"] != entry["id"]
        }:
            raise SpeakerManagementConflictError(
                "The alias is another character's canonical name."
            )
        entry["aliases"] = _ordered_unique(
            [*entry.get("aliases", []), alias]
        )
        target_voice = mapped_script_voice(entry, required=False)
        voice_config.setdefault(alias, {"alias_of": target_voice})
        if isinstance(voice_config[alias], dict):
            voice_config[alias]["alias_of"] = target_voice
        affected_speakers = _ordered_unique(
            [target_voice, entry["canonical_name"], alias]
        )

    elif operation == "remove_alias":
        entry = _require_entry(
            working_roster,
            payload.get("entry_id"),
        )
        alias = _require_text(payload.get("alias"), "alias")
        before = list(entry.get("aliases", []))
        entry["aliases"] = [
            value
            for value in before
            if value.casefold() != alias.casefold()
        ]
        if len(before) == len(entry["aliases"]):
            raise SpeakerManagementValidationError(
                f"Alias {alias!r} was not found."
            )
        target_voice = mapped_script_voice(entry, required=False)
        if payload.get("remove_voice_alias"):
            config = voice_config.get(alias)
            if isinstance(config, dict) and config.get("alias_of") in {
                target_voice,
                entry["canonical_name"],
            }:
                voice_config.pop(alias, None)
        affected_speakers = _ordered_unique(
            [target_voice, entry["canonical_name"], alias]
        )

    elif operation == "mark_unresolved":
        entry = _require_entry(
            working_roster,
            payload.get("entry_id"),
        )
        question = _require_text(
            payload.get("question") or payload.get("reason"),
            "question",
        )
        entry["resolution_status"] = "unresolved"
        entry["unresolved_questions"] = _ordered_unique(
            [*entry.get("unresolved_questions", []), question]
        )
        target_voice = mapped_script_voice(entry, required=False)
        affected_speakers = _ordered_unique(
            [target_voice, entry["canonical_name"], entry["display_name"]]
        )

    elif operation == "merge":
        primary = _require_entry(
            working_roster,
            payload.get("primary_entry_id"),
        )
        secondary = _require_entry(
            working_roster,
            payload.get("secondary_entry_id"),
        )
        if primary["id"] == secondary["id"]:
            raise SpeakerManagementValidationError(
                "Merge entries must be different."
            )
        primary_id = primary["id"]
        merged_secondary_id = secondary["id"]
        primary_script_voice = mapped_script_voice(primary)
        secondary_script_voice = mapped_script_voice(secondary)
        voice_project_resolution = payload.get("voice_project_resolution")
        merged = _merge_roster_entries(primary, secondary)
        roster_entries = [
            merged if item["id"] == primary["id"] else item
            for item in roster_entries
            if item["id"] != secondary["id"]
        ]
        changed_indices = _rename_script_speaker(
            working_script,
            old=secondary_script_voice,
            new=primary_script_voice,
        )
        _voice_config_rename(
            voice_config,
            old=secondary_script_voice,
            new=primary_script_voice,
            resolution=payload.get("voice_resolution"),
        )
        affected_speakers = _ordered_unique(
            [
                primary_script_voice,
                secondary_script_voice,
                primary["canonical_name"],
                secondary["canonical_name"],
            ]
        )

    elif operation == "exclude":
        entry = _require_entry(
            working_roster,
            payload.get("entry_id"),
        )
        mapping = script_mapping_by_id.get(entry["id"], {})
        line_count = int(mapping.get("script_line_count") or 0)
        if line_count:
            raise SpeakerManagementValidationError(
                f"{entry['display_name']} still owns {line_count} Script "
                "line(s). Merge it into the correct identity or reassign those "
                "lines before excluding it."
            )
        reason = _require_text(
            payload.get("reason")
            or "Excluded because this entity is not part of the canonical Cast.",
            "reason",
        )
        roster_entries = [
            item for item in roster_entries if item["id"] != entry["id"]
        ]
        roster.setdefault("excluded_entities", []).append(
            {
                "name": entry.get("display_name")
                or entry.get("canonical_name")
                or entry["id"],
                "reason": reason,
                "evidence": copy.deepcopy(entry.get("evidence") or []),
            }
        )
        target_voice = mapped_script_voice(entry, required=False)
        affected_speakers = _ordered_unique(
            [target_voice, entry["canonical_name"], entry["display_name"]]
        )

    elif operation == "split":
        original = _require_entry(
            working_roster,
            payload.get("entry_id"),
        )
        indices = _require_index_list(
            payload.get("entry_indices"),
            "entry_indices",
        )
        evidence_indexes = _require_index_list(
            payload.get("evidence_indexes"),
            "evidence_indexes",
        )
        if any(index >= len(original["evidence"]) for index in evidence_indexes):
            raise SpeakerManagementValidationError(
                "A split evidence index is out of range."
            )
        if len(evidence_indexes) >= len(original["evidence"]):
            raise SpeakerManagementValidationError(
                "A split must leave supporting evidence with the original identity."
            )
        new_name = _require_text(payload.get("new_name"), "new_name")
        if any(
            item["canonical_name"].casefold() == new_name.casefold()
            for item in roster_entries
        ):
            raise SpeakerManagementConflictError(
                f"Another roster entry already uses {new_name!r}."
            )
        new_id = stable_entry_id(
            "split:"
            + roster["source"]["fingerprint"]
            + ":"
            + original["id"]
            + ":"
            + ",".join(str(index) for index in indices)
            + ":"
            + new_name
        )
        moved_evidence = [
            copy.deepcopy(original["evidence"][index])
            for index in evidence_indexes
        ]
        original["evidence"] = [
            item
            for index, item in enumerate(original["evidence"])
            if index not in set(evidence_indexes)
        ]
        new_entry = {
            "id": new_id,
            "canonical_name": new_name,
            "display_name": str(payload.get("display_name") or new_name).strip(),
            "entity_kind": original["entity_kind"],
            "speaking_status": "speaker",
            "titles": list(payload.get("titles") or []),
            "aliases": list(payload.get("aliases") or []),
            "nicknames": list(payload.get("nicknames") or []),
            "pronouns": list(payload.get("pronouns") or []),
            "species": list(payload.get("species") or []),
            "relationships": list(payload.get("relationships") or []),
            "first_evidence_location": moved_evidence[0]["source_location"],
            "additional_evidence_locations": _ordered_unique(
                [item["source_location"] for item in moved_evidence[1:]]
            ),
            "confidence": float(payload.get("confidence", original["confidence"])),
            "resolution_status": "resolved",
            "possible_duplicate_ids": [],
            "mistaken_merge_risk": False,
            "unresolved_questions": [],
            "evidence": moved_evidence,
            "voice_clues": list(payload.get("voice_clues") or []),
            "sample_lines": [
                str(working_script[index].get("text", ""))
                for index in indices
                if str(working_script[index].get("text", "")).strip()
            ][:50],
        }
        roster_entries.append(new_entry)
        original_script_voice = mapped_script_voice(original)
        changed_indices = _reassign_script_indices(
            working_script,
            indices=indices,
            new_speaker=new_name,
            expected_speaker=original_script_voice,
        )
        affected_speakers = _ordered_unique(
            [original_script_voice, original["canonical_name"], new_name]
        )

    elif operation == "reassign":
        target = _require_entry(
            working_roster,
            payload.get("target_entry_id"),
        )
        indices = payload.get("entry_indices")
        if indices is None:
            start = payload.get("start_index")
            end = payload.get("end_index")
            if (
                not isinstance(start, int)
                or isinstance(start, bool)
                or not isinstance(end, int)
                or isinstance(end, bool)
                or start < 0
                or end < start
            ):
                raise SpeakerManagementValidationError(
                    "Provide entry_indices or a valid inclusive start/end range."
                )
            indices = list(range(start, end + 1))
        indices = _require_index_list(indices, "entry_indices")
        target_script_voice = mapped_script_voice(target)
        changed_indices = _reassign_script_indices(
            working_script,
            indices=indices,
            new_speaker=target_script_voice,
            expected_speaker=mapped_expected_speaker(
                payload.get("expected_speaker")
            ),
        )
        affected_speakers = sorted(
            {
                target_script_voice,
                target["canonical_name"],
                *[
                    _speaker(script[index])
                    for index in indices
                    if index < len(script)
                ],
            }
        )

    else:
        raise SpeakerManagementValidationError(
            f"Unsupported speaker management operation: {operation!r}."
        )

    new_roster = _rebuild_roster(
        roster,
        entries=roster_entries,
        source_text=source_text,
        operation=operation,
        at_utc=at_utc,
    )
    operation_seed = {
        "operation": operation,
        "payload": payload,
        "source_script_fingerprint": current_fingerprint,
        "result_script_fingerprint": _script_fingerprint(working_script),
        "roster_fingerprint": new_roster["roster_fingerprint"],
        "at_utc": at_utc,
    }
    operation_id = "speaker_" + fingerprint_value(operation_seed)[:24]

    old_chunks = _read_json(_chunks_path(root), default=[])
    if not isinstance(old_chunks, list):
        raise SpeakerManagementValidationError(
            "chunks.json must be a JSON array."
        )
    new_chunks, audio_invalidations = _reconcile_chunks(
        old_chunks,
        working_script,
        operation_id=operation_id,
    )
    metadata = _read_json(_metadata_path(root), default=None)
    if metadata is not None and not isinstance(metadata, dict):
        raise SpeakerManagementValidationError(
            "annotated_script.meta.json must be a JSON object."
        )

    changes: dict[Path, Any] = {
        _script_path(root): working_script,
        _chunks_path(root): new_chunks,
        _voice_config_path(root): voice_config,
        _roster_path(root): new_roster,
        root / _AUDIO_VALIDITY_FILENAME: _audio_validity(
            operation_id=operation_id,
            operation=operation,
            invalidations=audio_invalidations,
            at_utc=at_utc,
        ),
    }
    updated_metadata = _update_metadata(
        metadata,
        script=working_script,
        operation_id=operation_id,
        at_utc=at_utc,
    )
    if updated_metadata is not None:
        changes[_metadata_path(root)] = updated_metadata

    if operation == "rename":
        renamed = next(
            entry
            for entry in new_roster["entries"]
            if entry["id"] == payload["entry_id"]
        )
        old_name = renamed_old_canonical_name or affected_speakers[0]
        changes.update(
            _persona_updates_for_rename(
                root,
                entry_id=renamed["id"],
                old_name=old_name,
                new_name=renamed["canonical_name"],
                roster_fingerprint=new_roster["roster_fingerprint"],
            )
        )
    elif operation == "merge":
        primary = next(
            entry
            for entry in new_roster["entries"]
            if entry["id"] == primary_id
        )
        secondary = entries_by_id[merged_secondary_id]
        changes.update(
            _persona_updates_for_merge(
                root,
                primary=primary,
                secondary=secondary,
                roster_fingerprint=new_roster["roster_fingerprint"],
                source_text=source_text,
                at_utc=at_utc,
            )
        )
    else:
        for path, reference in _persona_refs(root):
            if reference.get("visual_roster_fingerprint") is not None:
                reference["visual_roster_fingerprint"] = new_roster[
                    "roster_fingerprint"
                ]
                changes[path] = reference

    changes.update(
        _refresh_voice_projects(
            root,
            roster=new_roster,
            source_text=source_text,
            merged_secondary_id=merged_secondary_id,
            merge_resolution=voice_project_resolution,
            primary_id=primary_id,
            at_utc=at_utc,
        )
    )

    generation_state = _generation_state_path(root)
    if generation_state.exists():
        archived = (
            _history_root(root)
            / operation_id
            / "invalidated_generation_state.json"
        )
        changes[archived] = _read_json(generation_state)
        changes[generation_state] = None

    summary = {
        "operation_id": operation_id,
        "operation": operation,
        "at_utc": at_utc,
        "affected_speakers": affected_speakers,
        "changed_script_indices": changed_indices,
        "source_script_fingerprint": current_fingerprint,
        "result_script_fingerprint": _script_fingerprint(working_script),
        "old_roster_fingerprint": roster["roster_fingerprint"],
        "new_roster_fingerprint": new_roster["roster_fingerprint"],
        "audio_invalidations": audio_invalidations,
    }
    return changes, summary


def _attach_audio_backup_state(
    *,
    root: Path,
    changes: dict[Path, Any],
    records: list[dict[str, Any]],
    operation_id: str,
) -> None:
    mapping = audio_backup_map(records)
    chunks = changes.get(_chunks_path(root))
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

    validity_path = root / _AUDIO_VALIDITY_FILENAME
    validity = changes.get(validity_path)
    if isinstance(validity, dict):
        changes[validity_path] = attach_audio_backup_evidence(
            validity,
            mapping,
        )


def _apply_transaction(
    *,
    root: Path,
    changes: dict[Path, Any],
    summary: dict[str, Any],
    audio_paths: list[str | None] | tuple[str | None, ...] = (),
) -> dict[str, Any]:
    del audio_paths
    operation_id = summary["operation_id"]
    return apply_audio_invalidation_transaction(
        project_root=root,
        operation_dir=_history_root(root) / operation_id,
        operation_id=operation_id,
        operation=summary["operation"],
        at_utc=summary["at_utc"],
        changes=changes,
        invalidations=summary.get("audio_invalidations", []),
        default_reason="speaker or chunk grouping changed",
        note=(
            "Invalidated production audio was moved to this speaker operation's "
            "content-addressed backup. Chunks must be regenerated before final merge."
        ),
        record_metadata=summary,
        record_schema_version=HISTORY_SCHEMA_VERSION,
        json_writer=atomic_json_write,
    )


def apply_speaker_operation(
    *,
    root_dir: str | Path,
    operation: str,
    expected_script_fingerprint: str,
    payload: dict[str, Any],
    at_utc: str | None = None,
) -> dict[str, Any]:
    root = Path(root_dir)
    with _MANAGEMENT_LOCK:
        changes, summary = _prepare_operation(
            root=root,
            operation=_require_text(operation, "operation"),
            payload=_require_dict(payload, "payload"),
            expected_script_fingerprint=_require_text(
                expected_script_fingerprint,
                "expected_script_fingerprint",
            ),
            at_utc=at_utc or utc_timestamp(),
        )
        return _apply_transaction(
            root=root,
            changes=changes,
            summary=summary,
            audio_paths=[
                item.get("audio_path")
                for item in summary.get("audio_invalidations", [])
            ],
        )


def load_speaker_operation(
    *,
    root_dir: str | Path,
    operation_id: str,
) -> dict[str, Any]:
    root = Path(root_dir)
    safe_id = _require_text(operation_id, "operation_id")
    path = _history_root(root) / safe_id / "operation.json"
    record = _read_json(path, default=None)
    if not isinstance(record, dict):
        raise SpeakerManagementValidationError(
            f"Speaker operation {safe_id!r} was not found."
        )
    if record.get("schema_version") != HISTORY_SCHEMA_VERSION:
        raise SpeakerManagementValidationError(
            "Unsupported speaker-management history schema."
        )
    return record


def speaker_operation_undo_blocker(
    *,
    root_dir: str | Path,
    record: dict[str, Any],
    current_hashes: dict[str, str | None] | None = None,
) -> str | None:
    if record.get("operation") == "undo":
        return "Undo audit records cannot be undone."
    files = record.get("files")
    if not isinstance(files, dict):
        return "The operation history does not contain restorable file state."
    root = Path(root_dir)
    hash_cache = current_hashes if current_hashes is not None else {}
    for relative, state in files.items():
        if not isinstance(relative, str) or not isinstance(state, dict):
            return "The operation history contains invalid file state."
        if relative not in hash_cache:
            hash_cache[relative] = _current_hash(root / relative)
        if hash_cache[relative] != state.get("after_sha256"):
            return f"Cannot undo because {relative} changed after the operation."
    audio_backups = record.get("audio_backups", [])
    if not isinstance(audio_backups, list):
        return "Speaker operation audio backups are invalid."
    try:
        validate_operation_audio_backups(
            root_dir=root,
            records=audio_backups,
            require_original_absent=True,
        )
    except AudioArtifactError as exc:
        return str(exc)
    return None


def undo_speaker_operation(
    *,
    root_dir: str | Path,
    operation_id: str,
    at_utc: str | None = None,
) -> dict[str, Any]:
    root = Path(root_dir)
    with _MANAGEMENT_LOCK:
        record = load_speaker_operation(
            root_dir=root,
            operation_id=operation_id,
        )
        if isinstance(record.get("audio_invalidation"), dict):
            undo_time = at_utc or utc_timestamp()
            try:
                restored = undo_audio_invalidation_transaction(
                    project_root=root,
                    record_path=(
                        _history_root(root)
                        / operation_id
                        / "operation.json"
                    ),
                    undone_at_utc=undo_time,
                    consume_backups=True,
                    mark_record_undone=True,
                )
            except AudioInvalidationError as exc:
                if exc.code == "audio_invalidation_undo_conflict":
                    message = str(exc).replace(
                        "changed after the invalidation",
                        "changed after the operation",
                    )
                    raise SpeakerManagementConflictError(message) from exc
                raise SpeakerManagementValidationError(str(exc)) from exc
            cleanup = restored["audio_backup_cleanup"]
            undo_id = "undo_" + fingerprint_value(
                {
                    "operation_id": operation_id,
                    "at_utc": undo_time,
                    "restored": restored["restored_files"],
                }
            )[:24]
            undo_record = {
                "schema_version": HISTORY_SCHEMA_VERSION,
                "operation_id": undo_id,
                "operation": "undo",
                "undoes_operation_id": operation_id,
                "at_utc": undo_time,
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
                    undo_time
                    if cleanup["removed_paths"]
                    or cleanup["already_missing_paths"]
                    else None
                ),
                "result_script_fingerprint": (
                    inspect_speaker_lines(root_dir=root)["script_fingerprint"]
                ),
            }
            atomic_json_write(
                undo_record,
                _history_root(root) / undo_id / "operation.json",
            )
            return undo_record
        undo_blocker = speaker_operation_undo_blocker(
            root_dir=root,
            record=record,
        )
        if undo_blocker is not None:
            raise SpeakerManagementConflictError(undo_blocker)
        audio_backups = record.get("audio_backups", [])

        current_snapshots = {
            relative: _snapshot(root / relative)
            for relative in record["files"]
        }
        restored = []
        restored_audio = []
        try:
            for relative, state in record["files"].items():
                path = root / relative
                before = state["before"]
                if not before["exists"]:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
                else:
                    content = base64.b64decode(before["content_base64"])
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = path.with_name(path.name + ".undo.tmp")
                    temporary.write_bytes(content)
                    os.replace(temporary, path)
                restored.append(relative)
            restored_audio = restore_operation_audio(
                root_dir=root,
                records=audio_backups,
                require_original_absent=True,
                consume_backups=False,
            )
        except Exception:
            for relative, snapshot in current_snapshots.items():
                path = root / relative
                if not snapshot["exists"]:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                content = base64.b64decode(snapshot["content_base64"])
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(path.name + ".undo-recovery.tmp")
                temporary.write_bytes(content)
                os.replace(temporary, path)
            remove_restored_operation_audio(
                root_dir=root,
                records=audio_backups,
            )
            raise
        undo_time = at_utc or utc_timestamp()
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
        undo_id = "undo_" + fingerprint_value(
            {
                "operation_id": operation_id,
                "at_utc": undo_time,
                "restored": restored,
            }
        )[:24]
        undo_record = {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "operation_id": undo_id,
            "operation": "undo",
            "undoes_operation_id": operation_id,
            "at_utc": undo_time,
            "restored_files": restored,
            "restored_audio_paths": restored_audio,
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
                undo_time
                if audio_cleanup["removed_paths"]
                or audio_cleanup["already_missing_paths"]
                else None
            ),
            "result_script_fingerprint": (
                inspect_speaker_lines(root_dir=root)["script_fingerprint"]
            ),
        }
        atomic_json_write(
            undo_record,
            _history_root(root) / undo_id / "operation.json",
        )
        return undo_record
