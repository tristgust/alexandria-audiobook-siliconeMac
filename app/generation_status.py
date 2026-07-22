from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from generation_state import (
    SCHEMA_VERSION as STATE_SCHEMA_VERSION,
    GenerationStateError,
    fingerprint_value,
    validate_generation_state,
)


METADATA_SCHEMA_VERSION = 1


_REASON_TEXT = {
    "source_changed": (
        "Source changed",
        "The selected source text no longer matches the source "
        "used by this checkpoint.",
    ),
    "prompt_changed": (
        "Prompt changed",
        "One or more script-generation prompts changed.",
    ),
    "model_changed": (
        "Model changed",
        "The configured script-generation model changed.",
    ),
    "backend_changed": (
        "Backend changed",
        "The configured LLM backend changed.",
    ),
    "runtime_settings_changed": (
        "Runtime settings changed",
        "One or more structured-output runtime settings changed.",
    ),
    "sampling_changed": (
        "Sampling changed",
        "One or more generation or sampling settings changed.",
    ),
    "chunk_size_changed": (
        "Chunk size changed",
        "The configured source chunk size changed.",
    ),
    "chunk_layout_changed": (
        "Chunk layout changed",
        "The ordered source chunks no longer match this checkpoint.",
    ),
    "state_schema_changed": (
        "Checkpoint schema changed",
        "The checkpoint uses a different state schema version.",
    ),
    "checkpoint_schema_invalid": (
        "Checkpoint schema is invalid",
        "The checkpoint does not satisfy the expected state contract.",
    ),
    "auditor_contract_changed": (
        "Auditor contract changed",
        "The script-fidelity auditor contract changed.",
    ),
    "generation_identity_changed": (
        "Generation identity changed",
        "The generation fingerprint changed, but the exact changed "
        "setting cannot be identified from this checkpoint.",
    ),
    "unknown_incompatibility": (
        "Unknown incompatibility",
        "The checkpoint differs from the current run for an "
        "unclassified reason.",
    ),
    "checkpoint_corrupt": (
        "Checkpoint is corrupt",
        "The checkpoint is not readable JSON.",
    ),
    "current_inputs_unavailable": (
        "Current inputs unavailable",
        "The current source and generation identity could not be "
        "computed.",
    ),
}


_IDENTITY_GROUPS = (
    ("model_changed", ("model_name",)),
    ("backend_changed", ("backend",)),
    (
        "prompt_changed",
        (
            "system_prompt",
            "user_prompt_template",
        ),
    ),
    (
        "runtime_settings_changed",
        (
            "base_url",
            "thinking",
            "structured_output",
            "corrective_retry",
        ),
    ),
    (
        "sampling_changed",
        (
            "max_tokens",
            "temperature",
            "top_p",
            "top_k",
            "min_p",
            "presence_penalty",
            "banned_tokens",
        ),
    ),
    ("chunk_size_changed", ("chunk_size",)),
)


def _is_int(value: Any) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
    )


def _reason(
    code: str,
    detail: str | None = None,
) -> dict[str, str]:
    title, default_detail = _REASON_TEXT[code]

    return {
        "code": code,
        "title": title,
        "explanation": (
            detail
            if detail is not None
            else default_detail
        ),
    }


def _deduplicate_reasons(
    reasons: list[dict[str, str]],
) -> list[dict[str, str]]:
    seen = set()
    result = []

    for reason in reasons:
        code = reason["code"]

        if code in seen:
            continue

        seen.add(code)
        result.append(reason)

    return result


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _safe_progress(raw: dict[str, Any]) -> tuple[int, int]:
    completed_value = raw.get("completed_chunks")
    total_value = raw.get("total_chunks")

    completed = (
        len(completed_value)
        if isinstance(completed_value, list)
        else 0
    )
    total = (
        total_value
        if _is_int(total_value) and total_value >= 0
        else 0
    )

    return completed, total


def _percent_complete(
    completed: int,
    total: int,
) -> float:
    if total <= 0:
        return 0.0

    return round(
        (completed / total) * 100.0,
        2,
    )


def _identity_reasons(
    saved: dict[str, Any],
    current: dict[str, Any],
) -> list[dict[str, str]]:
    reasons = []
    classified_fields = set()

    for code, fields in _IDENTITY_GROUPS:
        changed = [
            field
            for field in fields
            if saved.get(field) != current.get(field)
        ]

        classified_fields.update(fields)

        if changed:
            reasons.append(
                _reason(
                    code,
                    _REASON_TEXT[code][1]
                    + " Changed fields: "
                    + ", ".join(changed)
                    + ".",
                )
            )

    remaining_fields = (
        set(saved)
        | set(current)
    ) - classified_fields

    remaining_changed = sorted(
        field
        for field in remaining_fields
        if saved.get(field) != current.get(field)
    )

    if remaining_changed:
        reasons.append(
            _reason(
                "generation_identity_changed",
                _REASON_TEXT[
                    "generation_identity_changed"
                ][1]
                + " Changed fields: "
                + ", ".join(remaining_changed)
                + ".",
            )
        )

    return reasons


