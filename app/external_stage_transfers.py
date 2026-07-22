from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from character_roster import CharacterRosterError, read_character_roster
from external_workflows import (
    ExternalWorkflowConflictError,
    ExternalWorkflowValidationError,
    get_structured_result_candidate,
    mark_structured_result_transferred,
    utc_timestamp,
)
from generation_state import atomic_json_write, fingerprint_text, fingerprint_value
from task_bundles import get_task_definition
from roster_discovery import (
    RosterDiscoveryError,
    build_discovery_identity,
    build_draft_from_discovery_state,
    completed_observations,
    load_roster_discovery_state,
    new_roster_discovery_state,
    normalize_passage_result,
    validate_reconciliation_partition,
    validate_roster_discovery_state,
)
from visual_discovery import (
    VisualDiscoveryError,
    checkpoint_visual_passage,
    checkpoint_visual_reconciliation,
    load_visual_discovery_state,
    new_visual_discovery_state,
    normalize_visual_passage_result,
)
from voice_identity_context import (
    VoiceIdentityContextError,
    load_voice_identity_context,
)
from voice_training_api import (
    VoiceTrainingApiError,
    apply_voice_training_action_payload,
    create_voice_training_candidate_payload,
    get_voice_training_project_payload,
)
from voice_training_projects import voice_training_project_path


class ExternalStageTransferError(RuntimeError):
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


class ExternalStageTransferConflictError(ExternalStageTransferError):
    pass


class ExternalStageTransferValidationError(ExternalStageTransferError):
    pass


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _restore_bytes(path: Path, content: bytes | None) -> None:
    if content is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".external-transfer-rollback")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def _validate_candidate(
    *,
    root_dir: str | Path,
    candidate_id: str,
    expected_result_fingerprint: str,
) -> dict[str, Any]:
    try:
        candidate = get_structured_result_candidate(
            root_dir=root_dir,
            candidate_id=candidate_id,
        )
    except ExternalWorkflowValidationError as exc:
        raise ExternalStageTransferValidationError(
            exc.code,
            str(exc),
            details=exc.details,
        ) from exc
    if candidate["status"] != "inspected":
        raise ExternalStageTransferConflictError(
            "structured_result_already_transferred",
            "This result has already entered its native Alexandria workflow.",
        )
    if candidate["result_fingerprint"] != expected_result_fingerprint:
        raise ExternalStageTransferConflictError(
            "stale_structured_result",
            "The validated result changed before transfer.",
        )
    transfer = candidate.get("native_transfer") or {}
    if not transfer.get("supported"):
        raise ExternalStageTransferValidationError(
            "structured_result_transfer_unsupported",
            transfer.get("label") or "This stage has no native transfer yet.",
        )
    return candidate


def _validate_source(
    source_snapshot: dict[str, Any] | None,
    source_text: str | None,
) -> dict[str, Any]:
    if not isinstance(source_snapshot, dict) or not isinstance(source_text, str):
        raise ExternalStageTransferConflictError(
            "external_source_required",
            "The selected source is required for this roster transfer.",
        )
    required = {"path", "basename", "fingerprint", "character_count"}
    if set(source_snapshot) != required:
        raise ExternalStageTransferValidationError(
            "invalid_external_source",
            "The current source snapshot is incomplete.",
        )
    if fingerprint_text(source_text) != source_snapshot["fingerprint"]:
        raise ExternalStageTransferConflictError(
            "stale_source",
            "The selected source changed after this result was validated.",
        )
    return copy.deepcopy(source_snapshot)


def _ensure_candidate_source(
    candidate: dict[str, Any],
    source_snapshot: dict[str, Any] | None,
) -> None:
    expected = (candidate.get("snapshot") or {}).get("source_fingerprint")
    if expected is None:
        return
    current = (
        source_snapshot.get("fingerprint")
        if isinstance(source_snapshot, dict)
        else None
    )
    if current != expected:
        raise ExternalStageTransferConflictError(
            "stale_source",
            "The selected source changed after this handoff was exported.",
        )


def _artifact_fingerprint(path: Path) -> str | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExternalStageTransferValidationError(
            "external_artifact_unreadable",
            f"Could not read {path.name}: {exc}",
        ) from exc
    return fingerprint_value(value)


