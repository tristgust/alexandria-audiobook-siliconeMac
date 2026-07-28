from __future__ import annotations

from pathlib import Path
from typing import Any

from roster_context import (
    RosterContextError,
    load_project_roster_context,
)
from script_voice_mapping import (
    build_script_voice_index,
    resolve_script_voice_name,
)
from speaker_management import (
    SpeakerManagementConflictError,
    SpeakerManagementValidationError,
    apply_speaker_operation,
    inspect_speaker_lines,
    load_speaker_operation,
    undo_speaker_operation,
)
from speaker_management_status import (
    build_speaker_recovery,
    history_summaries,
)
from speaker_management_status_types import SpeakerManagementStatusPayload


class SpeakerManagementApiError(RuntimeError):
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
        return {"code": self.code, "message": self.detail}


def get_speaker_management_status_payload(
    *,
    root_dir: str | Path,
    speaker: str | None = None,
) -> SpeakerManagementStatusPayload:
    root = Path(root_dir)
    lines = inspect_speaker_lines(
        root_dir=root,
        speaker=None,
    )
    try:
        roster, _, _ = load_project_roster_context(root_dir=root)
    except RosterContextError as exc:
        return {
            **lines,
            "available": False,
            "reason": str(exc),
            "roster_fingerprint": None,
            "entries": [],
            "speaker_recovery": None,
            "history": history_summaries(root),
        }
    if roster is None:
        return {
            **lines,
            "available": False,
            "reason": "No approved character roster exists.",
            "roster_fingerprint": None,
            "entries": [],
            "speaker_recovery": None,
            "history": history_summaries(root),
        }
    speakers, line_speakers, speaker_counts = build_script_voice_index(
        lines["lines"]
    )
    entries = []
    for entry in roster["entries"]:
        mapping = resolve_script_voice_name(
            entry,
            speakers=speakers,
            line_speakers=line_speakers,
            speaker_counts=speaker_counts,
        )
        entries.append(
            {
                "character_id": entry["id"],
                "canonical_name": entry["canonical_name"],
                "display_name": entry["display_name"],
                "entity_kind": entry["entity_kind"],
                "speaking_status": entry["speaking_status"],
                "resolution_status": entry["resolution_status"],
                "aliases": entry["aliases"],
                "evidence_count": len(entry["evidence"]),
                "line_count": mapping["script_line_count"],
                **mapping,
            }
        )
    recovery = build_speaker_recovery(
        speaker=speaker,
        lines=lines["lines"],
        entries=entries,
        excluded_entities=roster.get("excluded_entities", []),
    )
    selected_script_voice = (
        recovery["script_speaker"] if recovery is not None else None
    )
    selected_lines = lines["lines"]
    if selected_script_voice is not None:
        selected_lines = [
            item
            for item in lines["lines"]
            if str(item["speaker"]).casefold()
            == str(selected_script_voice).casefold()
        ]
    return {
        **lines,
        "lines": selected_lines,
        "selected_script_voice": selected_script_voice,
        "available": True,
        "reason": None,
        "roster_fingerprint": roster["roster_fingerprint"],
        "entries": entries,
        "speaker_recovery": recovery,
        "history": history_summaries(root),
    }


def apply_speaker_operation_payload(
    *,
    root_dir: str | Path,
    operation: str,
    expected_script_fingerprint: str,
    payload: dict[str, Any],
    at_utc: str | None = None,
) -> dict[str, Any]:
    try:
        record = apply_speaker_operation(
            root_dir=root_dir,
            operation=operation,
            expected_script_fingerprint=expected_script_fingerprint,
            payload=payload,
            at_utc=at_utc,
        )
    except SpeakerManagementConflictError as exc:
        message = str(exc)
        code = (
            "stale_speaker_management"
            if "changed after" in message
            else "speaker_management_conflict"
        )
        raise SpeakerManagementApiError(
            status_code=409,
            code=code,
            detail=message,
        ) from exc
    except SpeakerManagementValidationError as exc:
        raise SpeakerManagementApiError(
            status_code=422,
            code="speaker_management_rejected",
            detail=str(exc),
        ) from exc
    except RosterContextError as exc:
        raise SpeakerManagementApiError(
            status_code=409,
            code="speaker_management_context_unavailable",
            detail=str(exc),
        ) from exc
    return {
        "operation": record,
        "status": get_speaker_management_status_payload(
            root_dir=root_dir,
        ),
    }


def undo_speaker_operation_payload(
    *,
    root_dir: str | Path,
    operation_id: str,
    at_utc: str | None = None,
) -> dict[str, Any]:
    try:
        record = undo_speaker_operation(
            root_dir=root_dir,
            operation_id=operation_id,
            at_utc=at_utc,
        )
    except SpeakerManagementConflictError as exc:
        raise SpeakerManagementApiError(
            status_code=409,
            code="speaker_management_undo_conflict",
            detail=str(exc),
        ) from exc
    except SpeakerManagementValidationError as exc:
        raise SpeakerManagementApiError(
            status_code=404,
            code="speaker_management_operation_not_found",
            detail=str(exc),
        ) from exc
    return {
        "operation": record,
        "status": get_speaker_management_status_payload(
            root_dir=root_dir,
        ),
    }


def get_speaker_operation_payload(
    *,
    root_dir: str | Path,
    operation_id: str,
) -> dict[str, Any]:
    try:
        return load_speaker_operation(
            root_dir=root_dir,
            operation_id=operation_id,
        )
    except SpeakerManagementValidationError as exc:
        raise SpeakerManagementApiError(
            status_code=404,
            code="speaker_management_operation_not_found",
            detail=str(exc),
        ) from exc