def _checkpoint_response(
    *,
    status: str,
    completed: int = 0,
    total: int = 0,
    completed_entries_present: bool = False,
    resumable: bool = False,
    source_match: bool | None = None,
    generation_match: bool | None = None,
    chunk_match: bool | None = None,
    auditor_match: bool | None = None,
    reasons: list[dict[str, str]] | None = None,
    explanation: str,
) -> dict[str, Any]:
    next_chunk = (
        completed + 1
        if completed < total
        else None
    )
    reason_list = reasons or []

    return {
        "status": status,
        "resumable": bool(resumable),
        "completed_chunks": completed,
        "total_chunks": total,
        "next_chunk": next_chunk,
        "percent_complete": (
            _percent_complete(
                completed,
                total,
            )
        ),
        "completed_entries_present": (
            completed_entries_present
        ),
        "source_fingerprint_match": source_match,
        "generation_fingerprint_match": (
            generation_match
        ),
        "chunk_layout_match": chunk_match,
        "auditor_contract_match": auditor_match,
        "reason_codes": [
            reason["code"]
            for reason in reason_list
        ],
        "reasons": reason_list,
        "explanation": explanation,
    }


def inspect_generation_checkpoint(
    *,
    checkpoint_path: str | Path,
    current_snapshot: dict[str, Any] | None,
    current_error: str | None = None,
) -> dict[str, Any]:
    path = Path(checkpoint_path)

    if not path.exists():
        return _checkpoint_response(
            status="none",
            explanation="No saved generation checkpoint exists.",
        )

    try:
        raw = _read_json(path)
    except (
        OSError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        reason = _reason(
            "checkpoint_corrupt",
            f"The checkpoint could not be read: {exc}",
        )
        return _checkpoint_response(
            status="corrupt",
            reasons=[reason],
            explanation=reason["explanation"],
        )

    if not isinstance(raw, dict):
        reason = _reason(
            "checkpoint_schema_invalid",
            "The checkpoint root must be a JSON object.",
        )
        return _checkpoint_response(
            status="invalid",
            reasons=[reason],
            explanation=reason["explanation"],
        )

    completed, total = _safe_progress(raw)

    if raw.get("schema_version") != STATE_SCHEMA_VERSION:
        reason = _reason(
            "state_schema_changed",
            "The checkpoint uses schema "
            f"{raw.get('schema_version')!r}; this installation "
            f"expects schema {STATE_SCHEMA_VERSION}.",
        )
        return _checkpoint_response(
            status="incompatible",
            completed=completed,
            total=total,
            reasons=[reason],
            explanation=reason["explanation"],
        )

    try:
        state = validate_generation_state(raw)
    except GenerationStateError as exc:
        reason = _reason(
            "checkpoint_schema_invalid",
            str(exc),
        )
        return _checkpoint_response(
            status="invalid",
            completed=completed,
            total=total,
            reasons=[reason],
            explanation=reason["explanation"],
        )

    completed_records = state["completed_chunks"]
    completed = len(completed_records)
    total = state["total_chunks"]
    completed_entries_present = (
        bool(completed_records)
        and all(
            bool(record.get("entries"))
            for record in completed_records
        )
    )

    if current_snapshot is None:
        reason = _reason(
            "current_inputs_unavailable",
            current_error
            or _REASON_TEXT[
                "current_inputs_unavailable"
            ][1],
        )
        return _checkpoint_response(
            status="unknown",
            completed=completed,
            total=total,
            completed_entries_present=(
                completed_entries_present
            ),
            reasons=[reason],
            explanation=reason["explanation"],
        )

    source_match = (
        state["source_fingerprint"]
        == current_snapshot["source_fingerprint"]
    )
    generation_match = (
        state["generation_fingerprint"]
        == current_snapshot["generation_fingerprint"]
    )
    chunk_match = (
        state["chunk_fingerprints"]
        == current_snapshot["chunk_fingerprints"]
    )

    saved_auditor = state.get(
        "auditor_contract_version"
    )
    current_auditor = current_snapshot.get(
        "auditor_contract_version"
    )
    auditor_match = (
        None
        if current_auditor is None
        else saved_auditor == current_auditor
    )

    reasons = []

    if not source_match:
        saved_source = state.get("source")
        saved_name = (
            saved_source.get("basename")
            if isinstance(saved_source, dict)
            else None
        )
        current_name = current_snapshot.get(
            "source_basename"
        )
        detail = _REASON_TEXT["source_changed"][1]

        if saved_name or current_name:
            detail += (
                " Saved source: "
                f"{saved_name or 'unknown'}; "
                "current source: "
                f"{current_name or 'unknown'}."
            )

        reasons.append(
            _reason(
                "source_changed",
                detail,
            )
        )

    if not generation_match:
        saved_identity = state.get(
            "generation_identity"
        )
        current_identity = current_snapshot.get(
            "generation_identity"
        )

        if (
            isinstance(saved_identity, dict)
            and isinstance(current_identity, dict)
        ):
            identity_reasons = _identity_reasons(
                saved_identity,
                current_identity,
            )

            if identity_reasons:
                reasons.extend(identity_reasons)
            else:
                reasons.append(
                    _reason(
                        "unknown_incompatibility"
                    )
                )
        else:
            reasons.append(
                _reason(
                    "generation_identity_changed"
                )
            )

    if not chunk_match:
        reasons.append(
            _reason(
                "chunk_layout_changed"
            )
        )

    if auditor_match is False:
        reasons.append(
            _reason(
                "auditor_contract_changed"
            )
        )

    reasons = _deduplicate_reasons(reasons)

    if reasons:
        titles = "; ".join(
            reason["title"]
            for reason in reasons
        )
        return _checkpoint_response(
            status="incompatible",
            completed=completed,
            total=total,
            completed_entries_present=(
                completed_entries_present
            ),
            source_match=source_match,
            generation_match=generation_match,
            chunk_match=chunk_match,
            auditor_match=auditor_match,
            reasons=reasons,
            explanation=(
                "Generation cannot resume: "
                + titles
                + "."
            ),
        )

    finalization_pending = (
        total > 0
        and completed == total
    )

    if finalization_pending:
        explanation = (
            f"All {total} chunks are complete. "
            "Final script and metadata finalization can be retried "
            "without regenerating chunks."
        )
        status = "finalization_pending"
    elif completed > 0:
        explanation = (
            f"Generation can resume from chunk "
            f"{completed + 1} of {total}."
        )
        status = "compatible"
    else:
        explanation = (
            "The checkpoint matches the current inputs. "
            f"Generation will start at chunk 1 of {total}."
        )
        status = "compatible"

    return _checkpoint_response(
        status=status,
        completed=completed,
        total=total,
        completed_entries_present=(
            completed_entries_present
        ),
        resumable=(
            completed > 0
            or finalization_pending
        ),
        source_match=True,
        generation_match=True,
        chunk_match=True,
        auditor_match=auditor_match,
        explanation=explanation,
    )


def _inspect_script(
    script_path: str | Path,
) -> dict[str, Any]:
    path = Path(script_path)

    if not path.exists():
        return {
            "exists": False,
            "status": "missing",
            "entries": None,
            "entry_count": None,
            "fingerprint": None,
            "error": None,
        }

    try:
        value = _read_json(path)
    except (
        OSError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        return {
            "exists": True,
            "status": "corrupt",
            "entries": None,
            "entry_count": None,
            "fingerprint": None,
            "error": str(exc),
        }

    if (
        not isinstance(value, list)
        or not all(
            isinstance(entry, dict)
            for entry in value
        )
    ):
        return {
            "exists": True,
            "status": "invalid",
            "entries": None,
            "entry_count": None,
            "fingerprint": None,
            "error": (
                "annotated_script.json must be "
                "a JSON array of objects."
            ),
        }

    return {
        "exists": True,
        "status": "valid",
        "entries": value,
        "entry_count": len(value),
        "fingerprint": fingerprint_value(value),
        "error": None,
    }


def _metadata_validation_errors(
    value: Any,
) -> list[str]:
    if not isinstance(value, dict):
        return ["Metadata root must be a JSON object."]

    errors = []

    if value.get("schema_version") != METADATA_SCHEMA_VERSION:
        errors.append(
            "Unsupported metadata schema version."
        )

    if not isinstance(
        value.get("generated_at_utc"),
        str,
    ):
        errors.append(
            "generated_at_utc must be text."
        )

    source = value.get("source")
    generation = value.get("generation")
    result = value.get("result")
    resume = value.get("resume")

    if not isinstance(source, dict):
        errors.append("source must be an object.")
    else:
        if not isinstance(source.get("basename"), str):
            errors.append("source.basename must be text.")
        source_verification = source.get(
            "verification_status",
            "verified",
        )
        if source_verification not in {"verified", "unverified"}:
            errors.append(
                "source.verification_status must be verified or unverified."
            )
        source_fingerprint = source.get("fingerprint")
        if source_verification == "unverified":
            if source_fingerprint is not None:
                errors.append(
                    "Unverified source metadata must not claim a fingerprint."
                )
        elif not isinstance(source_fingerprint, str):
            errors.append("source.fingerprint must be text.")
        if not _is_int(source.get("character_count")):
            errors.append(
                "source.character_count must be an integer."
            )
        if not _is_int(source.get("chunk_count")):
            errors.append(
                "source.chunk_count must be an integer."
            )

    if not isinstance(generation, dict):
        errors.append("generation must be an object.")
    else:
        if not isinstance(
            generation.get("fingerprint"),
            str,
        ):
            errors.append(
                "generation.fingerprint must be text."
            )
        if not isinstance(
            generation.get("effective_identity"),
            dict,
        ):
            errors.append(
                "generation.effective_identity must be an object."
            )

    if not isinstance(result, dict):
        errors.append("result must be an object.")
    else:
        if not isinstance(
            result.get("script_fingerprint"),
            str,
        ):
            errors.append(
                "result.script_fingerprint must be text."
            )
        if not _is_int(result.get("entry_count")):
            errors.append(
                "result.entry_count must be an integer."
            )
        speaker_labels = result.get("speaker_labels")
        if (
            not isinstance(speaker_labels, list)
            or not all(
                isinstance(label, str)
                for label in speaker_labels
            )
        ):
            errors.append(
                "result.speaker_labels must be a list of text."
            )

    if not isinstance(resume, dict):
        errors.append("resume must be an object.")
    else:
        if not isinstance(resume.get("resumed"), bool):
            errors.append(
                "resume.resumed must be boolean."
            )
        if not _is_int(
            resume.get(
                "previously_completed_chunks"
            )
        ):
            errors.append(
                "resume.previously_completed_chunks "
                "must be an integer."
            )

    return errors


def inspect_generation_result(
    *,
    script_path: str | Path,
    metadata_path: str | Path,
    finalization_pending: bool,
) -> dict[str, Any]:
    script = _inspect_script(script_path)
    metadata_target = Path(metadata_path)

    metadata_exists = metadata_target.exists()
    metadata_status = "missing"
    metadata_value = None
    errors = []

    if metadata_exists:
        try:
            candidate = _read_json(metadata_target)
        except (
            OSError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            metadata_status = "corrupt"
            errors.append(str(exc))
        else:
            validation_errors = (
                _metadata_validation_errors(
                    candidate
                )
            )

            if validation_errors:
                metadata_status = "invalid"
                errors.extend(validation_errors)
            else:
                metadata_status = "valid"
                metadata_value = candidate

    if (
        metadata_status == "valid"
        and script["status"] == "valid"
    ):
        metadata_result = metadata_value["result"]

        if (
            metadata_result["script_fingerprint"]
            != script["fingerprint"]
        ):
            metadata_status = "invalid"
            errors.append(
                "Metadata script fingerprint does not match "
                "annotated_script.json."
            )

        if (
            metadata_result["entry_count"]
            != script["entry_count"]
        ):
            metadata_status = "invalid"
            errors.append(
                "Metadata entry count does not match "
                "annotated_script.json."
            )

    if script["status"] == "missing":
        result_status = (
            "orphan_metadata"
            if metadata_exists
            else "missing"
        )
    elif script["status"] != "valid":
        result_status = "script_" + script["status"]
    elif metadata_status == "valid":
        result_status = "complete"
    elif (
        metadata_status == "missing"
        and finalization_pending
    ):
        result_status = "finalization_pending"
    elif metadata_status == "missing":
        result_status = "legacy"
        metadata_status = "legacy"
    else:
        result_status = "metadata_" + metadata_status

    return {
        "status": result_status,
        "script_exists": script["exists"],
        "script_status": script["status"],
        "script_entry_count": script["entry_count"],
        "script_fingerprint": script["fingerprint"],
        "metadata_exists": metadata_exists,
        "metadata_status": metadata_status,
        "metadata": metadata_value,
        "errors": (
            ([script["error"]] if script["error"] else [])
            + errors
        ),
    }


def build_generation_status(
    *,
    checkpoint_path: str | Path,
    script_path: str | Path,
    metadata_path: str | Path,
    current_snapshot: dict[str, Any] | None,
    current_error: str | None,
    process_running: bool,
    process_logs: list[str],
) -> dict[str, Any]:
    checkpoint = inspect_generation_checkpoint(
        checkpoint_path=checkpoint_path,
        current_snapshot=current_snapshot,
        current_error=current_error,
    )
    result = inspect_generation_result(
        script_path=script_path,
        metadata_path=metadata_path,
        finalization_pending=(
            checkpoint["status"]
            == "finalization_pending"
        ),
    )

    return {
        "process": {
            "running": bool(process_running),
            "logs": list(process_logs),
        },
        "checkpoint": checkpoint,
        "result": result,
    }