def _ensure_candidate_artifacts(
    candidate: dict[str, Any],
    *,
    root_dir: str | Path,
    roster_state_path: str | Path,
    roster_draft_path: str | Path,
    approved_roster_path: str | Path,
    visual_state_path: str | Path,
) -> None:
    expected = (
        (candidate.get("snapshot") or {}).get("artifact_fingerprints")
        or {}
    )
    paths = {
        "annotated_script": Path(root_dir) / "annotated_script.json",
        "character_roster": Path(approved_roster_path),
        "character_roster_draft": Path(roster_draft_path),
        "roster_discovery_state": Path(roster_state_path),
        "visual_discovery_state": Path(visual_state_path),
        "voice_config": Path(root_dir) / "voice_config.json",
    }
    for name, expected_fingerprint in expected.items():
        path = paths.get(name)
        current = _artifact_fingerprint(path) if path is not None else None
        if current != expected_fingerprint:
            raise ExternalStageTransferConflictError(
                "stale_artifact",
                f"Artifact {name!r} changed after this handoff was exported.",
                details={"artifact": name},
            )


def _mark_transferred(
    *,
    root_dir: str | Path,
    candidate: dict[str, Any],
    application: dict[str, Any],
) -> dict[str, Any]:
    try:
        return mark_structured_result_transferred(
            root_dir=root_dir,
            candidate_id=candidate["candidate_id"],
            expected_result_fingerprint=candidate["result_fingerprint"],
            application=application,
        )
    except ExternalWorkflowConflictError as exc:
        raise ExternalStageTransferConflictError(
            exc.code,
            str(exc),
            details=exc.details,
        ) from exc
    except ExternalWorkflowValidationError as exc:
        raise ExternalStageTransferValidationError(
            exc.code,
            str(exc),
            details=exc.details,
        ) from exc


def _transfer_roster_discovery(
    *,
    root_dir: str | Path,
    candidate: dict[str, Any],
    source_snapshot: dict[str, Any] | None,
    source_text: str | None,
    roster_state_path: str | Path,
    roster_draft_path: str | Path,
    approved_roster_path: str | Path,
    at_utc: str | None,
) -> dict[str, Any]:
    source = _validate_source(source_snapshot, source_text)
    state_path = Path(roster_state_path)
    draft_path = Path(roster_draft_path)
    approved_path = Path(approved_roster_path)
    if approved_path.exists():
        raise ExternalStageTransferConflictError(
            "approved_roster_exists",
            "An approved roster already exists. Review it in Character roster instead.",
        )
    if state_path.exists() or draft_path.exists():
        raise ExternalStageTransferConflictError(
            "roster_work_in_progress",
            "Character roster already has saved progress or a draft. Resolve or discard it there first.",
        )

    assert source_text is not None
    passage = {
        "index": 1,
        "start_char": 0,
        "end_char": len(source_text),
        "text": source_text,
        "fingerprint": fingerprint_text(source_text),
    }
    try:
        observations, warnings = normalize_passage_result(
            candidate["result"],
            passage=passage,
            source_fingerprint=source["fingerprint"],
        )
        identity = build_discovery_identity(
            model_name="Ordinary ChatGPT handoff",
            backend="external_chatgpt",
            passage_size=max(100, len(source_text)),
            overlap=0,
            temperature=0.0,
            max_tokens=0,
            seed=None,
            runtime_identity={
                "handoff_id": candidate["handoff_id"],
                "result_fingerprint": candidate["result_fingerprint"],
            },
        )
        state = new_roster_discovery_state(
            source=source,
            generation_identity=identity,
            passages=[passage],
        )
        completed = {
            **state,
            "completed_passages": [
                {
                    "index": 1,
                    "passage_fingerprint": passage["fingerprint"],
                    "observations": observations,
                    "warnings": warnings,
                }
            ],
        }
        completed = validate_roster_discovery_state(completed)
    except RosterDiscoveryError as exc:
        raise ExternalStageTransferValidationError(
            "roster_discovery_result_invalid",
            str(exc),
        ) from exc

    before = _read_bytes(state_path)
    try:
        atomic_json_write(completed, state_path)
        application = {
            "status": "native_review_ready",
            "destination": "character_roster",
            "tab": "characters",
            "stage": "roster_discovery",
            "observation_count": len(observations),
            "warning_count": len(warnings),
            "at_utc": at_utc,
        }
        transferred = _mark_transferred(
            root_dir=root_dir,
            candidate=candidate,
            application=application,
        )
    except Exception:
        _restore_bytes(state_path, before)
        raise
    return transferred


