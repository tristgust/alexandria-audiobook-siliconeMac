from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


RECOVERY_SCHEMA_VERSION = 1
RECOVERY_STATES = frozenset(
    {
        "new",
        "running",
        "resumable",
        "finalization_only",
        "restart_required",
        "complete",
        "blocked",
        "invalid",
        "unavailable",
    }
)


class RecoveryStatusError(ValueError):
    """The model-free recovery inputs do not satisfy the public contract."""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _integer(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def capped_logs(
    process: Mapping[str, Any] | None,
    *,
    limit: int = 200,
) -> dict[str, Any]:
    process = _mapping(process)
    raw_lines = _list(process.get("logs"))
    safe_lines = [str(line) for line in raw_lines if str(line).strip()]
    if limit < 1:
        raise RecoveryStatusError("Log limit must be positive.")
    return {
        "running": bool(process.get("running")),
        "cancel_requested": bool(process.get("cancel")),
        "lines": safe_lines[-limit:],
        "line_count": len(safe_lines),
        "truncated": len(safe_lines) > limit,
    }


def action(
    kind: str,
    label: str,
    *,
    endpoint: str | None = None,
    method: str = "POST",
    payload: Mapping[str, Any] | None = None,
    tab: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": kind,
        "label": label,
    }
    if endpoint:
        result["endpoint"] = endpoint
        result["method"] = method.upper()
    if payload:
        result["payload"] = dict(payload)
    if tab:
        result["tab"] = tab
    return result


def progress(
    *,
    completed: Any = 0,
    total: Any = 0,
    next_unit: Any = None,
    unit_label: str,
    last_checkpoint_at: str | None = None,
) -> dict[str, Any]:
    completed_value = _integer(completed)
    total_value = _integer(total)
    next_value = None
    if next_unit is not None:
        next_value = _integer(next_unit)
        if next_value == 0 and str(next_unit).strip() not in {"0", "0.0"}:
            next_value = None
    return {
        "completed": completed_value,
        "total": total_value,
        "next_unit": next_value,
        "unit_label": unit_label,
        "last_checkpoint_at": last_checkpoint_at,
    }


def stage(
    stage_id: str,
    label: str,
    state: str,
    *,
    summary: str,
    reason: str | None = None,
    primary_action: Mapping[str, Any] | None = None,
    discard_action: Mapping[str, Any] | None = None,
    stage_progress: Mapping[str, Any] | None = None,
    process: Mapping[str, Any] | None = None,
    identity: Mapping[str, Any] | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if state not in RECOVERY_STATES:
        raise RecoveryStatusError(
            f"Unsupported recovery state for {stage_id}: {state}"
        )
    return {
        "id": stage_id,
        "label": label,
        "state": state,
        "summary": summary,
        "reason": reason,
        "primary_action": dict(primary_action) if primary_action else None,
        "discard_action": dict(discard_action) if discard_action else None,
        "progress": dict(stage_progress) if stage_progress else None,
        "process": capped_logs(process),
        "identity": dict(identity) if identity else {},
        "details": dict(details) if details else {},
    }


def _source_block_reason(source: Mapping[str, Any]) -> str | None:
    if source.get("persisted") is not True:
        return source.get("error") or "No source book has been selected."
    if source.get("exists") is not True:
        return source.get("error") or "The saved source book no longer exists."
    if source.get("readable") is False:
        return source.get("error") or "The saved source book cannot be read."
    return None


def build_script_stage(
    status: Mapping[str, Any],
    source: Mapping[str, Any],
    *,
    checkpoint_at: str | None = None,
) -> dict[str, Any]:
    process_state = _mapping(status.get("process"))
    checkpoint = _mapping(status.get("checkpoint"))
    result = _mapping(status.get("result"))
    checkpoint_status = _text(checkpoint.get("status")) or "none"
    completed = _integer(checkpoint.get("completed_chunks"))
    total = _integer(checkpoint.get("total_chunks"))
    next_chunk = checkpoint.get("next_chunk")
    source_reason = _source_block_reason(source)
    checkpoint_exists = checkpoint_status != "none"
    discard = (
        action(
            "discard_script_checkpoint",
            "Discard script checkpoint",
            endpoint="/api/script_generation/discard",
        )
        if checkpoint_exists and not process_state.get("running")
        else None
    )
    common = {
        "stage_progress": progress(
            completed=completed,
            total=total,
            next_unit=next_chunk,
            unit_label="chunk",
            last_checkpoint_at=checkpoint_at,
        ),
        "process": process_state,
        "identity": {
            "source_basename": source.get("basename"),
            "source_path": source.get("path"),
            "script_fingerprint": result.get("script_fingerprint"),
        },
        "details": {
            "checkpoint_status": checkpoint_status,
            "reason_codes": _list(checkpoint.get("reason_codes")),
            "script_exists": bool(result.get("script_exists")),
            "script_status": result.get("script_status"),
            "metadata_status": result.get("metadata_status"),
        },
    }

    if process_state.get("running"):
        return stage(
            "script",
            "Script",
            "running",
            summary="Script generation is running.",
            **common,
        )
    if checkpoint_status == "compatible":
        next_value = _integer(next_chunk, completed + 1)
        return stage(
            "script",
            "Script",
            "resumable",
            summary=f"Resume script from chunk {next_value}.",
            primary_action=action(
                "resume_script",
                f"Resume script from chunk {next_value}",
                endpoint="/api/generate_script",
            ),
            discard_action=discard,
            **common,
        )
    if checkpoint_status == "finalization_pending":
        return stage(
            "script",
            "Script",
            "finalization_only",
            summary="All script chunks are complete; final files still need to be written.",
            primary_action=action(
                "retry_script_finalization",
                "Retry script finalization",
                endpoint="/api/generate_script",
            ),
            discard_action=discard,
            **common,
        )
    if checkpoint_status in {"corrupt", "invalid"}:
        return stage(
            "script",
            "Script",
            "invalid",
            summary="The saved Script checkpoint is invalid.",
            reason=_text(checkpoint.get("explanation")),
            discard_action=discard,
            **common,
        )
    if checkpoint_status not in {"none", "compatible", "finalization_pending"}:
        return stage(
            "script",
            "Script",
            "blocked",
            summary="Saved Script progress cannot be resumed safely.",
            reason=_text(checkpoint.get("explanation")),
            discard_action=discard,
            **common,
        )
    if result.get("script_exists") and result.get("script_status") == "valid":
        return stage(
            "script",
            "Script",
            "complete",
            summary="A complete annotated script is available.",
            primary_action=action(
                "view_script",
                "Review completed script",
                tab="script",
            ),
            **common,
        )
    if source_reason:
        return stage(
            "script",
            "Script",
            "blocked",
            summary="Script generation cannot start.",
            reason=source_reason,
            **common,
        )
    return stage(
        "script",
        "Script",
        "new",
        summary="No Script checkpoint or completed script exists.",
        primary_action=action(
            "start_script",
            "Start script generation",
            endpoint="/api/generate_script",
        ),
        **common,
    )


def build_roster_stage(
    status: Mapping[str, Any],
    source: Mapping[str, Any],
    *,
    checkpoint_at: str | None = None,
) -> dict[str, Any]:
    process_state = _mapping(status.get("process"))
    progress_state = _mapping(status.get("progress"))
    approved = _mapping(status.get("approved"))
    draft = _mapping(status.get("draft"))
    progress_status = _text(progress_state.get("status")) or "missing"
    completed = _integer(progress_state.get("completed_passages"))
    total = _integer(progress_state.get("total_passages"))
    next_passage = progress_state.get("next_passage")
    checkpoint_exists = bool(progress_state.get("exists"))
    discard = (
        action(
            "discard_roster_checkpoint",
            "Discard roster checkpoint",
            endpoint="/api/character_roster/discard-progress",
        )
        if checkpoint_exists and not process_state.get("running")
        else None
    )
    common = {
        "stage_progress": progress(
            completed=completed,
            total=total,
            next_unit=next_passage,
            unit_label="passage",
            last_checkpoint_at=checkpoint_at,
        ),
        "process": process_state,
        "identity": {
            "source_basename": source.get("basename"),
            "source_path": source.get("path"),
            "generation_fingerprint": progress_state.get(
                "generation_fingerprint"
            ),
            "draft_fingerprint": draft.get("fingerprint"),
            "approved_fingerprint": approved.get("fingerprint"),
        },
        "details": {
            "progress_status": progress_status,
            "active_artifact": status.get("active"),
            "draft_status": draft.get("status"),
            "approved_status": approved.get("status"),
        },
    }

    if process_state.get("running"):
        phase = (
            "reconciliation"
            if completed >= total and total > 0
            else "passage discovery"
        )
        return stage(
            "roster",
            "Character roster",
            "running",
            summary=f"Character roster {phase} is running.",
            primary_action=action(
                "cancel_roster",
                "Cancel roster discovery",
                endpoint="/api/character_roster/cancel",
            ),
            **common,
        )
    if approved.get("status") == "approved":
        return stage(
            "roster",
            "Character roster",
            "complete",
            summary="The approved character roster is available.",
            primary_action=action(
                "view_approved_roster",
                "Review approved roster",
                tab="characters",
            ),
            discard_action=discard,
            **common,
        )
    if draft.get("status") == "draft":
        return stage(
            "roster",
            "Character roster",
            "complete",
            summary="A complete roster draft is ready for review.",
            primary_action=action(
                "review_roster",
                "Review roster draft",
                tab="characters",
            ),
            discard_action=discard,
            **common,
        )
    if progress_status == "resumable":
        next_value = _integer(next_passage, completed + 1)
        return stage(
            "roster",
            "Character roster",
            "resumable",
            summary=f"Resume roster from passage {next_value}.",
            primary_action=action(
                "resume_roster",
                f"Resume roster from passage {next_value}",
                endpoint="/api/character_roster/discover",
            ),
            discard_action=discard,
            **common,
        )
    if progress_status == "awaiting_reconciliation":
        return stage(
            "roster",
            "Character roster",
            "finalization_only",
            summary="All passages are complete; global reconciliation remains.",
            primary_action=action(
                "reconcile_roster",
                "Run roster reconciliation",
                endpoint="/api/character_roster/discover",
            ),
            discard_action=discard,
            **common,
        )
    if progress_status == "ready_to_finalize":
        return stage(
            "roster",
            "Character roster",
            "finalization_only",
            summary="Roster reconciliation is complete; write the draft.",
            primary_action=action(
                "finalize_roster",
                "Finalize roster draft",
                endpoint="/api/character_roster/discover",
            ),
            discard_action=discard,
            **common,
        )
    if progress_status in {"corrupt", "invalid"}:
        return stage(
            "roster",
            "Character roster",
            "invalid",
            summary="The saved roster checkpoint is invalid.",
            reason=_text(progress_state.get("error")),
            discard_action=discard,
            **common,
        )
    if progress_status == "incompatible_source":
        return stage(
            "roster",
            "Character roster",
            "blocked",
            summary="Saved roster progress belongs to another source.",
            reason=_text(progress_state.get("error"))
            or "Discard only the roster checkpoint before starting again.",
            discard_action=discard,
            **common,
        )
    source_reason = _source_block_reason(source)
    if source_reason:
        return stage(
            "roster",
            "Character roster",
            "blocked",
            summary="Character roster discovery cannot start.",
            reason=source_reason,
            **common,
        )
    return stage(
        "roster",
        "Character roster",
        "new",
        summary="No roster discovery checkpoint or roster artifact exists.",
        primary_action=action(
            "start_roster",
            "Start character roster",
            endpoint="/api/character_roster/discover",
        ),
        **common,
    )


def build_visual_stage(
    status: Mapping[str, Any],
    source: Mapping[str, Any],
    *,
    checkpoint_at: str | None = None,
) -> dict[str, Any]:
    process_state = _mapping(status.get("process"))
    progress_state = _mapping(status.get("progress"))
    progress_status = _text(progress_state.get("status")) or "none"
    completed = _integer(progress_state.get("completed_passages"))
    total = _integer(progress_state.get("total_passages"))
    next_passage = progress_state.get("next_passage")
    checkpoint_exists = bool(progress_state.get("exists"))
    discard = (
        action(
            "discard_visual_checkpoint",
            "Discard visual checkpoint",
            endpoint="/api/character_visuals/discard-progress",
        )
        if checkpoint_exists and not process_state.get("running")
        else None
    )
    common = {
        "stage_progress": progress(
            completed=completed,
            total=total,
            next_unit=next_passage,
            unit_label="passage",
            last_checkpoint_at=checkpoint_at,
        ),
        "process": process_state,
        "identity": {
            "source_basename": source.get("basename"),
            "source_path": source.get("path"),
            "source_fingerprint": status.get("source_fingerprint"),
            "roster_fingerprint": status.get("roster_fingerprint"),
            "character_ids": _list(progress_state.get("character_ids")),
        },
        "details": {
            "progress_status": progress_status,
            "complete_count": _integer(status.get("complete_count")),
            "absent_count": _integer(status.get("absent_count")),
            "invalid_count": _integer(status.get("invalid_count")),
            "optional": True,
        },
    }

    if process_state.get("running"):
        return stage(
            "visual",
            "Visual dossiers",
            "running",
            summary="Optional visual dossier discovery is running.",
            primary_action=action(
                "cancel_visuals",
                "Cancel visual discovery",
                endpoint="/api/character_visuals/cancel",
            ),
            **common,
        )
    if progress_status == "resumable":
        return stage(
            "visual",
            "Visual dossiers",
            "resumable",
            summary="Saved visual dossier progress can continue.",
            primary_action=action(
                "resume_visuals",
                "Resume visual dossiers",
                endpoint="/api/character_visuals/discover",
                payload={
                    "enabled": True,
                    "entry_ids": _list(progress_state.get("character_ids")),
                },
            ),
            discard_action=discard,
            **common,
        )
    if progress_status in {
        "complete_pending_reconciliation",
        "complete_pending_write",
    }:
        return stage(
            "visual",
            "Visual dossiers",
            "finalization_only",
            summary=(
                "All visual passages are complete; reconciliation remains."
                if progress_status == "complete_pending_reconciliation"
                else "Visual reconciliation is complete; dossier files remain to be written."
            ),
            primary_action=action(
                "finalize_visuals",
                "Finish visual dossiers",
                endpoint="/api/character_visuals/discover",
                payload={
                    "enabled": True,
                    "entry_ids": _list(progress_state.get("character_ids")),
                },
            ),
            discard_action=discard,
            **common,
        )
    if progress_status == "invalid" or _integer(status.get("invalid_count")) > 0:
        return stage(
            "visual",
            "Visual dossiers",
            "invalid",
            summary="Visual dossier progress or output is invalid.",
            reason=_text(progress_state.get("error")),
            discard_action=discard,
            **common,
        )
    if progress_status in {"incompatible_source", "incompatible_roster"}:
        return stage(
            "visual",
            "Visual dossiers",
            "blocked",
            summary="Saved visual progress is incompatible with the current project.",
            reason=_text(progress_state.get("error"))
            or _text(status.get("context_error")),
            discard_action=discard,
            **common,
        )
    complete_count = _integer(status.get("complete_count"))
    absent_count = _integer(status.get("absent_count"))
    if complete_count > 0 and absent_count == 0:
        return stage(
            "visual",
            "Visual dossiers",
            "complete",
            summary="All selected optional visual dossiers are available.",
            primary_action=action(
                "view_visuals",
                "Review visual dossiers",
                tab="characters",
            ),
            discard_action=discard,
            **common,
        )
    if status.get("approved_roster_available") is not True:
        return stage(
            "visual",
            "Visual dossiers",
            "unavailable",
            summary="Visual dossiers require an approved character roster.",
            reason=_text(status.get("context_error")),
            discard_action=discard,
            **common,
        )
    return stage(
        "visual",
        "Visual dossiers",
        "new",
        summary="Optional visual dossier discovery has not started.",
        primary_action=action(
            "start_visuals",
            "Start visual dossiers",
            tab="characters",
        ),
        **common,
    )


def build_persona_stage(
    *,
    process: Mapping[str, Any],
    configured_speakers: Any,
    total_speakers: Any,
    script_available: bool,
    last_run_at: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    configured = _integer(configured_speakers)
    total = _integer(total_speakers)
    stage_progress = progress(
        completed=configured,
        total=total,
        next_unit=(configured + 1 if configured < total else None),
        unit_label="speaker",
        last_checkpoint_at=last_run_at,
    )
    common = {
        "stage_progress": stage_progress,
        "process": process,
        "details": {
            "durably_resumable": False,
            "configured_speakers": configured,
            "total_speakers": total,
            "error": error,
        },
    }
    if _mapping(process).get("running"):
        return stage(
            "persona",
            "Voice personas",
            "running",
            summary="Voice persona generation is running.",
            primary_action=action(
                "cancel_persona",
                "Cancel persona generation",
                endpoint="/api/cancel_persona",
            ),
            **common,
        )
    if error:
        return stage(
            "persona",
            "Voice personas",
            "invalid",
            summary="The saved Script or voice configuration is invalid.",
            reason=error,
            **common,
        )
    if not script_available or total == 0:
        return stage(
            "persona",
            "Voice personas",
            "unavailable",
            summary="Voice personas require a completed annotated script.",
            **common,
        )
    if configured >= total:
        return stage(
            "persona",
            "Voice personas",
            "complete",
            summary="Every script speaker has a saved voice configuration.",
            primary_action=action(
                "review_voices",
                "Review voices",
                tab="voices",
            ),
            **common,
        )
    prior_logs = capped_logs(process)["line_count"] > 0
    if configured > 0 or prior_logs:
        return stage(
            "persona",
            "Voice personas",
            "restart_required",
            summary="Persona generation is incomplete and cannot resume mid-run.",
            reason="Completed voice configurations are preserved; the remaining generation pass must restart.",
            primary_action=action(
                "restart_persona",
                "Restart persona generation",
                endpoint="/api/generate_personas",
            ),
            **common,
        )
    return stage(
        "persona",
        "Voice personas",
        "new",
        summary="Voice persona generation has not started.",
        primary_action=action(
            "start_persona",
            "Start persona generation",
            endpoint="/api/generate_personas",
        ),
        **common,
    )


def build_dataset_stage(
    *,
    projects: Iterable[Mapping[str, Any]],
    process: Mapping[str, Any],
    selected_project: str | None = None,
    last_checkpoint_at: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    safe_projects = [dict(item) for item in projects if isinstance(item, Mapping)]
    selected = None
    if selected_project:
        selected = next(
            (item for item in safe_projects if item.get("name") == selected_project),
            None,
        )
    if selected is None and safe_projects:
        selected = safe_projects[0]
    name = _text((selected or {}).get("name"))
    total = _integer((selected or {}).get("sample_count"))
    done = _integer((selected or {}).get("done_count"))
    next_sample = done + 1 if total and done < total else None
    common = {
        "stage_progress": progress(
            completed=done,
            total=total,
            next_unit=next_sample,
            unit_label="sample",
            last_checkpoint_at=last_checkpoint_at,
        ),
        "process": process,
        "identity": {"project_name": name},
        "details": {
            "project_count": len(safe_projects),
            "error": error,
            "projects": [
                {
                    "name": _text(item.get("name")),
                    "sample_count": _integer(item.get("sample_count")),
                    "done_count": _integer(item.get("done_count")),
                }
                for item in safe_projects
            ],
        },
    }
    if _mapping(process).get("running"):
        return stage(
            "dataset_builder",
            "Dataset builder",
            "running",
            summary=(
                f"Dataset generation is running for {name}."
                if name
                else "Dataset generation is running."
            ),
            primary_action=action(
                "cancel_dataset",
                "Cancel dataset generation",
                endpoint="/api/dataset_builder/cancel",
            ),
            **common,
        )
    if error:
        return stage(
            "dataset_builder",
            "Dataset builder",
            "invalid",
            summary="A persisted Dataset builder project is invalid.",
            reason=error,
            **common,
        )
    if not safe_projects:
        return stage(
            "dataset_builder",
            "Dataset builder",
            "new",
            summary="No Dataset builder project exists.",
            primary_action=action(
                "create_dataset",
                "Create dataset project",
                tab="dataset-builder",
            ),
            **common,
        )
    if total > 0 and done >= total:
        return stage(
            "dataset_builder",
            "Dataset builder",
            "complete",
            summary=f"Dataset project {name} has generated all samples.",
            primary_action=action(
                "review_dataset",
                f"Review dataset {name}",
                tab="dataset-builder",
            ),
            **common,
        )
    if total > 0 and done > 0:
        return stage(
            "dataset_builder",
            "Dataset builder",
            "resumable",
            summary=f"Continue dataset {name} from sample {next_sample}.",
            primary_action=action(
                "resume_dataset",
                f"Continue dataset {name} from sample {next_sample}",
                tab="dataset-builder",
            ),
            **common,
        )
    return stage(
        "dataset_builder",
        "Dataset builder",
        "new",
        summary=f"Dataset project {name} is ready for sample generation.",
        primary_action=action(
            "open_dataset",
            f"Open dataset {name}",
            tab="dataset-builder",
        ),
        **common,
    )


def build_audio_stage(
    *,
    chunks: Iterable[Mapping[str, Any]],
    process: Mapping[str, Any],
    last_checkpoint_at: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    safe_chunks = [dict(item) for item in chunks if isinstance(item, Mapping)]
    total = len(safe_chunks)
    completed = sum(
        1
        for item in safe_chunks
        if item.get("status") == "done" and item.get("audio_path")
    )
    error_count = sum(1 for item in safe_chunks if item.get("status") == "error")
    next_index = next(
        (
            index + 1
            for index, item in enumerate(safe_chunks)
            if not (
                item.get("status") == "done"
                and item.get("audio_path")
            )
        ),
        None,
    )
    common = {
        "stage_progress": progress(
            completed=completed,
            total=total,
            next_unit=next_index,
            unit_label="chunk",
            last_checkpoint_at=last_checkpoint_at,
        ),
        "process": process,
        "details": {"error_count": error_count, "error": error},
    }
    if _mapping(process).get("running"):
        return stage(
            "audio",
            "Audio generation",
            "running",
            summary="Audio generation is running.",
            primary_action=action(
                "cancel_audio",
                "Cancel audio generation",
                endpoint="/api/cancel_audio",
            ),
            **common,
        )
    if error:
        return stage(
            "audio",
            "Audio generation",
            "invalid",
            summary="The persisted chunk state is invalid.",
            reason=error,
            **common,
        )
    if total == 0:
        return stage(
            "audio",
            "Audio generation",
            "unavailable",
            summary="Audio generation requires built script chunks.",
            **common,
        )
    if completed >= total:
        return stage(
            "audio",
            "Audio generation",
            "complete",
            summary="Every audio chunk is generated.",
            primary_action=action(
                "review_audio",
                "Review generated audio",
                tab="audio",
            ),
            **common,
        )
    if completed > 0 or error_count > 0:
        return stage(
            "audio",
            "Audio generation",
            "resumable",
            summary=f"Resume audio from chunk {next_index}.",
            primary_action=action(
                "resume_audio",
                f"Resume audio from chunk {next_index}",
                endpoint="/api/recovery/action",
            ),
            **common,
        )
    return stage(
        "audio",
        "Audio generation",
        "new",
        summary="No audio chunks have been generated.",
        primary_action=action(
            "start_audio",
            "Start audio generation",
            endpoint="/api/recovery/action",
        ),
        **common,
    )


def build_training_stage(
    status: Mapping[str, Any],
    *,
    process: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    process_state = _mapping(process)
    jobs = [dict(item) for item in _list(status.get("jobs")) if isinstance(item, Mapping)]
    running_job = next(
        (item for item in jobs if item.get("status") in {"queued", "running"}),
        None,
    )
    latest = jobs[0] if jobs else None
    last_at = _text((latest or {}).get("finished_at_utc")) or _text(
        (latest or {}).get("started_at_utc")
    )
    common = {
        "stage_progress": progress(
            completed=sum(1 for item in jobs if item.get("status") == "completed"),
            total=len(jobs),
            next_unit=None,
            unit_label="job",
            last_checkpoint_at=last_at,
        ),
        "process": process_state,
        "identity": {
            "latest_job_id": (latest or {}).get("job_id"),
            "latest_action": (latest or {}).get("action"),
        },
        "details": {
            "experimental": bool(status.get("experimental")),
            "production_assignment_supported": bool(
                status.get("production_assignment_supported")
            ),
            "environment_exists": bool(status.get("environment_exists")),
            "job_count": len(jobs),
            "latest_job_status": (latest or {}).get("status"),
        },
    }
    if running_job or process_state.get("running"):
        action_name = _text((running_job or {}).get("action")) or "training"
        return stage(
            "experimental_training",
            "Experimental training",
            "running",
            summary=f"Experimental {action_name} job is running.",
            **common,
        )
    if _text(status.get("error")):
        return stage(
            "experimental_training",
            "Experimental training",
            "invalid",
            summary="Experimental training state could not be inspected.",
            reason=_text(status.get("error")),
            **common,
        )
    if not status.get("environment_exists"):
        return stage(
            "experimental_training",
            "Experimental training",
            "unavailable",
            summary="The isolated experimental training environment is not installed.",
            primary_action=action(
                "setup_training",
                "Set up training sidecar",
                tab="training",
            ),
            **common,
        )
    if latest and latest.get("status") in {"failed", "cancelled"}:
        return stage(
            "experimental_training",
            "Experimental training",
            "restart_required",
            summary="The latest experimental training job did not complete.",
            reason=_text(latest.get("error")),
            primary_action=action(
                "restart_training",
                "Open failed training job",
                tab="training",
            ),
            **common,
        )
    if latest and latest.get("status") == "completed":
        return stage(
            "experimental_training",
            "Experimental training",
            "complete",
            summary="The latest experimental training job completed.",
            primary_action=action(
                "review_training",
                "Review training results",
                tab="training",
            ),
            **common,
        )
    return stage(
        "experimental_training",
        "Experimental training",
        "new",
        summary="The experimental sidecar is ready; no job is active.",
        primary_action=action(
            "start_training",
            "Open experimental training",
            tab="training",
        ),
        **common,
    )


def build_recovery_summary(
    *,
    source: Mapping[str, Any],
    script_status: Mapping[str, Any],
    roster_status: Mapping[str, Any],
    visual_status: Mapping[str, Any],
    persona: Mapping[str, Any],
    dataset: Mapping[str, Any],
    audio: Mapping[str, Any],
    training_status: Mapping[str, Any],
    training_process: Mapping[str, Any] | None = None,
    timestamps: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    timestamps = _mapping(timestamps)
    stages = [
        build_script_stage(
            script_status,
            source,
            checkpoint_at=_text(timestamps.get("script")),
        ),
        build_roster_stage(
            roster_status,
            source,
            checkpoint_at=_text(timestamps.get("roster")),
        ),
        build_visual_stage(
            visual_status,
            source,
            checkpoint_at=_text(timestamps.get("visual")),
        ),
        build_persona_stage(
            process=_mapping(persona.get("process")),
            configured_speakers=persona.get("configured_speakers"),
            total_speakers=persona.get("total_speakers"),
            script_available=bool(persona.get("script_available")),
            last_run_at=_text(timestamps.get("persona")),
            error=_text(persona.get("error")),
        ),
        build_dataset_stage(
            projects=_list(dataset.get("projects")),
            process=_mapping(dataset.get("process")),
            selected_project=_text(dataset.get("selected_project")),
            last_checkpoint_at=_text(timestamps.get("dataset_builder")),
            error=_text(dataset.get("error")),
        ),
        build_audio_stage(
            chunks=_list(audio.get("chunks")),
            process=_mapping(audio.get("process")),
            last_checkpoint_at=_text(timestamps.get("audio")),
            error=_text(audio.get("error")),
        ),
        build_training_stage(
            training_status,
            process=training_process,
        ),
    ]
    states = {item["state"] for item in stages}
    return {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "model_free": True,
        "file_pure": True,
        "source": dict(source),
        "stages": stages,
        "summary": {
            "running": sum(1 for item in stages if item["state"] == "running"),
            "actionable": sum(
                1
                for item in stages
                if item["state"]
                in {
                    "new",
                    "resumable",
                    "finalization_only",
                    "restart_required",
                }
            ),
            "blocked": sum(
                1
                for item in stages
                if item["state"] in {"blocked", "invalid", "unavailable"}
            ),
            "complete": sum(1 for item in stages if item["state"] == "complete"),
            "states_present": sorted(states),
        },
    }
