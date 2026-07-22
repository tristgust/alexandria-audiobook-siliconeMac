from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from character_roster import validate_character_roster
from generation_state import atomic_json_write, fingerprint_value


VOICE_TRAINING_SCHEMA_VERSION = 1
PRIORITIES = frozenset({"primary", "secondary", "experimental"})
PERSONA_APPROVAL_STATUSES = frozenset({"draft", "approved"})
SYNTHETIC_PROJECT_STATUSES = frozenset(
    {"draft", "generating", "review", "approved", "exported"}
)
RECORDING_PROJECT_STATUSES = frozenset(
    {"draft", "processing", "review", "approved", "exported"}
)
SAMPLE_REVIEW_STATUSES = frozenset(
    {"pending", "accepted", "rejected", "regenerate"}
)
DATASET_STATUSES = frozenset({"draft", "review", "approved", "exported"})
DATASET_SOURCE_KINDS = frozenset({"synthetic", "existing_recordings"})
READINESS_STATUSES = frozenset(
    {"not_ready", "blocked", "ready_for_feasibility_review"}
)
VALIDATION_STATUSES = frozenset(
    {
        "not_evaluated",
        "dataset_ready",
        "adapter_pending",
        "validated",
        "rejected",
    }
)
FILE_PERMISSION_BASES = frozenset(
    {"owned", "licensed", "public_domain", "permissive"}
)
DUPLICATE_STATUSES = frozenset({"unchecked", "unique", "duplicate"})
CONTAMINATION_STATUSES = frozenset(
    {"unchecked", "clean", "overlapping_speaker", "contaminated"}
)
INCLUSION_DECISIONS = frozenset({"pending", "included", "excluded"})
ADAPTER_ASSIGNMENT_STATUSES = frozenset({"assigned", "retired"})

_CHARACTER_ID_RE = re.compile(r"^character_[0-9a-f]{20}$")
_CLIP_ID_RE = re.compile(r"^clip_[A-Za-z0-9][A-Za-z0-9_-]{2,127}$")
_FILE_ID_RE = re.compile(r"^file_[A-Za-z0-9][A-Za-z0-9_-]{2,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "character",
        "voice_training_candidate",
        "priority",
        "desired_base_persona",
        "designed_voice_project",
        "existing_recordings",
        "dataset_project",
        "training_readiness",
        "selected_reference_sample",
        "adapter_assignment",
        "adapter_provenance",
        "validation_status",
        "created_at_utc",
        "updated_at_utc",
        "project_fingerprint",
    }
)
_CHARACTER_KEYS = frozenset(
    {
        "id",
        "canonical_name",
        "display_name",
        "entity_kind",
        "speaking_status",
        "resolution_status",
        "source_fingerprint",
        "roster_fingerprint",
    }
)
_PERSONA_KEYS = frozenset(
    {
        "description",
        "ref_text",
        "approval_status",
        "approved_at_utc",
        "approved_fingerprint",
    }
)
_SYNTHETIC_PROJECT_KEYS = frozenset(
    {
        "status",
        "root_description",
        "global_seed",
        "seed_supported",
        "sample_target",
        "samples",
        "export",
    }
)
_SYNTHETIC_SAMPLE_KEYS = frozenset(
    {
        "clip_id",
        "text",
        "instruction",
        "seed",
        "audio_path",
        "audio_sha256",
        "generation_backend",
        "model",
        "generated_at_utc",
        "review_status",
        "review_notes",
        "drift_flags",
    }
)
_RECORDING_PROJECT_KEYS = frozenset(
    {
        "status",
        "same_speaker_declared",
        "speaker_declaration",
        "files",
        "clips",
        "export",
    }
)
_RECORDING_FILE_KEYS = frozenset(
    {
        "file_id",
        "original_filename",
        "stored_path",
        "sha256",
        "permission_basis",
        "imported_at_utc",
    }
)
_RECORDING_CLIP_KEYS = frozenset(
    {
        "clip_id",
        "source_file_id",
        "start_seconds",
        "end_seconds",
        "speaker_declaration",
        "transcript",
        "transcript_confidence",
        "transcript_corrected",
        "audio_quality_score",
        "normalization",
        "duplicate_status",
        "contamination_status",
        "inclusion_decision",
        "style_label",
        "audio_path",
        "audio_sha256",
    }
)
_NORMALIZATION_KEYS = frozenset({"sample_rate_hz", "channels", "format"})
_EXPORT_KEYS = frozenset(
    {
        "dataset_path",
        "metadata_path",
        "zip_path",
        "exported_at_utc",
        "dataset_fingerprint",
    }
)
_DATASET_KEYS = frozenset(
    {
        "source_kind",
        "status",
        "clip_ids",
        "metadata_path",
        "zip_path",
        "dataset_fingerprint",
        "approved_at_utc",
        "exported_at_utc",
    }
)
_READINESS_KEYS = frozenset(
    {"status", "blockers", "warnings", "dataset_fingerprint"}
)
_REFERENCE_KEYS = frozenset(
    {
        "clip_id",
        "source_kind",
        "audio_path",
        "audio_sha256",
        "selected_at_utc",
    }
)
_ADAPTER_ASSIGNMENT_KEYS = frozenset(
    {
        "status",
        "adapter_id",
        "adapter_path",
        "assigned_at_utc",
        "user_approved",
    }
)
_ADAPTER_PROVENANCE_KEYS = frozenset(
    {
        "training_backend",
        "base_model",
        "dataset_fingerprint",
        "training_settings",
        "adapter_path",
        "created_at_utc",
        "validation_samples",
        "comparison_results",
        "user_approved",
    }
)
_VALIDATION_KEYS = frozenset({"status", "notes", "checked_at_utc"})


class VoiceTrainingProjectError(RuntimeError):
    pass


class VoiceTrainingProjectValidationError(VoiceTrainingProjectError):
    pass


class VoiceTrainingProjectCorruptError(VoiceTrainingProjectError):
    pass


class VoiceTrainingProjectCompatibilityError(VoiceTrainingProjectError):
    pass


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise VoiceTrainingProjectValidationError(
            f"{label} must be a JSON object."
        )
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise VoiceTrainingProjectValidationError(
            f"{label} must be a JSON array."
        )
    return value