def _transfer_roster_reconciliation(
    *,
    root_dir: str | Path,
    candidate: dict[str, Any],
    source_snapshot: dict[str, Any] | None,
    source_text: str | None,
    roster_state_path: str | Path,
    roster_draft_path: str | Path,
    approved_roster_path: str | Path,
    at_utc: str | None,
) -> dict[str, Any]:
    source = _validate_source(source_snapshot, source_text)
    state_path = Path(roster_state_path)
    draft_path = Path(roster_draft_path)
    approved_path = Path(approved_roster_path)
    if approved_path.exists():
        raise ExternalStageTransferConflictError(
            "approved_roster_exists",
            "An approved roster already exists. Review it in Character roster instead.",
        )
    if draft_path.exists():
        raise ExternalStageTransferConflictError(
            "roster_draft_exists",
            "A roster draft already exists. Review or discard it before importing another reconciliation.",
        )
    try:
        state = load_roster_discovery_state(state_path)
    except RosterDiscoveryError as exc:
        raise ExternalStageTransferValidationError(
            "roster_state_invalid",
            str(exc),
        ) from exc
    if state is None:
        raise ExternalStageTransferConflictError(
            "roster_observations_required",
            "Validated roster observations are required before reconciliation.",
        )
    try:
        state = validate_roster_discovery_state(state)
    except RosterDiscoveryError as exc:
        raise ExternalStageTransferValidationError(
            "roster_state_invalid",
            str(exc),
        ) from exc
    if state["source"]["fingerprint"] != source["fingerprint"]:
        raise ExternalStageTransferConflictError(
            "stale_roster_state",
            "Saved roster observations belong to a different source.",
        )
    if len(state["completed_passages"]) != state["total_passages"]:
        raise ExternalStageTransferConflictError(
            "roster_observations_incomplete",
            "Finish roster discovery before importing reconciliation.",
        )
    if state["reconciliation"] is not None:
        raise ExternalStageTransferConflictError(
            "roster_reconciliation_exists",
            "Roster reconciliation has already been imported or generated.",
        )

    assert source_text is not None
    try:
        reconciliation = validate_reconciliation_partition(
            candidate["result"],
            completed_observations(state),
        )
        updated_state = validate_roster_discovery_state(
            {
                **state,
                "reconciliation": reconciliation,
            }
        )
        draft = build_draft_from_discovery_state(
            updated_state,
            source_text=source_text,
            generated_at_utc=at_utc,
        )
    except RosterDiscoveryError as exc:
        raise ExternalStageTransferValidationError(
            "roster_reconciliation_result_invalid",
            str(exc),
        ) from exc

    state_before = _read_bytes(state_path)
    draft_before = _read_bytes(draft_path)
    try:
        atomic_json_write(updated_state, state_path)
        atomic_json_write(draft, draft_path)
        application = {
            "status": "native_review_ready",
            "destination": "character_roster",
            "tab": "characters",
            "stage": "roster_reconciliation",
            "draft_fingerprint": draft["draft_fingerprint"],
            "entry_count": len(draft["entries"]),
            "at_utc": at_utc,
        }
        transferred = _mark_transferred(
            root_dir=root_dir,
            candidate=candidate,
            application=application,
        )
    except Exception:
        _restore_bytes(state_path, state_before)
        _restore_bytes(draft_path, draft_before)
        raise
    return transferred


def _find_identity_entry(
    roster: dict[str, Any],
    target: str,
) -> dict[str, Any] | None:
    wanted = target.strip().casefold()
    for entry in roster.get("entries") or []:
        labels = {
            str(entry.get(field) or "").strip().casefold()
            for field in ("id", "canonical_name", "display_name")
        }
        labels.update(
            str(value).strip().casefold()
            for field in ("aliases", "nicknames", "titles")
            for value in (entry.get(field) or [])
        )
        if wanted in labels:
            return entry
    return None


