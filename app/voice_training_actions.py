from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Any

from voice_training_projects import (
    VoiceTrainingProjectError,
    VoiceTrainingProjectValidationError,
    compute_dataset_fingerprint,
    compute_persona_fingerprint,
    compute_voice_training_project_fingerprint,
    read_voice_training_project,
    save_voice_training_project,
    utc_now_text,
    validate_voice_training_project,
)


_VOICE_TRAINING_ACTION_LOCK = threading.RLock()


class VoiceTrainingActionError(RuntimeError):
    pass


class VoiceTrainingConflictError(VoiceTrainingActionError):
    pass


def _require_text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise VoiceTrainingActionError(f"{label} must be text.")
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise VoiceTrainingActionError(f"{label} must not be empty.")
    return normalized


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise VoiceTrainingActionError(f"{label} must be a JSON object.")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise VoiceTrainingActionError(f"{label} must be a JSON array.")
    return value


def _check_fingerprint(
    project: dict[str, Any],
    expected_fingerprint: str,
) -> None:
    current = project["project_fingerprint"]
    if expected_fingerprint != current:
        raise VoiceTrainingConflictError(
            "The voice-training project changed after this action was loaded. "
            "Refresh and retry with the current project fingerprint."
        )


def _check_ownership(
    project: dict[str, Any],
    *,
    expected_character_id: str | None,
    expected_source_fingerprint: str | None,
    expected_roster_fingerprint: str | None,
) -> None:
    character = project["character"]
    checks = (
        (
            expected_character_id,
            character["id"],
            "The voice-training project belongs to another character.",
        ),
        (
            expected_source_fingerprint,
            character["source_fingerprint"],
            "The voice-training project belongs to another source.",
        ),
        (
            expected_roster_fingerprint,
            character["roster_fingerprint"],
            "The voice-training project belongs to another approved roster.",
        ),
    )
    for expected, actual, message in checks:
        if expected is not None and expected != actual:
            raise VoiceTrainingConflictError(message)


def _require_no_adapter_state(project: dict[str, Any], action: str) -> None:
    if (
        project["adapter_provenance"] is not None
        or project["adapter_assignment"] is not None
    ):
        raise VoiceTrainingActionError(
            f"{action} is blocked after adapter provenance or assignment exists. "
            "Create a new candidate project instead of silently invalidating an adapter."
        )


def _require_dataset_mutable(project: dict[str, Any], action: str) -> None:
    dataset = project["dataset_project"]
    if dataset is not None and dataset["status"] in {"approved", "exported"}:
        raise VoiceTrainingActionError(
            f"{action} is blocked after dataset approval. Create a new project or "
            "explicitly rebuild the dataset rather than changing accepted source clips."
        )