def _require_exact_keys(
    value: dict[str, Any],
    expected: frozenset[str] | set[str],
    label: str,
) -> None:
    actual = set(value)
    missing = sorted(set(expected) - actual)
    extra = sorted(actual - set(expected))
    if not missing and not extra:
        return
    details = []
    if missing:
        details.append("missing " + ", ".join(missing))
    if extra:
        details.append("unexpected " + ", ".join(extra))
    raise VoiceTrainingProjectValidationError(
        f"{label} has " + "; ".join(details) + "."
    )


def _require_text(
    value: Any,
    label: str,
    *,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise VoiceTrainingProjectValidationError(f"{label} must be text.")
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise VoiceTrainingProjectValidationError(
            f"{label} must not be empty."
        )
    return normalized


def _require_optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, label)


def _require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise VoiceTrainingProjectValidationError(
            f"{label} must be boolean."
        )
    return value


def _require_int(
    value: Any,
    label: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise VoiceTrainingProjectValidationError(
            f"{label} must be an integer."
        )
    if minimum is not None and value < minimum:
        raise VoiceTrainingProjectValidationError(
            f"{label} must be >= {minimum}."
        )
    if maximum is not None and value > maximum:
        raise VoiceTrainingProjectValidationError(
            f"{label} must be <= {maximum}."
        )
    return value


def _require_optional_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    return _require_int(value, label)


def _require_number(
    value: Any,
    label: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise VoiceTrainingProjectValidationError(
            f"{label} must be numeric."
        )
    normalized = float(value)
    if minimum is not None and normalized < minimum:
        raise VoiceTrainingProjectValidationError(
            f"{label} must be >= {minimum}."
        )
    if maximum is not None and normalized > maximum:
        raise VoiceTrainingProjectValidationError(
            f"{label} must be <= {maximum}."
        )
    return normalized


def _require_string_list(value: Any, label: str) -> list[str]:
    normalized = [
        _require_text(item, f"{label}[{index}]")
        for index, item in enumerate(_require_list(value, label))
    ]
    if len(normalized) != len(set(normalized)):
        raise VoiceTrainingProjectValidationError(
            f"{label} must not contain duplicates."
        )
    return normalized


def _require_timestamp(value: Any, label: str) -> str:
    text = _require_text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise VoiceTrainingProjectValidationError(
            f"{label} must be an ISO-8601 timestamp."
        ) from exc
    if parsed.tzinfo is None:
        raise VoiceTrainingProjectValidationError(
            f"{label} must include a timezone."
        )
    return text


def _require_optional_timestamp(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _require_timestamp(value, label)


def _require_sha256(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if not _SHA256_RE.fullmatch(text):
        raise VoiceTrainingProjectValidationError(
            f"{label} must be a lowercase SHA-256 digest."
        )
    return text


def _require_optional_sha256(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _require_sha256(value, label)


def _require_relative_path(value: Any, label: str) -> str:
    text = _require_text(value, label)
    path = Path(text)
    if path.is_absolute() or ".." in path.parts or text.startswith("~"):
        raise VoiceTrainingProjectValidationError(
            f"{label} must be a project-relative path."
        )
    return path.as_posix()


def _require_optional_relative_path(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _require_relative_path(value, label)


def _require_character_id(value: Any, label: str = "Character ID") -> str:
    text = _require_text(value, label)
    if not _CHARACTER_ID_RE.fullmatch(text):
        raise VoiceTrainingProjectValidationError(
            f"{label} must match character_<20 lowercase hex>."
        )
    return text


def _require_clip_id(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if not _CLIP_ID_RE.fullmatch(text):
        raise VoiceTrainingProjectValidationError(
            f"{label} is not a valid clip ID."
        )
    return text


def _require_file_id(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if not _FILE_ID_RE.fullmatch(text):
        raise VoiceTrainingProjectValidationError(
            f"{label} is not a valid file ID."
        )
    return text


def voice_training_project_path(
    projects_root: str | Path,
    character_id: str,
) -> Path:
    safe_id = _require_character_id(character_id)
    return Path(projects_root) / safe_id / "project.json"


def _validate_character(value: Any) -> dict[str, Any]:
    character = _require_dict(value, "Voice-training character")
    _require_exact_keys(character, _CHARACTER_KEYS, "Voice-training character")
    normalized = {
        "id": _require_character_id(character["id"]),
        "canonical_name": _require_text(
            character["canonical_name"],
            "Voice-training character.canonical_name",
        ),
        "display_name": _require_text(
            character["display_name"],
            "Voice-training character.display_name",
        ),
        "entity_kind": _require_text(
            character["entity_kind"],
            "Voice-training character.entity_kind",
        ),
        "speaking_status": _require_text(
            character["speaking_status"],
            "Voice-training character.speaking_status",
        ),
        "resolution_status": _require_text(
            character["resolution_status"],
            "Voice-training character.resolution_status",
        ),
        "source_fingerprint": _require_sha256(
            character["source_fingerprint"],
            "Voice-training character.source_fingerprint",
        ),
        "roster_fingerprint": _require_sha256(
            character["roster_fingerprint"],
            "Voice-training character.roster_fingerprint",
        ),
    }
    if normalized["speaking_status"] not in {"speaker", "narrator"}:
        raise VoiceTrainingProjectValidationError(
            "Voice-training candidates must be speakers in the current script."
        )
    if normalized["resolution_status"] != "resolved":
        raise VoiceTrainingProjectValidationError(
            "Voice-training candidates must have resolved identities."
        )
    return normalized


def compute_persona_fingerprint(*, description: str, ref_text: str) -> str:
    return fingerprint_value(
        {
            "description": description,
            "ref_text": ref_text,
        }
    )


def _validate_persona(value: Any) -> dict[str, Any]:
    persona = _require_dict(value, "Desired base persona")
    _require_exact_keys(persona, _PERSONA_KEYS, "Desired base persona")
    approval_status = _require_text(
        persona["approval_status"],
        "Desired base persona.approval_status",
    )
    if approval_status not in PERSONA_APPROVAL_STATUSES:
        raise VoiceTrainingProjectValidationError(
            "Desired base persona.approval_status is unsupported."
        )
    description = _require_text(
        persona["description"],
        "Desired base persona.description",
        allow_empty=approval_status == "draft",
    )
    ref_text = _require_text(
        persona["ref_text"],
        "Desired base persona.ref_text",
        allow_empty=approval_status == "draft",
    )
    approved_at = _require_optional_timestamp(
        persona["approved_at_utc"],
        "Desired base persona.approved_at_utc",
    )
    approved_fingerprint = _require_optional_sha256(
        persona["approved_fingerprint"],
        "Desired base persona.approved_fingerprint",
    )
    if approval_status == "draft":
        if approved_at is not None or approved_fingerprint is not None:
            raise VoiceTrainingProjectValidationError(
                "Draft voice personas cannot carry approval metadata."
            )
    else:
        if not description or not ref_text:
            raise VoiceTrainingProjectValidationError(
                "Approved voice personas require description and ref_text."
            )
        expected = compute_persona_fingerprint(
            description=description,
            ref_text=ref_text,
        )
        if approved_at is None or approved_fingerprint != expected:
            raise VoiceTrainingProjectValidationError(
                "Approved voice persona metadata is incomplete or stale."
            )
    return {
        "description": description,
        "ref_text": ref_text,
        "approval_status": approval_status,
        "approved_at_utc": approved_at,
        "approved_fingerprint": approved_fingerprint,
    }


def _validate_export(value: Any, label: str) -> dict[str, Any] | None:
    if value is None:
        return None
    export = _require_dict(value, label)
    _require_exact_keys(export, _EXPORT_KEYS, label)
    return {
        "dataset_path": _require_relative_path(
            export["dataset_path"], f"{label}.dataset_path"
        ),
        "metadata_path": _require_relative_path(
            export["metadata_path"], f"{label}.metadata_path"
        ),
        "zip_path": _require_relative_path(
            export["zip_path"], f"{label}.zip_path"
        ),
        "exported_at_utc": _require_timestamp(
            export["exported_at_utc"], f"{label}.exported_at_utc"
        ),
        "dataset_fingerprint": _require_sha256(
            export["dataset_fingerprint"],
            f"{label}.dataset_fingerprint",
        ),
    }


def _validate_synthetic_sample(value: Any, index: int) -> dict[str, Any]:
    label = f"Designed voice sample {index}"
    sample = _require_dict(value, label)
    _require_exact_keys(sample, _SYNTHETIC_SAMPLE_KEYS, label)
    review_status = _require_text(sample["review_status"], f"{label}.review_status")
    if review_status not in SAMPLE_REVIEW_STATUSES:
        raise VoiceTrainingProjectValidationError(
            f"{label}.review_status is unsupported."
        )
    return {
        "clip_id": _require_clip_id(sample["clip_id"], f"{label}.clip_id"),
        "text": _require_text(sample["text"], f"{label}.text"),
        "instruction": _require_text(
            sample["instruction"], f"{label}.instruction"
        ),
        "seed": _require_optional_int(sample["seed"], f"{label}.seed"),
        "audio_path": _require_relative_path(
            sample["audio_path"], f"{label}.audio_path"
        ),
        "audio_sha256": _require_sha256(
            sample["audio_sha256"], f"{label}.audio_sha256"
        ),
        "generation_backend": _require_text(
            sample["generation_backend"], f"{label}.generation_backend"
        ),
        "model": _require_text(sample["model"], f"{label}.model"),
        "generated_at_utc": _require_timestamp(
            sample["generated_at_utc"], f"{label}.generated_at_utc"
        ),
        "review_status": review_status,
        "review_notes": _require_text(
            sample["review_notes"],
            f"{label}.review_notes",
            allow_empty=True,
        ),
        "drift_flags": _require_string_list(
            sample["drift_flags"], f"{label}.drift_flags"
        ),
    }


def _validate_synthetic_project(
    value: Any,
    *,
    persona: dict[str, Any],
) -> dict[str, Any] | None:
    if value is None:
        return None
    project = _require_dict(value, "Designed voice project")
    _require_exact_keys(
        project,
        _SYNTHETIC_PROJECT_KEYS,
        "Designed voice project",
    )
    if persona["approval_status"] != "approved":
        raise VoiceTrainingProjectValidationError(
            "Synthetic voice projects require an approved base persona."
        )
    status = _require_text(project["status"], "Designed voice project.status")
    if status not in SYNTHETIC_PROJECT_STATUSES:
        raise VoiceTrainingProjectValidationError(
            "Designed voice project.status is unsupported."
        )
    root_description = _require_text(
        project["root_description"],
        "Designed voice project.root_description",
    )
    if root_description != persona["description"]:
        raise VoiceTrainingProjectValidationError(
            "Designed voice project must use the approved root persona description."
        )
    seed_supported = _require_bool(
        project["seed_supported"],
        "Designed voice project.seed_supported",
    )
    global_seed = _require_optional_int(
        project["global_seed"],
        "Designed voice project.global_seed",
    )
    if seed_supported and global_seed is None:
        raise VoiceTrainingProjectValidationError(
            "A fixed global seed is required when the backend supports seeds."
        )
    sample_target = _require_int(
        project["sample_target"],
        "Designed voice project.sample_target",
        minimum=20,
        maximum=25,
    )
    samples = [
        _validate_synthetic_sample(item, index)
        for index, item in enumerate(
            _require_list(project["samples"], "Designed voice project.samples")
        )
    ]
    clip_ids = [sample["clip_id"] for sample in samples]
    if len(clip_ids) != len(set(clip_ids)):
        raise VoiceTrainingProjectValidationError(
            "Designed voice sample IDs must be unique."
        )
    export = _validate_export(project["export"], "Designed voice project.export")
    if status == "exported" and export is None:
        raise VoiceTrainingProjectValidationError(
            "Exported designed voice projects require export metadata."
        )
    if export is not None and status != "exported":
        raise VoiceTrainingProjectValidationError(
            "Designed voice export metadata requires exported status."
        )
    return {
        "status": status,
        "root_description": root_description,
        "global_seed": global_seed,
        "seed_supported": seed_supported,
        "sample_target": sample_target,
        "samples": samples,
        "export": export,
    }


def _validate_recording_file(value: Any, index: int) -> dict[str, Any]:
    label = f"Existing recording file {index}"
    item = _require_dict(value, label)
    _require_exact_keys(item, _RECORDING_FILE_KEYS, label)
    permission_basis = _require_text(
        item["permission_basis"], f"{label}.permission_basis"
    )
    if permission_basis not in FILE_PERMISSION_BASES:
        raise VoiceTrainingProjectValidationError(
            f"{label}.permission_basis is unsupported."
        )
    return {
        "file_id": _require_file_id(item["file_id"], f"{label}.file_id"),
        "original_filename": _require_text(
            item["original_filename"], f"{label}.original_filename"
        ),
        "stored_path": _require_relative_path(
            item["stored_path"], f"{label}.stored_path"
        ),
        "sha256": _require_sha256(item["sha256"], f"{label}.sha256"),
        "permission_basis": permission_basis,
        "imported_at_utc": _require_timestamp(
            item["imported_at_utc"], f"{label}.imported_at_utc"
        ),
    }


def _validate_recording_clip(value: Any, index: int) -> dict[str, Any]:
    label = f"Existing recording clip {index}"
    clip = _require_dict(value, label)
    _require_exact_keys(clip, _RECORDING_CLIP_KEYS, label)
    normalization = _require_dict(
        clip["normalization"], f"{label}.normalization"
    )
    _require_exact_keys(normalization, _NORMALIZATION_KEYS, f"{label}.normalization")
    duplicate_status = _require_text(
        clip["duplicate_status"], f"{label}.duplicate_status"
    )
    contamination_status = _require_text(
        clip["contamination_status"], f"{label}.contamination_status"
    )
    inclusion_decision = _require_text(
        clip["inclusion_decision"], f"{label}.inclusion_decision"
    )
    if duplicate_status not in DUPLICATE_STATUSES:
        raise VoiceTrainingProjectValidationError(
            f"{label}.duplicate_status is unsupported."
        )
    if contamination_status not in CONTAMINATION_STATUSES:
        raise VoiceTrainingProjectValidationError(
            f"{label}.contamination_status is unsupported."
        )
    if inclusion_decision not in INCLUSION_DECISIONS:
        raise VoiceTrainingProjectValidationError(
            f"{label}.inclusion_decision is unsupported."
        )
    start_seconds = _require_number(
        clip["start_seconds"], f"{label}.start_seconds", minimum=0.0
    )
    end_seconds = _require_number(
        clip["end_seconds"], f"{label}.end_seconds", minimum=0.0
    )
    if end_seconds <= start_seconds:
        raise VoiceTrainingProjectValidationError(
            f"{label} end_seconds must be greater than start_seconds."
        )
    transcript_corrected = _require_bool(
        clip["transcript_corrected"], f"{label}.transcript_corrected"
    )
    if inclusion_decision == "included":
        if not transcript_corrected:
            raise VoiceTrainingProjectValidationError(
                f"{label} must have a reviewed transcript before inclusion."
            )
        if duplicate_status != "unique" or contamination_status != "clean":
            raise VoiceTrainingProjectValidationError(
                f"{label} must be unique and clean before inclusion."
            )
    return {
        "clip_id": _require_clip_id(clip["clip_id"], f"{label}.clip_id"),
        "source_file_id": _require_file_id(
            clip["source_file_id"], f"{label}.source_file_id"
        ),
        "start_seconds": start_seconds,
        "end_seconds": end_seconds,
        "speaker_declaration": _require_text(
            clip["speaker_declaration"], f"{label}.speaker_declaration"
        ),
        "transcript": _require_text(clip["transcript"], f"{label}.transcript"),
        "transcript_confidence": _require_number(
            clip["transcript_confidence"],
            f"{label}.transcript_confidence",
            minimum=0.0,
            maximum=1.0,
        ),
        "transcript_corrected": transcript_corrected,
        "audio_quality_score": _require_number(
            clip["audio_quality_score"],
            f"{label}.audio_quality_score",
            minimum=0.0,
            maximum=1.0,
        ),
        "normalization": {
            "sample_rate_hz": _require_int(
                normalization["sample_rate_hz"],
                f"{label}.normalization.sample_rate_hz",
                minimum=8000,
            ),
            "channels": _require_int(
                normalization["channels"],
                f"{label}.normalization.channels",
                minimum=1,
                maximum=2,
            ),
            "format": _require_text(
                normalization["format"], f"{label}.normalization.format"
            ).lower(),
        },
        "duplicate_status": duplicate_status,
        "contamination_status": contamination_status,
        "inclusion_decision": inclusion_decision,
        "style_label": _require_text(
            clip["style_label"], f"{label}.style_label", allow_empty=True
        ),
        "audio_path": _require_relative_path(
            clip["audio_path"], f"{label}.audio_path"
        ),
        "audio_sha256": _require_sha256(
            clip["audio_sha256"], f"{label}.audio_sha256"
        ),
    }


def _validate_recording_project(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    project = _require_dict(value, "Existing recordings project")
    _require_exact_keys(
        project,
        _RECORDING_PROJECT_KEYS,
        "Existing recordings project",
    )
    status = _require_text(project["status"], "Existing recordings project.status")
    if status not in RECORDING_PROJECT_STATUSES:
        raise VoiceTrainingProjectValidationError(
            "Existing recordings project.status is unsupported."
        )
    declared = _require_bool(
        project["same_speaker_declared"],
        "Existing recordings project.same_speaker_declared",
    )
    if not declared:
        raise VoiceTrainingProjectValidationError(
            "Existing recordings require an explicit same-speaker declaration."
        )
    speaker_declaration = _require_text(
        project["speaker_declaration"],
        "Existing recordings project.speaker_declaration",
    )
    files = [
        _validate_recording_file(item, index)
        for index, item in enumerate(
            _require_list(project["files"], "Existing recordings project.files")
        )
    ]
    file_ids = [item["file_id"] for item in files]
    if len(file_ids) != len(set(file_ids)):
        raise VoiceTrainingProjectValidationError(
            "Existing recording file IDs must be unique."
        )
    clips = [
        _validate_recording_clip(item, index)
        for index, item in enumerate(
            _require_list(project["clips"], "Existing recordings project.clips")
        )
    ]
    clip_ids = [item["clip_id"] for item in clips]
    if len(clip_ids) != len(set(clip_ids)):
        raise VoiceTrainingProjectValidationError(
            "Existing recording clip IDs must be unique."
        )
    file_id_set = set(file_ids)
    for clip in clips:
        if clip["source_file_id"] not in file_id_set:
            raise VoiceTrainingProjectValidationError(
                f"Clip {clip['clip_id']!r} references an unknown source file."
            )
        if clip["speaker_declaration"] != speaker_declaration:
            raise VoiceTrainingProjectValidationError(
                f"Clip {clip['clip_id']!r} does not match the project speaker declaration."
            )
    export = _validate_export(project["export"], "Existing recordings project.export")
    if status == "exported" and export is None:
        raise VoiceTrainingProjectValidationError(
            "Exported recording projects require export metadata."
        )
    if export is not None and status != "exported":
        raise VoiceTrainingProjectValidationError(
            "Recording export metadata requires exported status."
        )
    return {
        "status": status,
        "same_speaker_declared": declared,
        "speaker_declaration": speaker_declaration,
        "files": files,
        "clips": clips,
        "export": export,
    }


def _project_clip_map(
    *,
    synthetic: dict[str, Any] | None,
    recordings: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    clips: dict[str, dict[str, Any]] = {}
    if synthetic is not None:
        for sample in synthetic["samples"]:
            clips[sample["clip_id"]] = {"source_kind": "synthetic", **sample}
    if recordings is not None:
        for clip in recordings["clips"]:
            if clip["clip_id"] in clips:
                raise VoiceTrainingProjectValidationError(
                    "Clip IDs must be unique across synthetic and recording projects."
                )
            clips[clip["clip_id"]] = {"source_kind": "existing_recordings", **clip}
    return clips


def compute_dataset_fingerprint(
    *,
    source_kind: str,
    clips: list[dict[str, Any]],
) -> str:
    return fingerprint_value(
        {
            "source_kind": source_kind,
            "clips": clips,
        }
    )


def _validate_dataset_project(
    value: Any,
    *,
    synthetic: dict[str, Any] | None,
    recordings: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if value is None:
        return None
    project = _require_dict(value, "Dataset project")
    _require_exact_keys(project, _DATASET_KEYS, "Dataset project")
    source_kind = _require_text(project["source_kind"], "Dataset project.source_kind")
    if source_kind not in DATASET_SOURCE_KINDS:
        raise VoiceTrainingProjectValidationError(
            "Dataset project.source_kind is unsupported."
        )
    status = _require_text(project["status"], "Dataset project.status")
    if status not in DATASET_STATUSES:
        raise VoiceTrainingProjectValidationError(
            "Dataset project.status is unsupported."
        )
    clip_ids = _require_string_list(project["clip_ids"], "Dataset project.clip_ids")
    clip_map = _project_clip_map(synthetic=synthetic, recordings=recordings)
    selected_clips: list[dict[str, Any]] = []
    for clip_id in clip_ids:
        clip = clip_map.get(clip_id)
        if clip is None:
            raise VoiceTrainingProjectValidationError(
                f"Dataset project references unknown clip {clip_id!r}."
            )
        if clip["source_kind"] != source_kind:
            raise VoiceTrainingProjectValidationError(
                "A dataset project cannot mix synthetic and existing recordings."
            )
        if source_kind == "synthetic" and clip["review_status"] != "accepted":
            raise VoiceTrainingProjectValidationError(
                f"Synthetic clip {clip_id!r} must be accepted before dataset inclusion."
            )
        if source_kind == "existing_recordings" and clip["inclusion_decision"] != "included":
            raise VoiceTrainingProjectValidationError(
                f"Recording clip {clip_id!r} must be included before dataset inclusion."
            )
        selected_clips.append(clip)
    metadata_path = _require_optional_relative_path(
        project["metadata_path"], "Dataset project.metadata_path"
    )
    zip_path = _require_optional_relative_path(
        project["zip_path"], "Dataset project.zip_path"
    )
    dataset_fingerprint = _require_optional_sha256(
        project["dataset_fingerprint"], "Dataset project.dataset_fingerprint"
    )
    approved_at = _require_optional_timestamp(
        project["approved_at_utc"], "Dataset project.approved_at_utc"
    )
    exported_at = _require_optional_timestamp(
        project["exported_at_utc"], "Dataset project.exported_at_utc"
    )
    if status in {"approved", "exported"}:
        if not clip_ids:
            raise VoiceTrainingProjectValidationError(
                "Approved datasets must contain at least one accepted clip."
            )
        expected = compute_dataset_fingerprint(
            source_kind=source_kind,
            clips=selected_clips,
        )
        if dataset_fingerprint != expected or approved_at is None:
            raise VoiceTrainingProjectValidationError(
                "Approved dataset metadata is incomplete or stale."
            )
    elif any(item is not None for item in (dataset_fingerprint, approved_at, exported_at)):
        raise VoiceTrainingProjectValidationError(
            "Draft or review datasets cannot carry approval/export metadata."
        )
    if status == "exported":
        if metadata_path is None or zip_path is None or exported_at is None:
            raise VoiceTrainingProjectValidationError(
                "Exported datasets require metadata, ZIP, and export time."
            )
    elif exported_at is not None or zip_path is not None:
        raise VoiceTrainingProjectValidationError(
            "Dataset ZIP/export metadata requires exported status."
        )
    return {
        "source_kind": source_kind,
        "status": status,
        "clip_ids": clip_ids,
        "metadata_path": metadata_path,
        "zip_path": zip_path,
        "dataset_fingerprint": dataset_fingerprint,
        "approved_at_utc": approved_at,
        "exported_at_utc": exported_at,
    }


def _validate_reference(
    value: Any,
    *,
    dataset: dict[str, Any] | None,
    clip_map: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if value is None:
        return None
    reference = _require_dict(value, "Selected reference sample")
    _require_exact_keys(reference, _REFERENCE_KEYS, "Selected reference sample")
    clip_id = _require_clip_id(reference["clip_id"], "Selected reference sample.clip_id")
    source_kind = _require_text(
        reference["source_kind"], "Selected reference sample.source_kind"
    )
    if dataset is None or dataset["status"] not in {"approved", "exported"}:
        raise VoiceTrainingProjectValidationError(
            "Reference selection requires an approved dataset."
        )
    if clip_id not in dataset["clip_ids"]:
        raise VoiceTrainingProjectValidationError(
            "Selected reference sample must belong to the approved dataset."
        )
    clip = clip_map[clip_id]
    if source_kind != dataset["source_kind"] or source_kind != clip["source_kind"]:
        raise VoiceTrainingProjectValidationError(
            "Selected reference sample source kind is inconsistent."
        )
    audio_path = _require_relative_path(
        reference["audio_path"], "Selected reference sample.audio_path"
    )
    audio_sha256 = _require_sha256(
        reference["audio_sha256"], "Selected reference sample.audio_sha256"
    )
    if audio_path != clip["audio_path"] or audio_sha256 != clip["audio_sha256"]:
        raise VoiceTrainingProjectValidationError(
            "Selected reference sample does not match the accepted clip artifact."
        )
    return {
        "clip_id": clip_id,
        "source_kind": source_kind,
        "audio_path": audio_path,
        "audio_sha256": audio_sha256,
        "selected_at_utc": _require_timestamp(
            reference["selected_at_utc"],
            "Selected reference sample.selected_at_utc",
        ),
    }


def _validate_readiness(
    value: Any,
    *,
    persona: dict[str, Any],
    dataset: dict[str, Any] | None,
    reference: dict[str, Any] | None,
) -> dict[str, Any]:
    readiness = _require_dict(value, "Training readiness")
    _require_exact_keys(readiness, _READINESS_KEYS, "Training readiness")
    status = _require_text(readiness["status"], "Training readiness.status")
    if status not in READINESS_STATUSES:
        raise VoiceTrainingProjectValidationError(
            "Training readiness.status is unsupported."
        )
    blockers = _require_string_list(readiness["blockers"], "Training readiness.blockers")
    warnings = _require_string_list(readiness["warnings"], "Training readiness.warnings")
    dataset_fingerprint = _require_optional_sha256(
        readiness["dataset_fingerprint"],
        "Training readiness.dataset_fingerprint",
    )
    expected_dataset_fingerprint = (
        dataset["dataset_fingerprint"] if dataset is not None else None
    )
    if dataset_fingerprint != expected_dataset_fingerprint:
        raise VoiceTrainingProjectValidationError(
            "Training readiness dataset fingerprint is stale."
        )
    if status == "ready_for_feasibility_review":
        if blockers:
            raise VoiceTrainingProjectValidationError(
                "Ready projects cannot retain blockers."
            )
        if persona["approval_status"] != "approved":
            raise VoiceTrainingProjectValidationError(
                "Ready projects require an approved base persona."
            )
        if dataset is None or dataset["status"] not in {"approved", "exported"}:
            raise VoiceTrainingProjectValidationError(
                "Ready projects require an approved dataset."
            )
        if reference is None:
            raise VoiceTrainingProjectValidationError(
                "Ready projects require an explicit reference sample."
            )
    return {
        "status": status,
        "blockers": blockers,
        "warnings": warnings,
        "dataset_fingerprint": dataset_fingerprint,
    }


def _validate_adapter_assignment(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    assignment = _require_dict(value, "Adapter assignment")
    _require_exact_keys(assignment, _ADAPTER_ASSIGNMENT_KEYS, "Adapter assignment")
    status = _require_text(assignment["status"], "Adapter assignment.status")
    if status not in ADAPTER_ASSIGNMENT_STATUSES:
        raise VoiceTrainingProjectValidationError(
            "Adapter assignment.status is unsupported."
        )
    approved = _require_bool(
        assignment["user_approved"], "Adapter assignment.user_approved"
    )
    if status == "assigned" and not approved:
        raise VoiceTrainingProjectValidationError(
            "Adapter assignment must be explicitly user approved."
        )
    return {
        "status": status,
        "adapter_id": _require_text(
            assignment["adapter_id"], "Adapter assignment.adapter_id"
        ),
        "adapter_path": _require_relative_path(
            assignment["adapter_path"], "Adapter assignment.adapter_path"
        ),
        "assigned_at_utc": _require_timestamp(
            assignment["assigned_at_utc"], "Adapter assignment.assigned_at_utc"
        ),
        "user_approved": approved,
    }


def _validate_adapter_provenance(
    value: Any,
    *,
    dataset: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if value is None:
        return None
    provenance = _require_dict(value, "Adapter provenance")
    _require_exact_keys(
        provenance,
        _ADAPTER_PROVENANCE_KEYS,
        "Adapter provenance",
    )
    dataset_fingerprint = _require_sha256(
        provenance["dataset_fingerprint"],
        "Adapter provenance.dataset_fingerprint",
    )
    if dataset is None or dataset_fingerprint != dataset["dataset_fingerprint"]:
        raise VoiceTrainingProjectValidationError(
            "Adapter provenance must reference the approved project dataset."
        )
    settings = _require_dict(
        provenance["training_settings"],
        "Adapter provenance.training_settings",
    )
    validation_samples = _require_list(
        provenance["validation_samples"],
        "Adapter provenance.validation_samples",
    )
    comparison_results = _require_dict(
        provenance["comparison_results"],
        "Adapter provenance.comparison_results",
    )
    return {
        "training_backend": _require_text(
            provenance["training_backend"],
            "Adapter provenance.training_backend",
        ),
        "base_model": _require_text(
            provenance["base_model"], "Adapter provenance.base_model"
        ),
        "dataset_fingerprint": dataset_fingerprint,
        "training_settings": copy.deepcopy(settings),
        "adapter_path": _require_relative_path(
            provenance["adapter_path"], "Adapter provenance.adapter_path"
        ),
        "created_at_utc": _require_timestamp(
            provenance["created_at_utc"], "Adapter provenance.created_at_utc"
        ),
        "validation_samples": copy.deepcopy(validation_samples),
        "comparison_results": copy.deepcopy(comparison_results),
        "user_approved": _require_bool(
            provenance["user_approved"],
            "Adapter provenance.user_approved",
        ),
    }


def _validate_validation_status(
    value: Any,
    *,
    adapter_provenance: dict[str, Any] | None,
) -> dict[str, Any]:
    status_value = _require_dict(value, "Validation status")
    _require_exact_keys(status_value, _VALIDATION_KEYS, "Validation status")
    status = _require_text(status_value["status"], "Validation status.status")
    if status not in VALIDATION_STATUSES:
        raise VoiceTrainingProjectValidationError(
            "Validation status.status is unsupported."
        )
    checked_at = _require_optional_timestamp(
        status_value["checked_at_utc"], "Validation status.checked_at_utc"
    )
    if status == "not_evaluated" and checked_at is not None:
        raise VoiceTrainingProjectValidationError(
            "Unevaluated projects cannot carry a validation timestamp."
        )
    if status in {"validated", "rejected"} and adapter_provenance is None:
        raise VoiceTrainingProjectValidationError(
            "Adapter validation requires adapter provenance."
        )
    return {
        "status": status,
        "notes": _require_string_list(status_value["notes"], "Validation status.notes"),
        "checked_at_utc": checked_at,
    }


def _fingerprint_payload(project: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in project.items()
        if key != "project_fingerprint"
    }


def compute_voice_training_project_fingerprint(project: dict[str, Any]) -> str:
    return fingerprint_value(_fingerprint_payload(project))


def validate_voice_training_project(value: Any) -> dict[str, Any]:
    project = _require_dict(value, "Voice-training project")
    _require_exact_keys(project, _TOP_LEVEL_KEYS, "Voice-training project")
    if project["schema_version"] != VOICE_TRAINING_SCHEMA_VERSION:
        raise VoiceTrainingProjectValidationError(
            "Unsupported voice-training project schema version."
        )
    character = _validate_character(project["character"])
    candidate = _require_bool(
        project["voice_training_candidate"],
        "Voice-training project.voice_training_candidate",
    )
    if not candidate:
        raise VoiceTrainingProjectValidationError(
            "A project file may exist only for an explicitly selected candidate."
        )
    priority = _require_text(project["priority"], "Voice-training project.priority")
    if priority not in PRIORITIES:
        raise VoiceTrainingProjectValidationError(
            "Voice-training project.priority is unsupported."
        )
    persona = _validate_persona(project["desired_base_persona"])
    synthetic = _validate_synthetic_project(
        project["designed_voice_project"], persona=persona
    )
    recordings = _validate_recording_project(project["existing_recordings"])
    dataset = _validate_dataset_project(
        project["dataset_project"],
        synthetic=synthetic,
        recordings=recordings,
    )
    clip_map = _project_clip_map(synthetic=synthetic, recordings=recordings)
    reference = _validate_reference(
        project["selected_reference_sample"],
        dataset=dataset,
        clip_map=clip_map,
    )
    readiness = _validate_readiness(
        project["training_readiness"],
        persona=persona,
        dataset=dataset,
        reference=reference,
    )
    adapter_provenance = _validate_adapter_provenance(
        project["adapter_provenance"], dataset=dataset
    )
    adapter_assignment = _validate_adapter_assignment(project["adapter_assignment"])
    if adapter_assignment is not None and adapter_provenance is None:
        raise VoiceTrainingProjectValidationError(
            "Adapter assignment requires adapter provenance."
        )
    if (
        adapter_assignment is not None
        and adapter_provenance is not None
        and adapter_assignment["adapter_path"] != adapter_provenance["adapter_path"]
    ):
        raise VoiceTrainingProjectValidationError(
            "Adapter assignment path does not match adapter provenance."
        )
    validation_status = _validate_validation_status(
        project["validation_status"],
        adapter_provenance=adapter_provenance,
    )
    created_at = _require_timestamp(
        project["created_at_utc"], "Voice-training project.created_at_utc"
    )
    updated_at = _require_timestamp(
        project["updated_at_utc"], "Voice-training project.updated_at_utc"
    )
    normalized = {
        "schema_version": VOICE_TRAINING_SCHEMA_VERSION,
        "character": character,
        "voice_training_candidate": True,
        "priority": priority,
        "desired_base_persona": persona,
        "designed_voice_project": synthetic,
        "existing_recordings": recordings,
        "dataset_project": dataset,
        "training_readiness": readiness,
        "selected_reference_sample": reference,
        "adapter_assignment": adapter_assignment,
        "adapter_provenance": adapter_provenance,
        "validation_status": validation_status,
        "created_at_utc": created_at,
        "updated_at_utc": updated_at,
        "project_fingerprint": _require_sha256(
            project["project_fingerprint"],
            "Voice-training project.project_fingerprint",
        ),
    }
    expected_fingerprint = compute_voice_training_project_fingerprint(normalized)
    if normalized["project_fingerprint"] != expected_fingerprint:
        raise VoiceTrainingProjectValidationError(
            "Voice-training project fingerprint does not match its contents."
        )
    return normalized


def build_voice_training_project(
    *,
    approved_roster: dict[str, Any],
    character_id: str,
    priority: str,
    desired_description: str = "",
    desired_ref_text: str = "",
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    roster = validate_character_roster(
        approved_roster,
        expected_status="approved",
    )
    safe_character_id = _require_character_id(character_id)
    entry = next(
        (item for item in roster["entries"] if item["id"] == safe_character_id),
        None,
    )
    if entry is None:
        raise VoiceTrainingProjectCompatibilityError(
            "The selected speaker is not present in the current identity catalog."
        )
    if entry["speaking_status"] not in {"speaker", "narrator"}:
        raise VoiceTrainingProjectCompatibilityError(
            "Only speakers in the current script may become voice-training candidates."
        )
    if entry["resolution_status"] != "resolved":
        raise VoiceTrainingProjectCompatibilityError(
            "Unresolved identities cannot become voice-training candidates."
        )
    selected_priority = _require_text(priority, "Voice-training project priority")
    if selected_priority not in PRIORITIES:
        raise VoiceTrainingProjectValidationError(
            "Voice-training project priority is unsupported."
        )
    timestamp = (
        _require_timestamp(created_at_utc, "Voice-training project creation time")
        if created_at_utc is not None
        else utc_now_text()
    )
    source_fingerprint = _require_sha256(
        roster["source"]["fingerprint"], "Approved roster source fingerprint"
    )
    roster_fingerprint = _require_sha256(
        roster["roster_fingerprint"], "Approved roster fingerprint"
    )
    project = {
        "schema_version": VOICE_TRAINING_SCHEMA_VERSION,
        "character": {
            "id": entry["id"],
            "canonical_name": entry["canonical_name"],
            "display_name": entry["display_name"],
            "entity_kind": entry["entity_kind"],
            "speaking_status": entry["speaking_status"],
            "resolution_status": entry["resolution_status"],
            "source_fingerprint": source_fingerprint,
            "roster_fingerprint": roster_fingerprint,
        },
        "voice_training_candidate": True,
        "priority": selected_priority,
        "desired_base_persona": {
            "description": desired_description.strip(),
            "ref_text": desired_ref_text.strip(),
            "approval_status": "draft",
            "approved_at_utc": None,
            "approved_fingerprint": None,
        },
        "designed_voice_project": None,
        "existing_recordings": None,
        "dataset_project": None,
        "training_readiness": {
            "status": "not_ready",
            "blockers": [
                "Approve the desired base persona.",
                "Create and approve a dataset.",
                "Select a reference sample.",
                "Phase 22 must measure training feasibility before adapter training is treated as supported.",
            ],
            "warnings": [],
            "dataset_fingerprint": None,
        },
        "selected_reference_sample": None,
        "adapter_assignment": None,
        "adapter_provenance": None,
        "validation_status": {
            "status": "not_evaluated",
            "notes": [],
            "checked_at_utc": None,
        },
        "created_at_utc": timestamp,
        "updated_at_utc": timestamp,
        "project_fingerprint": "0" * 64,
    }
    project["project_fingerprint"] = compute_voice_training_project_fingerprint(project)
    return validate_voice_training_project(project)


def save_voice_training_project(
    project: dict[str, Any],
    path: str | Path,
    *,
    replace_existing: bool = False,
) -> dict[str, Any]:
    target = Path(path)
    if target.exists() and not replace_existing:
        raise VoiceTrainingProjectError(
            "A voice-training project already exists for this character."
        )
    normalized = validate_voice_training_project(project)
    expected_target = voice_training_project_path(
        target.parent.parent,
        normalized["character"]["id"],
    )
    if target != expected_target:
        raise VoiceTrainingProjectValidationError(
            "Voice-training project path does not match its character ID."
        )
    atomic_json_write(normalized, target)
    return normalized


def read_voice_training_project(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(str(target))
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise VoiceTrainingProjectCorruptError(
            f"Voice-training project could not be read: {exc}"
        ) from exc
    return validate_voice_training_project(value)


def inspect_voice_training_project(
    *,
    path: str | Path,
    expected_character_id: str | None = None,
    expected_source_fingerprint: str | None = None,
    expected_roster_fingerprint: str | None = None,
) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {
            "status": "absent",
            "path": str(target),
            "priority": None,
            "persona_status": None,
            "dataset_status": None,
            "readiness_status": None,
            "reference_selected": False,
            "adapter_assigned": False,
            "error": None,
        }
    try:
        project = read_voice_training_project(target)
    except VoiceTrainingProjectCorruptError as exc:
        return {
            "status": "corrupt",
            "path": str(target),
            "priority": None,
            "persona_status": None,
            "dataset_status": None,
            "readiness_status": None,
            "reference_selected": False,
            "adapter_assigned": False,
            "error": str(exc),
        }
    except VoiceTrainingProjectValidationError as exc:
        return {
            "status": "invalid",
            "path": str(target),
            "priority": None,
            "persona_status": None,
            "dataset_status": None,
            "readiness_status": None,
            "reference_selected": False,
            "adapter_assigned": False,
            "error": str(exc),
        }
    character = project["character"]
    compatibility_checks = (
        (
            expected_character_id,
            character["id"],
            "incompatible_character",
            "Voice-training project belongs to another character.",
        ),
        (
            expected_source_fingerprint,
            character["source_fingerprint"],
            "incompatible_source",
            "Voice-training project belongs to another source.",
        ),
        (
            expected_roster_fingerprint,
            character["roster_fingerprint"],
            "incompatible_roster",
            "Voice-training project belongs to another speaker identity catalog.",
        ),
    )
    for expected, actual, status, error in compatibility_checks:
        if expected is not None and expected != actual:
            return {
                "status": status,
                "path": str(target),
                "priority": project["priority"],
                "persona_status": project["desired_base_persona"]["approval_status"],
                "dataset_status": (
                    project["dataset_project"]["status"]
                    if project["dataset_project"] is not None
                    else None
                ),
                "readiness_status": project["training_readiness"]["status"],
                "reference_selected": project["selected_reference_sample"] is not None,
                "adapter_assigned": project["adapter_assignment"] is not None,
                "error": error,
            }
    return {
        "status": "candidate",
        "path": str(target),
        "priority": project["priority"],
        "persona_status": project["desired_base_persona"]["approval_status"],
        "dataset_status": (
            project["dataset_project"]["status"]
            if project["dataset_project"] is not None
            else None
        ),
        "readiness_status": project["training_readiness"]["status"],
        "reference_selected": project["selected_reference_sample"] is not None,
        "adapter_assigned": project["adapter_assignment"] is not None,
        "error": None,
    }


def build_voice_training_status(
    *,
    approved_roster: dict[str, Any] | None,
    projects_root: str | Path,
) -> dict[str, Any]:
    if approved_roster is None:
        return {
            "available": False,
            "reason": "No annotated script or approved character roster exists.",
            "source_fingerprint": None,
            "roster_fingerprint": None,
            "entries": [],
            "candidate_count": 0,
            "ready_count": 0,
            "invalid_count": 0,
        }
    roster = validate_character_roster(
        approved_roster,
        expected_status="approved",
    )
    source_fingerprint = roster["source"]["fingerprint"]
    roster_fingerprint = roster["roster_fingerprint"]
    entries = []
    for entry in roster["entries"]:
        eligible = (
            entry["speaking_status"] in {"speaker", "narrator"}
            and entry["resolution_status"] == "resolved"
        )
        if eligible:
            path = voice_training_project_path(projects_root, entry["id"])
            inspection = inspect_voice_training_project(
                path=path,
                expected_character_id=entry["id"],
                expected_source_fingerprint=source_fingerprint,
                expected_roster_fingerprint=roster_fingerprint,
            )
        else:
            path = voice_training_project_path(projects_root, entry["id"])
            inspection = {
                "status": "ineligible",
                "path": str(path),
                "priority": None,
                "persona_status": None,
                "dataset_status": None,
                "readiness_status": None,
                "reference_selected": False,
                "adapter_assigned": False,
                "error": None,
            }
        entries.append(
            {
                "character_id": entry["id"],
                "canonical_name": entry["canonical_name"],
                "display_name": entry["display_name"],
                "entity_kind": entry["entity_kind"],
                "speaking_status": entry["speaking_status"],
                "resolution_status": entry["resolution_status"],
                "eligible": eligible,
                **inspection,
            }
        )
    return {
        "available": True,
        "reason": None,
        "source_fingerprint": source_fingerprint,
        "roster_fingerprint": roster_fingerprint,
        "entries": entries,
        "candidate_count": sum(item["status"] == "candidate" for item in entries),
        "ready_count": sum(
            item["readiness_status"] == "ready_for_feasibility_review"
            for item in entries
        ),
        "invalid_count": sum(
            item["status"]
            in {
                "corrupt",
                "invalid",
                "incompatible_character",
                "incompatible_source",
                "incompatible_roster",
            }
            for item in entries
        ),
    }