def _transfer_persona(
    *,
    root_dir: str | Path,
    candidate: dict[str, Any],
    source_snapshot: dict[str, Any] | None,
    source_text: str | None,
    approved_roster_path: str | Path,
    voice_training_projects_root: str | Path,
    replace_persona_draft: bool,
    at_utc: str | None,
) -> dict[str, Any]:
    target = str((candidate.get("target") or {}).get("value") or "").strip()
    if not target:
        raise ExternalStageTransferValidationError(
            "persona_target_missing",
            "The stored Persona handoff has no speaker target.",
        )
    current_source_fingerprint = (
        source_snapshot.get("fingerprint")
        if isinstance(source_snapshot, dict)
        else None
    )
    try:
        roster, context = load_voice_identity_context(
            approved_roster_path=approved_roster_path,
            source_text=source_text,
            current_source_fingerprint=current_source_fingerprint,
            script_path=Path(root_dir) / "annotated_script.json",
            required=True,
        )
    except VoiceIdentityContextError as exc:
        raise ExternalStageTransferConflictError(
            "persona_identity_context_required",
            str(exc),
        ) from exc
    assert roster is not None
    entry = _find_identity_entry(roster, target)
    if entry is None:
        raise ExternalStageTransferConflictError(
            "persona_target_not_found",
            f"The current speaker catalog no longer contains {target!r}.",
        )
    character_id = entry["id"]
    project_path = voice_training_project_path(
        voice_training_projects_root,
        character_id,
    )
    project_before = _read_bytes(project_path)
    result = candidate["result"]
    try:
        if project_path.exists():
            project = get_voice_training_project_payload(
                approved_roster_path=approved_roster_path,
                projects_root=voice_training_projects_root,
                character_id=character_id,
                source_text=source_text,
                current_source_fingerprint=current_source_fingerprint,
            )
            persona = project["desired_base_persona"]
            has_existing_persona = bool(
                persona.get("description") or persona.get("ref_text")
            )
            if has_existing_persona and not replace_persona_draft:
                raise ExternalStageTransferConflictError(
                    "persona_draft_exists",
                    "A Persona draft already exists for this speaker. Confirm replacement before importing another.",
                    details={
                        "character_id": character_id,
                        "target": target,
                    },
                )
            project = apply_voice_training_action_payload(
                approved_roster_path=approved_roster_path,
                projects_root=voice_training_projects_root,
                character_id=character_id,
                expected_fingerprint=project["project_fingerprint"],
                action="update_persona",
                payload={
                    "description": result["description"],
                    "ref_text": result["ref_text"],
                },
                source_text=source_text,
                current_source_fingerprint=current_source_fingerprint,
                at_utc=at_utc,
            )
        else:
            project = create_voice_training_candidate_payload(
                approved_roster_path=approved_roster_path,
                projects_root=voice_training_projects_root,
                character_id=character_id,
                priority="primary",
                desired_description=result["description"],
                desired_ref_text=result["ref_text"],
                source_text=source_text,
                current_source_fingerprint=current_source_fingerprint,
                created_at_utc=at_utc,
            )
        application = {
            "status": "native_review_ready",
            "destination": "expressive_voices",
            "tab": "voice-projects",
            "stage": candidate["task_type"],
            "character_id": character_id,
            "target": target,
            "identity_source": context["identity_source"],
            "project_fingerprint": project["project_fingerprint"],
            "at_utc": at_utc,
        }
        transferred = _mark_transferred(
            root_dir=root_dir,
            candidate=candidate,
            application=application,
        )
    except ExternalStageTransferError:
        _restore_bytes(project_path, project_before)
        raise
    except VoiceTrainingApiError as exc:
        _restore_bytes(project_path, project_before)
        error_type = (
            ExternalStageTransferConflictError
            if exc.status_code in {404, 409}
            else ExternalStageTransferValidationError
        )
        raise error_type(exc.code, exc.detail) from exc
    except Exception:
        _restore_bytes(project_path, project_before)
        raise
    return transferred


