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
    SpeakerManagementError,
    SpeakerManagementValidationError,
    apply_speaker_operation,
    inspect_speaker_lines,
    load_speaker_operation,
    undo_speaker_operation,
)


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


def _history_summaries(root: Path, limit: int = 50) -> list[dict[str, Any]]:
    directory = root / "speaker_management_history"
    if not directory.exists():
        return []
    summaries = []
    for path in sorted(
        directory.glob("*/operation.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    ):
        try:
            record = load_speaker_operation(
                root_dir=root,
                operation_id=path.parent.name,
            )
        except SpeakerManagementError:
            continue
        summaries.append(
            {
                "operation_id": record["operation_id"],
                "operation": record["operation"],
                "at_utc": record["at_utc"],
                "affected_speakers": record.get("affected_speakers", []),
                "changed_script_indices": record.get(
                    "changed_script_indices",
                    [],
                ),
                "audio_invalidation_count": len(
                    record.get("audio_invalidations", [])
                ),
                "source_script_fingerprint": record.get(
                    "source_script_fingerprint"
                ),
                "result_script_fingerprint": record.get(
                    "result_script_fingerprint"
                ),
            }
        )
        if len(summaries) >= max(1, min(limit, 200)):
            break
    return summaries


def get_speaker_management_status_payload(
    *,
    root_dir: str | Path,
    speaker: str | None = None,
) -> dict[str, Any]:
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
            "history": _history_summaries(root),
        }
    if roster is None:
        return {
            **lines,
            "available": False,
            "reason": "No approved character roster exists.",
            "roster_fingerprint": None,
            "entries": [],
            "history": _history_summaries(root),
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
    selected_lines = lines["lines"]
    selected_script_voice = None
    if speaker is not None:
        requested = str(speaker).strip().casefold()
        selected_entry = next(
            (
                item
                for item in entries
                if requested
                in {
                    str(item["character_id"]).casefold(),
                    str(item["canonical_name"]).casefold(),
                    str(item["display_name"]).casefold(),
                    str(item.get("script_voice_name") or "").casefold(),
                }
            ),
            None,
        )
        selected_script_voice = (
            selected_entry.get("script_voice_name")
            if selected_entry is not None
            else str(speaker).strip()
        )
        selected_lines = [
            item
            for item in lines["lines"]
            if item["speaker"] == selected_script_voice
        ]
    return {
        **lines,
        "lines": selected_lines,
        "selected_script_voice": selected_script_voice,
        "available": True,
        "reason": None,
        "roster_fingerprint": roster["roster_fingerprint"],
        "entries": entries,
        "history": _history_summaries(root),
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
            if "script changed" in message
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
