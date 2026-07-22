from __future__ import annotations

from pathlib import Path
from typing import Any

from script_voice_mapping import (
    load_script_voice_index,
    resolve_script_voice_name,
)
from voice_identity_context import (
    VoiceIdentityContextInvalidError,
    VoiceIdentityContextSourceMismatchError,
    VoiceIdentityContextUnavailableError,
    load_voice_identity_context,
)
from voice_training_actions import (
    VoiceTrainingActionError,
    VoiceTrainingConflictError,
    create_voice_training_project_file,
    mutate_voice_training_project_file,
)
from voice_training_projects import (
    VoiceTrainingProjectCompatibilityError,
    VoiceTrainingProjectCorruptError,
    VoiceTrainingProjectError,
    VoiceTrainingProjectValidationError,
    build_voice_training_project,
    build_voice_training_status,
    inspect_voice_training_project,
    read_voice_training_project,
    voice_training_project_path,
)


class VoiceTrainingApiError(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        detail: str,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail

    def as_detail(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.detail,
        }


def _load_identity_context(
    *,
    approved_roster_path: str | Path,
    source_text: str | None = None,
    current_source_fingerprint: str | None = None,
    required: bool = True,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    try:
        return load_voice_identity_context(
            approved_roster_path=approved_roster_path,
            source_text=source_text,
            current_source_fingerprint=current_source_fingerprint,
            required=required,
            allow_script_fallback=False,
        )
    except VoiceIdentityContextUnavailableError as exc:
        raise VoiceTrainingApiError(
            status_code=409,
            code="approved_roster_required",
            detail=str(exc),
        ) from exc
    except VoiceIdentityContextSourceMismatchError as exc:
        raise VoiceTrainingApiError(
            status_code=409,
            code="approved_roster_source_mismatch",
            detail=str(exc),
        ) from exc
    except VoiceIdentityContextInvalidError as exc:
        raise VoiceTrainingApiError(
            status_code=409,
            code="identity_context_invalid",
            detail=str(exc),
        ) from exc


def _load_approved_roster(
    *,
    approved_roster_path: str | Path,
    source_text: str | None = None,
    current_source_fingerprint: str | None = None,
    required: bool = True,
) -> dict[str, Any] | None:
    roster, _ = _load_identity_context(
        approved_roster_path=approved_roster_path,
        source_text=source_text,
        current_source_fingerprint=current_source_fingerprint,
        required=required,
    )
    return roster


def _find_roster_entry(
    roster: dict[str, Any],
    character_id: str,
) -> dict[str, Any]:
    entry = next(
        (
            item
            for item in roster["entries"]
            if item["id"] == character_id
        ),
        None,
    )
    if entry is None:
        raise VoiceTrainingApiError(
            status_code=404,
            code="character_not_found",
            detail=(
                "The selected speaker is not present in the current script or approved roster."
            ),
        )
    return entry


def _require_eligible_entry(entry: dict[str, Any]) -> None:
    if entry["speaking_status"] not in {"speaker", "narrator"}:
        raise VoiceTrainingApiError(
            status_code=409,
            code="character_ineligible",
            detail=(
                "Only speakers in the current script can become expressive "
                "voice-training candidates."
            ),
        )
    if entry["resolution_status"] != "resolved":
        raise VoiceTrainingApiError(
            status_code=409,
            code="character_unresolved",
            detail=(
                "Resolve this speaker identity before creating an expressive "
                "voice-training project."
            ),
        )


def get_voice_training_status_payload(
    *,
    approved_roster_path: str | Path,
    projects_root: str | Path,
    source_text: str | None = None,
    current_source_fingerprint: str | None = None,
    script_path: str | Path | None = None,
) -> dict[str, Any]:
    roster, context = _load_identity_context(
        approved_roster_path=approved_roster_path,
        source_text=source_text,
        current_source_fingerprint=current_source_fingerprint,
        required=False,
    )
    status = build_voice_training_status(
        approved_roster=roster,
        projects_root=projects_root,
    )
    status["source_compatible"] = context["source_compatible"]
    status["identity_source"] = context["identity_source"]
    status["roster_enriched"] = context["roster_enriched"]
    status["context_error"] = context["context_warning"]
    if roster is not None:
        resolved_script_path = (
            Path(script_path)
            if script_path is not None
            else Path(approved_roster_path).parent / "annotated_script.json"
        )
        speakers, line_speakers, speaker_counts = load_script_voice_index(
            resolved_script_path
        )
        roster_by_id = {
            entry["id"]: entry
            for entry in roster.get("entries") or []
        }
        for item in status.get("entries") or []:
            roster_entry = roster_by_id.get(item.get("character_id"))
            if roster_entry is None:
                continue
            item.update(
                resolve_script_voice_name(
                    roster_entry,
                    speakers=speakers,
                    line_speakers=line_speakers,
                    speaker_counts=speaker_counts,
                )
            )
    if roster is None:
        status["reason"] = (
            context["context_warning"]
            or "Approve the Character roster before creating Voice profiles."
        )
    return status


def get_voice_training_project_payload(
    *,
    approved_roster_path: str | Path,
    projects_root: str | Path,
    character_id: str,
    source_text: str | None = None,
    current_source_fingerprint: str | None = None,
) -> dict[str, Any]:
    roster = _load_approved_roster(
        approved_roster_path=approved_roster_path,
        source_text=source_text,
        current_source_fingerprint=current_source_fingerprint,
    )
    assert roster is not None
    entry = _find_roster_entry(roster, character_id)
    _require_eligible_entry(entry)

    try:
        path = voice_training_project_path(
            projects_root,
            character_id,
        )
    except VoiceTrainingProjectValidationError as exc:
        raise VoiceTrainingApiError(
            status_code=400,
            code="invalid_character_id",
            detail=str(exc),
        ) from exc

    inspection = inspect_voice_training_project(
        path=path,
        expected_character_id=character_id,
        expected_source_fingerprint=roster["source"]["fingerprint"],
        expected_roster_fingerprint=roster["roster_fingerprint"],
    )
    status = inspection["status"]
    if status == "absent":
        raise VoiceTrainingApiError(
            status_code=404,
            code="voice_training_project_not_found",
            detail=(
                "No expressive voice-training project exists for this character."
            ),
        )
    if status == "corrupt":
        raise VoiceTrainingApiError(
            status_code=409,
            code="voice_training_project_corrupt",
            detail=inspection["error"] or "The project file is corrupt.",
        )
    if status == "invalid":
        raise VoiceTrainingApiError(
            status_code=409,
            code="voice_training_project_invalid",
            detail=inspection["error"] or "The project file is invalid.",
        )
    if status.startswith("incompatible_"):
        raise VoiceTrainingApiError(
            status_code=409,
            code=status,
            detail=inspection["error"] or (
                "The project does not belong to the current speaker identity catalog."
            ),
        )
    try:
        return read_voice_training_project(path)
    except VoiceTrainingProjectCorruptError as exc:
        raise VoiceTrainingApiError(
            status_code=409,
            code="voice_training_project_corrupt",
            detail=str(exc),
        ) from exc
    except VoiceTrainingProjectValidationError as exc:
        raise VoiceTrainingApiError(
            status_code=409,
            code="voice_training_project_invalid",
            detail=str(exc),
        ) from exc


def create_voice_training_candidate_payload(
    *,
    approved_roster_path: str | Path,
    projects_root: str | Path,
    character_id: str,
    priority: str,
    desired_description: str = "",
    desired_ref_text: str = "",
    source_text: str | None = None,
    current_source_fingerprint: str | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    roster = _load_approved_roster(
        approved_roster_path=approved_roster_path,
        source_text=source_text,
        current_source_fingerprint=current_source_fingerprint,
    )
    assert roster is not None
    entry = _find_roster_entry(roster, character_id)
    _require_eligible_entry(entry)
    try:
        path = voice_training_project_path(
            projects_root,
            character_id,
        )
        project = build_voice_training_project(
            approved_roster=roster,
            character_id=character_id,
            priority=priority,
            desired_description=desired_description,
            desired_ref_text=desired_ref_text,
            created_at_utc=created_at_utc,
        )
        return create_voice_training_project_file(
            project=project,
            project_path=path,
        )
    except VoiceTrainingConflictError as exc:
        raise VoiceTrainingApiError(
            status_code=409,
            code="voice_training_project_exists",
            detail=str(exc),
        ) from exc
    except VoiceTrainingProjectCompatibilityError as exc:
        raise VoiceTrainingApiError(
            status_code=409,
            code="character_ineligible",
            detail=str(exc),
        ) from exc
    except (
        VoiceTrainingProjectValidationError,
        VoiceTrainingActionError,
    ) as exc:
        raise VoiceTrainingApiError(
            status_code=422,
            code="invalid_voice_training_project",
            detail=str(exc),
        ) from exc


def apply_voice_training_action_payload(
    *,
    approved_roster_path: str | Path,
    projects_root: str | Path,
    character_id: str,
    expected_fingerprint: str,
    action: str,
    payload: dict[str, Any] | None = None,
    source_text: str | None = None,
    current_source_fingerprint: str | None = None,
    at_utc: str | None = None,
) -> dict[str, Any]:
    roster = _load_approved_roster(
        approved_roster_path=approved_roster_path,
        source_text=source_text,
        current_source_fingerprint=current_source_fingerprint,
    )
    assert roster is not None
    entry = _find_roster_entry(roster, character_id)
    _require_eligible_entry(entry)
    try:
        path = voice_training_project_path(
            projects_root,
            character_id,
        )
    except VoiceTrainingProjectValidationError as exc:
        raise VoiceTrainingApiError(
            status_code=400,
            code="invalid_character_id",
            detail=str(exc),
        ) from exc
    if not path.exists():
        raise VoiceTrainingApiError(
            status_code=404,
            code="voice_training_project_not_found",
            detail=(
                "No expressive voice-training project exists for this character."
            ),
        )
    try:
        return mutate_voice_training_project_file(
            project_path=path,
            expected_fingerprint=expected_fingerprint,
            action=action,
            payload=payload,
            expected_character_id=character_id,
            expected_source_fingerprint=roster["source"]["fingerprint"],
            expected_roster_fingerprint=roster["roster_fingerprint"],
            at_utc=at_utc,
        )
    except VoiceTrainingConflictError as exc:
        message = str(exc)
        code = (
            "stale_voice_training_project"
            if "changed after this action was loaded" in message
            else "voice_training_conflict"
        )
        raise VoiceTrainingApiError(
            status_code=409,
            code=code,
            detail=message,
        ) from exc
    except VoiceTrainingProjectCorruptError as exc:
        raise VoiceTrainingApiError(
            status_code=409,
            code="voice_training_project_corrupt",
            detail=str(exc),
        ) from exc
    except VoiceTrainingProjectValidationError as exc:
        raise VoiceTrainingApiError(
            status_code=422,
            code="invalid_voice_training_project",
            detail=str(exc),
        ) from exc
    except VoiceTrainingActionError as exc:
        raise VoiceTrainingApiError(
            status_code=422,
            code="voice_training_action_rejected",
            detail=str(exc),
        ) from exc
    except VoiceTrainingProjectError as exc:
        raise VoiceTrainingApiError(
            status_code=409,
            code="voice_training_project_error",
            detail=str(exc),
        ) from exc