def _transfer_persona_catalog(
    *,
    root_dir: str | Path,
    candidate: dict[str, Any],
    source_snapshot: dict[str, Any] | None,
    source_text: str | None,
    approved_roster_path: str | Path,
    voice_training_projects_root: str | Path,
    persona_catalog_decision: bool,
    replace_persona_speakers: set[str],
    at_utc: str | None,
) -> dict[str, Any]:
    current_source_fingerprint = (
        source_snapshot.get("fingerprint")
        if isinstance(source_snapshot, dict)
        else None
    )
    try:
        roster, context = load_voice_identity_context(
            approved_roster_path=approved_roster_path,
            source_text=source_text,
            current_source_fingerprint=current_source_fingerprint,
            script_path=Path(root_dir) / "annotated_script.json",
            required=True,
        )
    except VoiceIdentityContextError as exc:
        raise ExternalStageTransferConflictError(
            "persona_identity_context_required",
            str(exc),
        ) from exc
    assert roster is not None
    try:
        script = json.loads(
            (Path(root_dir) / "annotated_script.json").read_text(
                encoding="utf-8"
            )
        )
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExternalStageTransferConflictError(
            "persona_catalog_script_required",
            "A readable current Script is required before importing all Personas.",
        ) from exc
    if not isinstance(script, list):
        raise ExternalStageTransferValidationError(
            "persona_catalog_script_invalid",
            "The current Script is not a JSON array.",
        )
    expected_speakers: list[str] = []
    speaker_lines: dict[str, set[str]] = {}
    for entry in script:
        if not isinstance(entry, dict):
            continue
        speaker = str(entry.get("speaker") or "").strip()
        text = str(entry.get("text") or "").strip()
        if not speaker:
            continue
        if speaker not in speaker_lines:
            expected_speakers.append(speaker)
            speaker_lines[speaker] = set()
        if text:
            speaker_lines[speaker].add(text)
    personas = list((candidate.get("result") or {}).get("personas") or [])
    returned_speakers = [str(item.get("speaker") or "") for item in personas]
    if set(returned_speakers) != set(expected_speakers) or len(
        returned_speakers
    ) != len(expected_speakers):
        missing = sorted(set(expected_speakers) - set(returned_speakers))
        unexpected = sorted(set(returned_speakers) - set(expected_speakers))
        raise ExternalStageTransferValidationError(
            "persona_catalog_incomplete",
            "The completed Persona catalog must return every current Script speaker exactly once.",
            details={"missing": missing, "unexpected": unexpected},
        )

    planned: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    snapshots: dict[Path, bytes | None] = {}
    for persona in personas:
        speaker = persona["speaker"]
        if persona["ref_text"] not in speaker_lines.get(speaker, set()):
            raise ExternalStageTransferValidationError(
                "persona_catalog_ref_text_not_exact",
                f"Persona ref_text for {speaker!r} is not one exact current Script line for that speaker.",
                details={"speaker": speaker},
            )
        entry = _find_identity_entry(roster, speaker)
        if entry is None:
            raise ExternalStageTransferConflictError(
                "persona_catalog_target_not_found",
                f"The approved Character roster no longer contains Script speaker {speaker!r}.",
                details={"speaker": speaker},
            )
        character_id = entry["id"]
        project_path = voice_training_project_path(
            voice_training_projects_root,
            character_id,
        )
        snapshots[project_path] = _read_bytes(project_path)
        project = None
        if project_path.exists():
            try:
                project = get_voice_training_project_payload(
                    approved_roster_path=approved_roster_path,
                    projects_root=voice_training_projects_root,
                    character_id=character_id,
                    source_text=source_text,
                    current_source_fingerprint=current_source_fingerprint,
                )
            except VoiceTrainingApiError as exc:
                raise ExternalStageTransferConflictError(
                    exc.code,
                    exc.detail,
                    details={"speaker": speaker, "character_id": character_id},
                ) from exc
            existing = project["desired_base_persona"]
            if existing.get("description") or existing.get("ref_text"):
                conflicts.append(
                    {
                        "speaker": speaker,
                        "character_id": character_id,
                        "current": {
                            "description": existing.get("description") or "",
                            "ref_text": existing.get("ref_text") or "",
                            "approval_status": existing.get("approval_status") or "draft",
                        },
                        "imported": {
                            "description": persona["description"],
                            "ref_text": persona["ref_text"],
                        },
                    }
                )
        planned.append(
            {
                "speaker": speaker,
                "character_id": character_id,
                "project_path": project_path,
                "project": project,
                "persona": persona,
            }
        )
    conflict_speakers = {item["speaker"] for item in conflicts}
    unknown_replacements = sorted(
        replace_persona_speakers - conflict_speakers
    )
    if unknown_replacements:
        raise ExternalStageTransferValidationError(
            "persona_catalog_replacement_invalid",
            "Replacement was requested for a speaker without a current/imported conflict.",
            details={"speakers": unknown_replacements},
        )
    if conflicts and not persona_catalog_decision:
        raise ExternalStageTransferConflictError(
            "persona_catalog_comparison_required",
            "Compare the current and imported Voice profile for each existing speaker, then choose which profiles to replace.",
            details={
                "conflicts": conflicts,
                "new_speakers": [
                    item["speaker"]
                    for item in planned
                    if item["speaker"] not in conflict_speakers
                ],
            },
        )

    created_projects: list[dict[str, Any]] = []
    kept_projects: list[dict[str, str]] = []
    try:
        for item in planned:
            project = item["project"]
            persona = item["persona"]
            if (
                item["speaker"] in conflict_speakers
                and item["speaker"] not in replace_persona_speakers
            ):
                kept_projects.append(
                    {
                        "speaker": item["speaker"],
                        "character_id": item["character_id"],
                    }
                )
                continue
            if project is None:
                updated = create_voice_training_candidate_payload(
                    approved_roster_path=approved_roster_path,
                    projects_root=voice_training_projects_root,
                    character_id=item["character_id"],
                    priority="primary",
                    desired_description=persona["description"],
                    desired_ref_text=persona["ref_text"],
                    source_text=source_text,
                    current_source_fingerprint=current_source_fingerprint,
                    created_at_utc=at_utc,
                )
            else:
                updated = apply_voice_training_action_payload(
                    approved_roster_path=approved_roster_path,
                    projects_root=voice_training_projects_root,
                    character_id=item["character_id"],
                    expected_fingerprint=project["project_fingerprint"],
                    action="update_persona",
                    payload={
                        "description": persona["description"],
                        "ref_text": persona["ref_text"],
                    },
                    source_text=source_text,
                    current_source_fingerprint=current_source_fingerprint,
                    at_utc=at_utc,
                )
            created_projects.append(
                {
                    "speaker": item["speaker"],
                    "character_id": item["character_id"],
                    "project_fingerprint": updated["project_fingerprint"],
                }
            )
        application = {
            "status": "native_review_ready",
            "destination": "expressive_voices",
            "tab": "voice-projects",
            "stage": candidate["task_type"],
            "persona_count": len(created_projects),
            "created_count": sum(
                item["speaker"] not in conflict_speakers
                for item in created_projects
            ),
            "replaced_count": sum(
                item["speaker"] in replace_persona_speakers
                for item in created_projects
            ),
            "kept_count": len(kept_projects),
            "projects": created_projects,
            "kept_projects": kept_projects,
            "warnings": list(
                (candidate.get("result") or {}).get("warnings") or []
            ),
            "identity_source": context["identity_source"],
            "at_utc": at_utc,
        }
        return _mark_transferred(
            root_dir=root_dir,
            candidate=candidate,
            application=application,
        )
    except Exception:
        for project_path, before in snapshots.items():
            _restore_bytes(project_path, before)
            if before is None:
                try:
                    project_path.parent.rmdir()
                except OSError:
                    pass
        raise


