from __future__ import annotations

import copy
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from speaker_management import (
    SpeakerManagementError,
    load_speaker_operation,
    speaker_operation_undo_blocker,
)
from speaker_management_status_types import (
    ExcludedAudit,
    HistorySummary,
    RecoveryEntry,
    RecoveryLine,
    SpeakerRecovery,
)


def _key(value: str | None) -> str:
    return str(value or "").strip().casefold()


def _optional_text(value: Any) -> str | None:
    return str(value) if value is not None else None


def history_summaries(root: Path, limit: int = 50) -> list[HistorySummary]:
    directory = root / "speaker_management_history"
    if not directory.exists():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(
        directory.glob("*/operation.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    ):
        try:
            records.append(load_speaker_operation(
                root_dir=root,
                operation_id=path.parent.name,
            ))
        except SpeakerManagementError:
            continue
        if len(records) >= max(1, min(limit, 200)):
            break
    undone_ids = {
        str(record.get("undoes_operation_id"))
        for record in records
        if record.get("operation") == "undo"
        and record.get("undoes_operation_id")
    }
    current_hashes: dict[str, str | None] = {}
    summaries: list[HistorySummary] = []
    for record in records:
        operation_id = str(record.get("operation_id") or "")
        operation = str(record.get("operation") or "")
        undone = operation_id in undone_ids
        blocked_reason = None
        if operation != "undo" and not undone:
            blocked_reason = speaker_operation_undo_blocker(
                root_dir=root,
                record=record,
                current_hashes=current_hashes,
            )
        affected = record.get("affected_speakers")
        changed = record.get("changed_script_indices")
        invalidations = record.get("audio_invalidations")
        summaries.append({
            "operation_id": operation_id,
            "operation": operation,
            "at_utc": str(record.get("at_utc") or ""),
            "affected_speakers": [
                str(item) for item in affected
            ] if isinstance(affected, list) else [],
            "changed_script_indices": [
                item for item in changed
                if isinstance(item, int) and not isinstance(item, bool)
            ] if isinstance(changed, list) else [],
            "audio_invalidation_count": (
                len(invalidations) if isinstance(invalidations, list) else 0
            ),
            "source_script_fingerprint": _optional_text(
                record.get("source_script_fingerprint")
            ),
            "result_script_fingerprint": _optional_text(
                record.get("result_script_fingerprint")
            ),
            "undoes_operation_id": _optional_text(
                record.get("undoes_operation_id")
            ),
            "undone": undone,
            "undoable": operation != "undo" and not undone
            and blocked_reason is None,
            "undo_blocked_reason": blocked_reason,
        })
    return summaries


def build_speaker_recovery(
    *,
    speaker: str | None,
    lines: Sequence[RecoveryLine],
    entries: Sequence[RecoveryEntry],
    excluded_entities: Sequence[ExcludedAudit],
) -> SpeakerRecovery | None:
    requested = str(speaker or "").strip()
    if not requested:
        return None
    requested_key = _key(requested)
    active_entry = next(
        (
            entry
            for entry in entries
            if requested_key
            in {
                _key(entry.get("character_id")),
                _key(entry.get("canonical_name")),
                _key(entry.get("display_name")),
                _key(entry.get("script_voice_name")),
            }
        ),
        None,
    )
    script_speaker = str(
        active_entry.get("script_voice_name")
        if active_entry and active_entry.get("script_voice_name")
        else next(
            (
                line.get("speaker")
                for line in lines
                if _key(line.get("speaker")) == requested_key
            ),
            requested,
        )
    ).strip()
    spoken_lines = [
        copy.deepcopy(line)
        for line in lines
        if _key(line.get("speaker")) == _key(script_speaker)
        and str(line.get("text") or "").strip()
    ]
    identity_keys = {
        requested_key,
        _key(script_speaker),
        *(
            {
                _key(active_entry.get("canonical_name")),
                _key(active_entry.get("display_name")),
            }
            if active_entry
            else set()
        ),
    }
    excluded_audit = [
        copy.deepcopy(item)
        for item in excluded_entities
        if _key(item.get("name")) in identity_keys
    ]
    display_name = str(
        (excluded_audit[0].get("name") if excluded_audit else None)
        or (active_entry.get("display_name") if active_entry else None)
        or script_speaker
    ).strip()
    common = {
        "script_speaker": script_speaker,
        "display_name": display_name,
        "line_count": len(spoken_lines),
        "sample_lines": spoken_lines[:5],
        "sample_lines_truncated": len(spoken_lines) > 5,
        "excluded_audit": excluded_audit,
    }
    if active_entry is not None:
        return {
            **common,
            "state": "active",
            "blocked_reason": None,
            "eligible": False,
            "active_character_id": str(active_entry["character_id"]),
        }
    if not spoken_lines:
        return {
            **common,
            "state": "blocked_no_lines",
            "blocked_reason": "No spoken Script lines match this label.",
            "eligible": False,
            "active_character_id": None,
        }
    if not excluded_audit:
        return {
            **common,
            "state": "blocked_no_audit",
            "blocked_reason": (
                "No preserved exclusion audit matches this Script label. "
                "Review the current roster before adding an identity."
            ),
            "eligible": False,
            "active_character_id": None,
        }
    return {
        **common,
        "state": "eligible",
        "blocked_reason": None,
        "eligible": True,
        "active_character_id": None,
    }