def _clip_map(project: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    synthetic = project["designed_voice_project"]
    if synthetic is not None:
        for sample in synthetic["samples"]:
            result[sample["clip_id"]] = {
                "source_kind": "synthetic",
                **copy.deepcopy(sample),
            }
    recordings = project["existing_recordings"]
    if recordings is not None:
        for clip in recordings["clips"]:
            if clip["clip_id"] in result:
                raise VoiceTrainingActionError(
                    "Clip IDs must be unique across synthetic and recording paths."
                )
            result[clip["clip_id"]] = {
                "source_kind": "existing_recordings",
                **copy.deepcopy(clip),
            }
    return result


def _refresh_project(
    project: dict[str, Any],
    *,
    at_utc: str | None,
) -> dict[str, Any]:
    working = copy.deepcopy(project)
    working["updated_at_utc"] = at_utc or utc_now_text()
    working["project_fingerprint"] = "0" * 64
    working["project_fingerprint"] = (
        compute_voice_training_project_fingerprint(working)
    )
    return validate_voice_training_project(working)


def calculate_training_readiness(
    project: dict[str, Any],
) -> dict[str, Any]:
    normalized = validate_voice_training_project(project)
    blockers: list[str] = []
    warnings: list[str] = []

    if normalized["desired_base_persona"]["approval_status"] != "approved":
        blockers.append("Approve the desired base persona.")

    dataset = normalized["dataset_project"]
    dataset_fingerprint = None
    if dataset is None or dataset["status"] not in {"approved", "exported"}:
        blockers.append("Create and approve a reviewed dataset.")
    else:
        dataset_fingerprint = dataset["dataset_fingerprint"]

    if normalized["selected_reference_sample"] is None:
        blockers.append("Select an approved dataset clip as the reference sample.")

    if blockers:
        status = "not_ready"
    else:
        status = "ready_for_feasibility_review"
        warnings.append(
            "Dataset readiness does not prove Apple Silicon or LoRA training support; "
            "Phase 22 must measure feasibility before training is treated as supported."
        )

    return {
        "status": status,
        "blockers": blockers,
        "warnings": warnings,
        "dataset_fingerprint": dataset_fingerprint,
    }


def _refresh_readiness(project: dict[str, Any]) -> None:
    interim = copy.deepcopy(project)
    interim["training_readiness"] = {
        "status": "not_ready",
        "blockers": [],
        "warnings": [],
        "dataset_fingerprint": (
            interim["dataset_project"]["dataset_fingerprint"]
            if interim["dataset_project"] is not None
            else None
        ),
    }
    interim["project_fingerprint"] = "0" * 64
    interim["project_fingerprint"] = (
        compute_voice_training_project_fingerprint(interim)
    )
    project["training_readiness"] = calculate_training_readiness(interim)


def _require_synthetic_project(project: dict[str, Any]) -> dict[str, Any]:
    synthetic = project["designed_voice_project"]
    if synthetic is None:
        raise VoiceTrainingActionError(
            "No designed-voice project exists for this candidate."
        )
    return synthetic


def _require_recording_project(project: dict[str, Any]) -> dict[str, Any]:
    recordings = project["existing_recordings"]
    if recordings is None:
        raise VoiceTrainingActionError(
            "No existing-recordings project exists for this candidate."
        )
    return recordings


def _require_sample(
    synthetic: dict[str, Any],
    clip_id: str,
) -> dict[str, Any]:
    for sample in synthetic["samples"]:
        if sample["clip_id"] == clip_id:
            return sample
    raise VoiceTrainingActionError(
        f"Designed-voice sample {clip_id!r} was not found."
    )


def _require_recording_clip(
    recordings: dict[str, Any],
    clip_id: str,
) -> dict[str, Any]:
    for clip in recordings["clips"]:
        if clip["clip_id"] == clip_id:
            return clip
    raise VoiceTrainingActionError(
        f"Existing-recording clip {clip_id!r} was not found."
    )


def apply_voice_training_action(
    project: dict[str, Any],
    *,
    expected_fingerprint: str,
    action: str,
    payload: dict[str, Any] | None = None,
    expected_character_id: str | None = None,
    expected_source_fingerprint: str | None = None,
    expected_roster_fingerprint: str | None = None,
    at_utc: str | None = None,
) -> dict[str, Any]:
    normalized = validate_voice_training_project(project)
    _check_fingerprint(normalized, expected_fingerprint)
    _check_ownership(
        normalized,
        expected_character_id=expected_character_id,
        expected_source_fingerprint=expected_source_fingerprint,
        expected_roster_fingerprint=expected_roster_fingerprint,
    )
    data = _require_dict(payload or {}, "Voice-training action payload")
    working = copy.deepcopy(normalized)

    if action == "update_persona":
        if working["designed_voice_project"] is not None:
            raise VoiceTrainingActionError(
                "The base persona cannot change after a designed-voice project exists."
            )
        _require_no_adapter_state(working, "Persona editing")
        working["desired_base_persona"] = {
            "description": _require_text(
                data.get("description"),
                "Desired persona description",
                allow_empty=True,
            ),
            "ref_text": _require_text(
                data.get("ref_text"),
                "Desired persona reference text",
                allow_empty=True,
            ),
            "approval_status": "draft",
            "approved_at_utc": None,
            "approved_fingerprint": None,
        }

    elif action == "approve_persona":
        if working["designed_voice_project"] is not None:
            raise VoiceTrainingActionError(
                "The base persona cannot be re-approved after a designed-voice project exists."
            )
        persona = working["desired_base_persona"]
        description = _require_text(
            data.get("description", persona["description"]),
            "Desired persona description",
        )
        ref_text = _require_text(
            data.get("ref_text", persona["ref_text"]),
            "Desired persona reference text",
        )
        approved_at = at_utc or utc_now_text()
        working["desired_base_persona"] = {
            "description": description,
            "ref_text": ref_text,
            "approval_status": "approved",
            "approved_at_utc": approved_at,
            "approved_fingerprint": compute_persona_fingerprint(
                description=description,
                ref_text=ref_text,
            ),
        }

    elif action == "create_synthetic_project":
        if working["desired_base_persona"]["approval_status"] != "approved":
            raise VoiceTrainingActionError(
                "Approve the desired base persona before creating a synthetic project."
            )
        if working["designed_voice_project"] is not None:
            raise VoiceTrainingConflictError(
                "A designed-voice project already exists."
            )
        if working["existing_recordings"] is not None:
            raise VoiceTrainingActionError(
                "This project already uses the existing-recordings path."
            )
        seed_supported = data.get("seed_supported")
        if not isinstance(seed_supported, bool):
            raise VoiceTrainingActionError(
                "seed_supported must be boolean."
            )
        global_seed = data.get("global_seed")
        if global_seed is not None and (
            not isinstance(global_seed, int) or isinstance(global_seed, bool)
        ):
            raise VoiceTrainingActionError(
                "global_seed must be an integer or null."
            )
        sample_target = data.get("sample_target", 24)
        if (
            not isinstance(sample_target, int)
            or isinstance(sample_target, bool)
            or not 20 <= sample_target <= 25
        ):
            raise VoiceTrainingActionError(
                "sample_target must be an integer from 20 through 25."
            )
        working["designed_voice_project"] = {
            "status": "draft",
            "root_description": working["desired_base_persona"]["description"],
            "global_seed": global_seed,
            "seed_supported": seed_supported,
            "sample_target": sample_target,
            "samples": [],
            "export": None,
        }

    elif action == "add_synthetic_sample":
        _require_dataset_mutable(working, "Adding a synthetic sample")
        synthetic = _require_synthetic_project(working)
        sample = copy.deepcopy(
            _require_dict(data.get("sample"), "Designed-voice sample")
        )
        clip_id = _require_text(sample.get("clip_id"), "Designed-voice sample clip_id")
        if clip_id in _clip_map(working):
            raise VoiceTrainingConflictError(
                f"Clip ID {clip_id!r} already exists in this project."
            )
        synthetic["samples"].append(sample)
        synthetic["status"] = "review"

    elif action == "review_synthetic_sample":
        _require_dataset_mutable(working, "Synthetic sample review")
        synthetic = _require_synthetic_project(working)
        clip_id = _require_text(data.get("clip_id"), "Designed-voice sample clip_id")
        sample = _require_sample(synthetic, clip_id)
        review_status = _require_text(
            data.get("review_status"),
            "Designed-voice sample review_status",
        )
        sample["review_status"] = review_status
        sample["review_notes"] = _require_text(
            data.get("review_notes", ""),
            "Designed-voice sample review_notes",
            allow_empty=True,
        )
        drift_flags = _require_list(
            data.get("drift_flags", []),
            "Designed-voice sample drift_flags",
        )
        sample["drift_flags"] = [
            _require_text(item, "Designed-voice drift flag")
            for item in drift_flags
        ]
        synthetic["status"] = "review"

    elif action == "create_recording_project":
        if working["existing_recordings"] is not None:
            raise VoiceTrainingConflictError(
                "An existing-recordings project already exists."
            )
        if working["designed_voice_project"] is not None:
            raise VoiceTrainingActionError(
                "This project already uses the synthetic designed-voice path."
            )
        if data.get("same_speaker_declared") is not True:
            raise VoiceTrainingActionError(
                "An explicit same-speaker declaration is required."
            )
        working["existing_recordings"] = {
            "status": "draft",
            "same_speaker_declared": True,
            "speaker_declaration": _require_text(
                data.get("speaker_declaration"),
                "Existing-recordings speaker declaration",
            ),
            "files": [],
            "clips": [],
            "export": None,
        }

    elif action == "add_recording_file":
        _require_dataset_mutable(working, "Adding a recording file")
        recordings = _require_recording_project(working)
        file_value = copy.deepcopy(
            _require_dict(data.get("file"), "Existing recording file")
        )
        file_id = _require_text(file_value.get("file_id"), "Recording file_id")
        if any(item["file_id"] == file_id for item in recordings["files"]):
            raise VoiceTrainingConflictError(
                f"Recording file ID {file_id!r} already exists."
            )
        recordings["files"].append(file_value)
        recordings["status"] = "processing"

    elif action == "add_recording_clip":
        _require_dataset_mutable(working, "Adding a recording clip")
        recordings = _require_recording_project(working)
        clip_value = copy.deepcopy(
            _require_dict(data.get("clip"), "Existing recording clip")
        )
        clip_id = _require_text(clip_value.get("clip_id"), "Recording clip_id")
        if clip_id in _clip_map(working):
            raise VoiceTrainingConflictError(
                f"Clip ID {clip_id!r} already exists in this project."
            )
        recordings["clips"].append(clip_value)
        recordings["status"] = "review"

    elif action == "review_recording_clip":
        _require_dataset_mutable(working, "Recording clip review")
        recordings = _require_recording_project(working)
        clip_id = _require_text(data.get("clip_id"), "Recording clip_id")
        clip = _require_recording_clip(recordings, clip_id)
        editable = (
            "transcript",
            "transcript_confidence",
            "transcript_corrected",
            "audio_quality_score",
            "duplicate_status",
            "contamination_status",
            "inclusion_decision",
            "style_label",
        )
        for key in editable:
            if key in data:
                clip[key] = copy.deepcopy(data[key])
        recordings["status"] = "review"

    elif action == "approve_dataset":
        _require_no_adapter_state(working, "Dataset approval")
        if working["dataset_project"] is not None:
            raise VoiceTrainingConflictError(
                "A dataset project already exists."
            )
        source_kind = _require_text(
            data.get("source_kind"),
            "Dataset source_kind",
        )
        clip_ids = [
            _require_text(item, "Dataset clip ID")
            for item in _require_list(data.get("clip_ids"), "Dataset clip_ids")
        ]
        if not clip_ids:
            raise VoiceTrainingActionError(
                "At least one accepted clip is required for dataset approval."
            )
        if len(clip_ids) != len(set(clip_ids)):
            raise VoiceTrainingActionError(
                "Dataset clip IDs must not contain duplicates."
            )
        clips = _clip_map(working)
        selected: list[dict[str, Any]] = []
        for clip_id in clip_ids:
            clip = clips.get(clip_id)
            if clip is None:
                raise VoiceTrainingActionError(
                    f"Dataset clip {clip_id!r} was not found."
                )
            if clip["source_kind"] != source_kind:
                raise VoiceTrainingActionError(
                    "A dataset cannot mix synthetic and existing-recording clips."
                )
            if source_kind == "synthetic":
                if clip["review_status"] != "accepted":
                    raise VoiceTrainingActionError(
                        f"Synthetic clip {clip_id!r} is not accepted."
                    )
            elif source_kind == "existing_recordings":
                if clip["inclusion_decision"] != "included":
                    raise VoiceTrainingActionError(
                        f"Recording clip {clip_id!r} is not included."
                    )
            else:
                raise VoiceTrainingActionError(
                    f"Unsupported dataset source kind: {source_kind!r}."
                )
            selected.append(clip)
        approved_at = at_utc or utc_now_text()
        dataset_fingerprint = compute_dataset_fingerprint(
            source_kind=source_kind,
            clips=selected,
        )
        working["dataset_project"] = {
            "source_kind": source_kind,
            "status": "approved",
            "clip_ids": clip_ids,
            "metadata_path": data.get("metadata_path"),
            "zip_path": None,
            "dataset_fingerprint": dataset_fingerprint,
            "approved_at_utc": approved_at,
            "exported_at_utc": None,
        }
        source_project = (
            working["designed_voice_project"]
            if source_kind == "synthetic"
            else working["existing_recordings"]
        )
        assert source_project is not None
        source_project["status"] = "approved"

    elif action == "record_dataset_export":
        _require_no_adapter_state(working, "Dataset export recording")
        dataset = working["dataset_project"]
        if dataset is None or dataset["status"] != "approved":
            raise VoiceTrainingActionError(
                "Approve the dataset before recording an export."
            )
        exported_at = at_utc or utc_now_text()
        metadata_path = _require_text(
            data.get("metadata_path"),
            "Dataset metadata path",
        )
        zip_path = _require_text(data.get("zip_path"), "Dataset ZIP path")
        dataset_path = _require_text(
            data.get("dataset_path"),
            "Dataset directory path",
        )
        dataset["status"] = "exported"
        dataset["metadata_path"] = metadata_path
        dataset["zip_path"] = zip_path
        dataset["exported_at_utc"] = exported_at
        source_project = (
            working["designed_voice_project"]
            if dataset["source_kind"] == "synthetic"
            else working["existing_recordings"]
        )
        assert source_project is not None
        source_project["status"] = "exported"
        source_project["export"] = {
            "dataset_path": dataset_path,
            "metadata_path": metadata_path,
            "zip_path": zip_path,
            "exported_at_utc": exported_at,
            "dataset_fingerprint": dataset["dataset_fingerprint"],
        }

    elif action == "select_reference":
        _require_no_adapter_state(working, "Reference selection")
        dataset = working["dataset_project"]
        if dataset is None or dataset["status"] not in {"approved", "exported"}:
            raise VoiceTrainingActionError(
                "Approve the dataset before selecting a reference sample."
            )
        clip_id = _require_text(data.get("clip_id"), "Reference clip ID")
        if clip_id not in dataset["clip_ids"]:
            raise VoiceTrainingActionError(
                "The selected reference must belong to the approved dataset."
            )
        clip = _clip_map(working)[clip_id]
        working["selected_reference_sample"] = {
            "clip_id": clip_id,
            "source_kind": dataset["source_kind"],
            "audio_path": clip["audio_path"],
            "audio_sha256": clip["audio_sha256"],
            "selected_at_utc": at_utc or utc_now_text(),
        }

    elif action == "refresh_readiness":
        pass

    elif action == "record_adapter_provenance":
        if working["adapter_provenance"] is not None:
            raise VoiceTrainingConflictError(
                "Adapter provenance already exists."
            )
        _refresh_readiness(working)
        if (
            working["training_readiness"]["status"]
            != "ready_for_feasibility_review"
        ):
            raise VoiceTrainingActionError(
                "The candidate is not ready for Phase 22 feasibility review."
            )
        provenance = copy.deepcopy(
            _require_dict(data.get("provenance"), "Adapter provenance")
        )
        if provenance.get("dataset_fingerprint") != working[
            "dataset_project"
        ]["dataset_fingerprint"]:
            raise VoiceTrainingActionError(
                "Adapter provenance must use the approved project dataset fingerprint."
            )
        working["adapter_provenance"] = provenance
        working["validation_status"] = {
            "status": "adapter_pending",
            "notes": [],
            "checked_at_utc": at_utc or utc_now_text(),
        }

    elif action == "record_adapter_validation":
        if working["adapter_provenance"] is None:
            raise VoiceTrainingActionError(
                "Adapter validation requires recorded adapter provenance."
            )
        status = _require_text(
            data.get("status"),
            "Adapter validation status",
        )
        if status not in {"validated", "rejected"}:
            raise VoiceTrainingActionError(
                "Adapter validation status must be 'validated' or 'rejected'."
            )
        notes = [
            _require_text(item, "Adapter validation note")
            for item in _require_list(data.get("notes", []), "Adapter validation notes")
        ]
        working["validation_status"] = {
            "status": status,
            "notes": notes,
            "checked_at_utc": at_utc or utc_now_text(),
        }

    elif action == "assign_adapter":
        if working["adapter_provenance"] is None:
            raise VoiceTrainingActionError(
                "Adapter assignment requires adapter provenance."
            )
        if working["validation_status"]["status"] != "validated":
            raise VoiceTrainingActionError(
                "Only a validated adapter may be assigned."
            )
        if working["adapter_assignment"] is not None:
            raise VoiceTrainingConflictError(
                "An adapter is already assigned to this project."
            )
        if data.get("user_approved") is not True:
            raise VoiceTrainingActionError(
                "Adapter assignment requires explicit user approval."
            )
        adapter_path = _require_text(
            data.get(
                "adapter_path",
                working["adapter_provenance"]["adapter_path"],
            ),
            "Adapter path",
        )
        if adapter_path != working["adapter_provenance"]["adapter_path"]:
            raise VoiceTrainingActionError(
                "Adapter assignment path must match adapter provenance."
            )
        working["adapter_assignment"] = {
            "status": "assigned",
            "adapter_id": _require_text(
                data.get("adapter_id"),
                "Adapter ID",
            ),
            "adapter_path": adapter_path,
            "assigned_at_utc": at_utc or utc_now_text(),
            "user_approved": True,
        }

    else:
        raise VoiceTrainingActionError(
            f"Unsupported voice-training action: {action!r}."
        )

    _refresh_readiness(working)
    return _refresh_project(working, at_utc=at_utc)


def mutate_voice_training_project_file(
    *,
    project_path: str | Path,
    expected_fingerprint: str,
    action: str,
    payload: dict[str, Any] | None = None,
    expected_character_id: str | None = None,
    expected_source_fingerprint: str | None = None,
    expected_roster_fingerprint: str | None = None,
    at_utc: str | None = None,
) -> dict[str, Any]:
    with _VOICE_TRAINING_ACTION_LOCK:
        path = Path(project_path)
        project = read_voice_training_project(path)
        updated = apply_voice_training_action(
            project,
            expected_fingerprint=expected_fingerprint,
            action=action,
            payload=payload,
            expected_character_id=expected_character_id,
            expected_source_fingerprint=expected_source_fingerprint,
            expected_roster_fingerprint=expected_roster_fingerprint,
            at_utc=at_utc,
        )
        saved = save_voice_training_project(
            updated,
            path,
            replace_existing=True,
        )
        verified = read_voice_training_project(path)
        if saved != verified:
            raise VoiceTrainingProjectValidationError(
                "Voice-training project verification did not match the saved artifact."
            )
        return verified


def create_voice_training_project_file(
    *,
    project: dict[str, Any],
    project_path: str | Path,
) -> dict[str, Any]:
    with _VOICE_TRAINING_ACTION_LOCK:
        path = Path(project_path)
        if path.exists():
            raise VoiceTrainingConflictError(
                "A voice-training project already exists for this character."
            )
        try:
            saved = save_voice_training_project(project, path)
        except VoiceTrainingProjectError as exc:
            raise VoiceTrainingActionError(str(exc)) from exc
        verified = read_voice_training_project(path)
        if saved != verified:
            raise VoiceTrainingProjectValidationError(
                "Voice-training project verification did not match the saved artifact."
            )
        return verified