def _transfer_visual_discovery(
    *,
    root_dir: str | Path,
    candidate: dict[str, Any],
    source_snapshot: dict[str, Any] | None,
    source_text: str | None,
    approved_roster_path: str | Path,
    visual_state_path: str | Path,
    at_utc: str | None,
) -> dict[str, Any]:
    source = _validate_source(source_snapshot, source_text)
    assert source_text is not None
    try:
        roster = read_character_roster(
            approved_roster_path,
            source_text=source_text,
            expected_status="approved",
        )
    except (FileNotFoundError, CharacterRosterError) as exc:
        raise ExternalStageTransferConflictError(
            "visual_approved_roster_required",
            "An approved, source-compatible Character roster is required before visual review.",
        ) from exc
    target = str((candidate.get("target") or {}).get("value") or "").strip()
    entry = _find_identity_entry(roster, target)
    if entry is None:
        raise ExternalStageTransferConflictError(
            "visual_target_not_found",
            f"The approved roster no longer contains {target!r}.",
        )
    state_path = Path(visual_state_path)
    if state_path.exists():
        raise ExternalStageTransferConflictError(
            "visual_work_in_progress",
            "Visual dossiers already have saved observations or reconciliation progress. Open that review before replacing it.",
        )
    passage = {
        "index": 1,
        "start_char": 0,
        "end_char": len(source_text),
        "text": source_text,
        "fingerprint": fingerprint_text(source_text),
    }
    try:
        observations, warnings = normalize_visual_passage_result(
            candidate["result"],
            passage=passage,
            source_text=source_text,
            allowed_character_ids={entry["id"]},
        )
        identity = {
            "model_name": "Ordinary ChatGPT Task Bundle",
            "backend": "external_chatgpt",
            "passage_size": max(100, len(source_text)),
            "overlap_chars": 0,
            "temperature": 0.0,
            "max_tokens": 0,
            "seed": None,
            "task_id": candidate.get("task_id"),
            "result_fingerprint": candidate["result_fingerprint"],
        }
        state = new_visual_discovery_state(
            source=source,
            roster_fingerprint=roster["roster_fingerprint"],
            character_ids=[entry["id"]],
            generation_identity=identity,
            passages=[passage],
        )
    except VisualDiscoveryError as exc:
        raise ExternalStageTransferValidationError(
            "visual_discovery_result_invalid",
            str(exc),
        ) from exc
    before = _read_bytes(state_path)
    try:
        completed = checkpoint_visual_passage(
            state=state,
            path=state_path,
            passage=passage,
            observations=observations,
            warnings=warnings,
        )
        application = {
            "status": "native_review_ready",
            "destination": "visual_dossiers",
            "tab": "characters",
            "stage": "visual_discovery",
            "character_id": entry["id"],
            "observation_count": len(observations),
            "warning_count": len(warnings),
            "state_fingerprint": fingerprint_value(completed),
            "at_utc": at_utc,
        }
        transferred = _mark_transferred(
            root_dir=root_dir,
            candidate=candidate,
            application=application,
        )
    except Exception:
        _restore_bytes(state_path, before)
        raise
    return transferred


def _transfer_visual_reconciliation(
    *,
    root_dir: str | Path,
    candidate: dict[str, Any],
    source_snapshot: dict[str, Any] | None,
    source_text: str | None,
    approved_roster_path: str | Path,
    visual_state_path: str | Path,
    at_utc: str | None,
) -> dict[str, Any]:
    source = _validate_source(source_snapshot, source_text)
    assert source_text is not None
    try:
        roster = read_character_roster(
            approved_roster_path,
            source_text=source_text,
            expected_status="approved",
        )
    except (FileNotFoundError, CharacterRosterError) as exc:
        raise ExternalStageTransferConflictError(
            "visual_approved_roster_required",
            "An approved, source-compatible Character roster is required before visual reconciliation.",
        ) from exc
    state_path = Path(visual_state_path)
    try:
        state = load_visual_discovery_state(state_path)
    except VisualDiscoveryError as exc:
        raise ExternalStageTransferValidationError(
            "visual_state_invalid",
            str(exc),
        ) from exc
    if state is None:
        raise ExternalStageTransferConflictError(
            "visual_observations_required",
            "Validated visual observations are required before compilation.",
        )
    if state["source"]["fingerprint"] != source["fingerprint"]:
        raise ExternalStageTransferConflictError(
            "stale_visual_state",
            "Saved visual observations belong to a different source.",
        )
    if state["roster_fingerprint"] != roster["roster_fingerprint"]:
        raise ExternalStageTransferConflictError(
            "stale_visual_roster",
            "The approved Character roster changed after visual discovery.",
        )
    if state["reconciliation"] is not None:
        raise ExternalStageTransferConflictError(
            "visual_reconciliation_exists",
            "Visual reconciliation has already been imported or generated.",
        )
    before = _read_bytes(state_path)
    try:
        updated = checkpoint_visual_reconciliation(
            state=state,
            path=state_path,
            reconciliation=candidate["result"],
        )
        application = {
            "status": "native_review_ready",
            "destination": "visual_dossiers",
            "tab": "characters",
            "stage": "visual_reconciliation",
            "character_count": len(
                (candidate["result"] or {}).get("characters") or []
            ),
            "state_fingerprint": fingerprint_value(updated),
            "at_utc": at_utc,
        }
        transferred = _mark_transferred(
            root_dir=root_dir,
            candidate=candidate,
            application=application,
        )
    except VisualDiscoveryError as exc:
        _restore_bytes(state_path, before)
        raise ExternalStageTransferValidationError(
            "visual_reconciliation_result_invalid",
            str(exc),
        ) from exc
    except Exception:
        _restore_bytes(state_path, before)
        raise
    return transferred


def transfer_structured_result_candidate(
    *,
    root_dir: str | Path,
    candidate_id: str,
    expected_result_fingerprint: str,
    source_snapshot: dict[str, Any] | None,
    source_text: str | None,
    roster_state_path: str | Path,
    roster_draft_path: str | Path,
    approved_roster_path: str | Path,
    voice_training_projects_root: str | Path,
    visual_state_path: str | Path | None = None,
    replace_persona_draft: bool = False,
    persona_catalog_decision: bool = False,
    replace_persona_speakers: set[str] | None = None,
    at_utc: str | None = None,
) -> dict[str, Any]:
    at_utc = at_utc or utc_timestamp()
    replace_persona_speakers = set(replace_persona_speakers or set())
    visual_state_path = (
        Path(visual_state_path)
        if visual_state_path is not None
        else Path(root_dir) / "persona_visual_state.json"
    )
    candidate = _validate_candidate(
        root_dir=root_dir,
        candidate_id=candidate_id,
        expected_result_fingerprint=expected_result_fingerprint,
    )
    _ensure_candidate_source(candidate, source_snapshot)
    _ensure_candidate_artifacts(
        candidate,
        root_dir=root_dir,
        roster_state_path=roster_state_path,
        roster_draft_path=roster_draft_path,
        approved_roster_path=approved_roster_path,
        visual_state_path=visual_state_path,
    )
    task_type = candidate["task_type"]
    transfer_handler = get_task_definition(task_type).transfer.handler
    if transfer_handler == "roster_discovery":
        return _transfer_roster_discovery(
            root_dir=root_dir,
            candidate=candidate,
            source_snapshot=source_snapshot,
            source_text=source_text,
            roster_state_path=roster_state_path,
            roster_draft_path=roster_draft_path,
            approved_roster_path=approved_roster_path,
            at_utc=at_utc,
        )
    if transfer_handler == "roster_reconciliation":
        return _transfer_roster_reconciliation(
            root_dir=root_dir,
            candidate=candidate,
            source_snapshot=source_snapshot,
            source_text=source_text,
            roster_state_path=roster_state_path,
            roster_draft_path=roster_draft_path,
            approved_roster_path=approved_roster_path,
            at_utc=at_utc,
        )
    if transfer_handler == "persona_catalog":
        return _transfer_persona_catalog(
            root_dir=root_dir,
            candidate=candidate,
            source_snapshot=source_snapshot,
            source_text=source_text,
            approved_roster_path=approved_roster_path,
            voice_training_projects_root=voice_training_projects_root,
            persona_catalog_decision=persona_catalog_decision,
            replace_persona_speakers=replace_persona_speakers,
            at_utc=at_utc,
        )
    if transfer_handler == "persona_single":
        return _transfer_persona(
            root_dir=root_dir,
            candidate=candidate,
            source_snapshot=source_snapshot,
            source_text=source_text,
            approved_roster_path=approved_roster_path,
            voice_training_projects_root=voice_training_projects_root,
            replace_persona_draft=replace_persona_draft,
            at_utc=at_utc,
        )
    if transfer_handler == "visual_discovery":
        return _transfer_visual_discovery(
            root_dir=root_dir,
            candidate=candidate,
            source_snapshot=source_snapshot,
            source_text=source_text,
            approved_roster_path=approved_roster_path,
            visual_state_path=visual_state_path,
            at_utc=at_utc,
        )
    if transfer_handler == "visual_reconciliation":
        return _transfer_visual_reconciliation(
            root_dir=root_dir,
            candidate=candidate,
            source_snapshot=source_snapshot,
            source_text=source_text,
            approved_roster_path=approved_roster_path,
            visual_state_path=visual_state_path,
            at_utc=at_utc,
        )
    raise ExternalStageTransferValidationError(
        "structured_result_transfer_unsupported",
        "This stage has no native transfer yet.",
    )
