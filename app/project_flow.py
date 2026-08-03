from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from audio_artifacts import (
    AudioArtifactError,
    audio_binding_fingerprint,
    confined_audio_path,
    sha256_file,
)
from audio_generation_policy import synthesis_config_with_generation_seed
from generation_state import fingerprint_value
from script_voice_mapping import (
    build_script_voice_index,
    resolve_script_voice_name,
)
from voice_aliases import VoiceAliasError, resolve_voice_alias


PROJECT_FLOW_SCHEMA_VERSION = 1
PROJECT_FLOW_STAGE_KEYS = ("script", "cast", "produce", "export")
PROJECT_FLOW_STAGE_STATES = frozenset(
    {
        "not_started",
        "ready",
        "running",
        "resumable",
        "review_required",
        "blocked",
        "complete",
        "stale",
        "failed",
    }
)


class ProjectFlowError(ValueError):
    pass


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _read_json(path: str | Path) -> tuple[Any | None, str | None]:
    target = Path(path)
    if not target.exists():
        return None, None
    try:
        return json.loads(target.read_text(encoding="utf-8")), None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return None, str(exc)


def _stable_identifier(prefix: str, *parts: Any) -> str:
    payload = "\x1f".join(str(part or "") for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()[:20]}"


def _action(
    action_id: str,
    label: str,
    *,
    native_destination: str,
    target_id: str | None = None,
    endpoint: str | None = None,
    destructive: bool = False,
) -> dict[str, Any]:
    return {
        "id": action_id,
        "label": label,
        "native_destination": native_destination,
        "target_id": target_id,
        "endpoint": endpoint,
        "destructive": bool(destructive),
    }


def _blocker(
    code: str,
    *,
    stage: str,
    title: str,
    explanation: str,
    native_destination: str,
    target_id: str | None = None,
    severity: str = "error",
    blocking: bool = True,
    safe_action_id: str | None = None,
    dependency_fingerprint: str | None = None,
) -> dict[str, Any]:
    blocker_id = _stable_identifier(
        "blocker",
        code,
        stage,
        native_destination,
        target_id,
        dependency_fingerprint,
    )
    return {
        "id": blocker_id,
        "code": code,
        "stage": stage,
        "title": title,
        "explanation": explanation,
        "severity": severity,
        "blocking": bool(blocking),
        "native_destination": native_destination,
        "target_id": target_id,
        "safe_action_id": safe_action_id,
        "dependency_fingerprint": dependency_fingerprint,
        "technical_detail_available": True,
    }


def _deduplicate_blockers(blockers: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for blocker in blockers:
        identifier = str(blocker.get("id") or "")
        if identifier in seen:
            continue
        seen.add(identifier)
        result.append(blocker)
    return result


def _stage(
    key: str,
    *,
    state: str,
    summary: str,
    blockers: Iterable[dict[str, Any]] = (),
    safe_next_action: dict[str, Any] | None = None,
    fingerprints: Mapping[str, Any] | None = None,
    operation: Mapping[str, Any] | None = None,
    metrics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if key not in PROJECT_FLOW_STAGE_KEYS:
        raise ProjectFlowError(f"Unsupported project-flow stage: {key}")
    if state not in PROJECT_FLOW_STAGE_STATES:
        raise ProjectFlowError(f"Unsupported project-flow state: {state}")
    blocker_list = _deduplicate_blockers(blockers)
    return {
        "key": key,
        "state": state,
        "summary": summary,
        "blocker_count": sum(bool(item.get("blocking")) for item in blocker_list),
        "blockers": blocker_list,
        "safe_next_action": safe_next_action,
        "fingerprints": dict(fingerprints or {}),
        "operation": dict(operation or {}),
        "metrics": dict(metrics or {}),
    }


def _gate_blockers(
    *,
    stage: str,
    evidence: Mapping[str, Any],
    gates: Iterable[tuple[str, str, str, str, str]],
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for field, code, title, explanation, destination in gates:
        if evidence.get(field) is False:
            blockers.append(
                _blocker(
                    code,
                    stage=stage,
                    title=title,
                    explanation=explanation,
                    native_destination=destination,
                    target_id=_text(evidence.get(f"{field}_target_id")),
                    dependency_fingerprint=_text(
                        evidence.get(f"{field}_dependency_fingerprint")
                    ),
                )
            )
    return blockers


def _script_stage(
    evidence: Mapping[str, Any],
    *,
    compatibility_blockers: list[dict[str, Any]],
) -> dict[str, Any]:
    process = _mapping(evidence.get("process"))
    fingerprints = _mapping(evidence.get("fingerprints"))
    blockers = list(compatibility_blockers)
    source_available = evidence.get("source_available") is True

    if process.get("running"):
        return _stage(
            "script",
            state="running",
            summary="Script generation is running.",
            fingerprints=fingerprints,
            operation=process,
        )
    if evidence.get("failed") is True:
        blockers.append(
            _blocker(
                "script_generation_failed",
                stage="script",
                title="Script generation failed",
                explanation=_text(evidence.get("failure_reason"))
                or "Script generation stopped before producing a valid result.",
                native_destination="script",
                target_id="script:generation",
                safe_action_id="retry_script_generation",
            )
        )
        return _stage(
            "script",
            state="failed",
            summary="Script generation failed.",
            blockers=blockers,
            safe_next_action=_action(
                "retry_script_generation",
                "Retry Script generation",
                native_destination="script",
                target_id="script:generation",
                endpoint="/api/generate_script",
            ),
            fingerprints=fingerprints,
            operation=process,
        )
    if evidence.get("resumable") is True:
        return _stage(
            "script",
            state="resumable",
            summary="Saved Script progress can be resumed safely.",
            blockers=blockers,
            safe_next_action=_action(
                "resume_script_generation",
                "Resume generation",
                native_destination="script",
                target_id="script:generation",
                endpoint="/api/generate_script",
            ),
            fingerprints=fingerprints,
            operation=process,
        )
    if not source_available:
        blockers.append(
            _blocker(
                "script_source_unavailable",
                stage="script",
                title="Source is unavailable",
                explanation=_text(evidence.get("source_error"))
                or "Select a readable source file before generating a Script.",
                native_destination="projects",
                target_id="project:source",
                safe_action_id="select_project_source",
            )
        )
        return _stage(
            "script",
            state="blocked",
            summary="Script cannot start until the project has a readable source.",
            blockers=blockers,
            safe_next_action=_action(
                "select_project_source",
                "Select source",
                native_destination="projects",
                target_id="project:source",
            ),
            fingerprints=fingerprints,
        )

    artifact_exists = evidence.get("artifact_exists") is True
    if not artifact_exists:
        if blockers:
            return _stage(
                "script",
                state="blocked",
                summary="Script generation is blocked by project compatibility.",
                blockers=blockers,
                fingerprints=fingerprints,
            )
        if evidence.get("import_candidate_exists") is True:
            return _stage(
                "script",
                state="review_required",
                summary="An imported Alexandria Script candidate is ready for validation and acceptance.",
                safe_next_action=_action(
                    "review_imported_script",
                    "Review imported Script",
                    native_destination="script",
                    target_id="script:import-review",
                ),
                fingerprints=fingerprints,
            )
        return _stage(
            "script",
            state="not_started",
            summary="No authoritative Script exists yet.",
            safe_next_action=_action(
                "generate_script",
                "Generate Script",
                native_destination="script",
                target_id="script:generation",
                endpoint="/api/generate_script",
            ),
            fingerprints=fingerprints,
        )

    blockers.extend(
        _gate_blockers(
            stage="script",
            evidence=evidence,
            gates=(
                (
                    "structure_valid",
                    "script_structure_invalid",
                    "Script structure is invalid",
                    "The authoritative Script does not satisfy the required entry structure.",
                    "script",
                ),
                (
                    "attribution_valid",
                    "script_attribution_invalid",
                    "Speaker attribution is invalid",
                    "At least one Script entry has a blocking speaker-attribution error.",
                    "script",
                ),
                (
                    "fidelity_valid",
                    "script_source_fidelity_failed",
                    "Source fidelity failed",
                    "The Script cannot account for the selected source under the exact-fidelity contract.",
                    "script",
                ),
                (
                    "artifact_current",
                    "script_artifact_stale",
                    "Script is stale",
                    "The authoritative Script was produced from different source or generation dependencies.",
                    "script",
                ),
                (
                    "provenance_recorded",
                    "script_provenance_missing",
                    "Script provenance is incomplete",
                    "The authoritative Script does not record sufficient generation or import provenance.",
                    "script",
                ),
                (
                    "finalization_complete",
                    "script_finalization_incomplete",
                    "Script finalization is incomplete",
                    "Generation completed its chunks but did not finish writing the authoritative artifacts.",
                    "script",
                ),
            ),
        )
    )
    if blockers:
        stale_codes = {"script_artifact_stale"}
        state = (
            "stale"
            if any(item.get("code") in stale_codes for item in blockers)
            else "blocked"
        )
        return _stage(
            "script",
            state=state,
            summary="Script requires correction before the project can continue.",
            blockers=blockers,
            safe_next_action=_action(
                "review_script",
                "Review Script",
                native_destination="script",
                target_id="script:review",
            ),
            fingerprints=fingerprints,
        )

    if evidence.get("review_required") is True or evidence.get("accepted") is not True:
        return _stage(
            "script",
            state="review_required",
            summary="A valid Script is available but still requires review or acceptance.",
            blockers=(),
            safe_next_action=_action(
                "review_script",
                "Review Script",
                native_destination="script",
                target_id="script:review",
            ),
            fingerprints=fingerprints,
        )

    return _stage(
        "script",
        state="complete",
        summary="The authoritative Script is current, validated, and accepted.",
        safe_next_action=_action(
            "open_cast",
            "Open Cast",
            native_destination="cast",
        ),
        fingerprints=fingerprints,
    )


def _cast_stage(
    evidence: Mapping[str, Any],
    *,
    script_stage: Mapping[str, Any],
    compatibility_blockers: list[dict[str, Any]],
) -> dict[str, Any]:
    process = _mapping(evidence.get("process"))
    fingerprints = _mapping(evidence.get("fingerprints"))
    blockers = list(compatibility_blockers)

    if script_stage.get("state") != "complete":
        blockers.append(
            _blocker(
                "cast_script_dependency_incomplete",
                stage="cast",
                title="Script is not complete",
                explanation="Cast cannot become authoritative until the current Script is validated and accepted.",
                native_destination="script",
                target_id="script:review",
                dependency_fingerprint=_text(
                    _mapping(script_stage.get("fingerprints")).get("script")
                ),
            )
        )
        return _stage(
            "cast",
            state="blocked",
            summary="Cast is waiting for the authoritative Script.",
            blockers=blockers,
            safe_next_action=_action(
                "open_script",
                "Open Script",
                native_destination="script",
            ),
            fingerprints=fingerprints,
            operation=process,
        )
    if process.get("running"):
        return _stage(
            "cast",
            state="running",
            summary="Character discovery or reconciliation is running.",
            fingerprints=fingerprints,
            operation=process,
        )
    if evidence.get("failed") is True:
        blockers.append(
            _blocker(
                "cast_discovery_failed",
                stage="cast",
                title="Character discovery failed",
                explanation=_text(evidence.get("failure_reason"))
                or "Character discovery or reconciliation failed.",
                native_destination="cast",
                target_id="cast:discovery",
                safe_action_id="retry_cast_discovery",
            )
        )
        return _stage(
            "cast",
            state="failed",
            summary="Cast discovery failed.",
            blockers=blockers,
            safe_next_action=_action(
                "retry_cast_discovery",
                "Retry discovery",
                native_destination="cast",
                target_id="cast:discovery",
                endpoint="/api/character_roster/discover",
            ),
            fingerprints=fingerprints,
            operation=process,
        )
    if evidence.get("resumable") is True:
        return _stage(
            "cast",
            state="resumable",
            summary="Saved character discovery can be resumed safely.",
            blockers=blockers,
            safe_next_action=_action(
                "resume_cast_discovery",
                "Resume discovery",
                native_destination="cast",
                target_id="cast:discovery",
                endpoint="/api/character_roster/discover",
            ),
            fingerprints=fingerprints,
            operation=process,
        )
    if evidence.get("roster_exists") is not True:
        if blockers:
            return _stage(
                "cast",
                state="blocked",
                summary="Cast cannot start because the project is incompatible.",
                blockers=blockers,
                fingerprints=fingerprints,
            )
        return _stage(
            "cast",
            state="ready",
            summary="The Script is ready for automatic character discovery.",
            safe_next_action=_action(
                "discover_cast",
                "Discover characters",
                native_destination="cast",
                target_id="cast:discovery",
                endpoint="/api/character_roster/discover",
            ),
            fingerprints=fingerprints,
        )
    if evidence.get("review_required") is True and evidence.get("roster_approved") is not True:
        return _stage(
            "cast",
            state="review_required",
            summary="A Cast draft is ready for issue-focused review.",
            safe_next_action=_action(
                "review_cast",
                "Review Cast",
                native_destination="cast",
                target_id="cast:review",
            ),
            fingerprints=fingerprints,
        )

    issue_groups = (
        (
            "unresolved_identity_ids",
            "cast_identity_unresolved",
            "Character identity is unresolved",
            "Resolve or explicitly preserve this speaking identity before Cast can complete.",
        ),
        (
            "ambiguous_mapping_ids",
            "cast_script_label_ambiguous",
            "Script label mapping is ambiguous",
            "The character does not map to exactly one Script voice label.",
        ),
        (
            "missing_voice_ids",
            "cast_voice_missing",
            "Production voice is missing",
            "Assign a valid production voice to this required speaking identity.",
        ),
        (
            "invalid_voice_ids",
            "cast_voice_invalid",
            "Production voice is invalid",
            "The selected production voice configuration is incomplete or incompatible.",
        ),
        (
            "invalid_clone_ids",
            "cast_clone_reference_invalid",
            "Clone reference is invalid",
            "The selected clone requires current reference audio and an exact non-empty transcript.",
        ),
        (
            "controlled_clone_approval_missing_ids",
            "cast_controlled_clone_approval_invalid",
            "Controlled-clone approval is not current",
            "Generate, listen to, and confirm a preview bound to the current clone inputs.",
        ),
        (
            "invalid_adapter_ids",
            "cast_adapter_invalid",
            "Adapter or trained voice is invalid",
            "The selected adapter is missing, incompatible, unreviewed, or not approved for production assignment.",
        ),
        (
            "stale_voice_ids",
            "cast_voice_stale",
            "Voice configuration is stale",
            "The authoritative Voice configuration no longer matches its dependencies.",
        ),
    )
    for field, code, title, explanation in issue_groups:
        for target_id in _list(evidence.get(field)):
            blockers.append(
                _blocker(
                    code,
                    stage="cast",
                    title=title,
                    explanation=explanation,
                    native_destination="cast",
                    target_id=str(target_id),
                    dependency_fingerprint=_text(
                        fingerprints.get("script")
                    ),
                )
            )

    if evidence.get("roster_approved") is not True:
        blockers.append(
            _blocker(
                "cast_roster_not_approved",
                stage="cast",
                title="Cast roster is not approved",
                explanation="Approve the reviewed roster before production voice assignments become authoritative.",
                native_destination="cast",
                target_id="cast:review",
                safe_action_id="review_cast",
            )
        )
    if evidence.get("roster_current") is False:
        blockers.append(
            _blocker(
                "cast_roster_stale",
                stage="cast",
                title="Cast roster is stale",
                explanation="The approved roster belongs to a different Script or source dependency.",
                native_destination="cast",
                target_id="cast:review",
                dependency_fingerprint=_text(fingerprints.get("roster")),
            )
        )

    if blockers:
        state = (
            "stale"
            if any(item.get("code") in {"cast_roster_stale", "cast_voice_stale"} for item in blockers)
            else "blocked"
        )
        return _stage(
            "cast",
            state=state,
            summary="Cast has identity or Voice blockers.",
            blockers=blockers,
            safe_next_action=_action(
                "show_cast_blockers",
                "Show Cast blockers",
                native_destination="cast",
                target_id="cast:blockers",
            ),
            fingerprints=fingerprints,
            metrics={
                "required_speaking_characters": int(
                    evidence.get("required_speaking_characters") or 0
                ),
                "valid_production_voices": int(
                    evidence.get("valid_production_voices") or 0
                ),
            },
        )

    return _stage(
        "cast",
        state="complete",
        summary="Every required speaking identity has one current valid production voice.",
        safe_next_action=_action(
            "open_produce",
            "Open Produce",
            native_destination="produce",
        ),
        fingerprints=fingerprints,
        metrics={
            "required_speaking_characters": int(
                evidence.get("required_speaking_characters") or 0
            ),
            "valid_production_voices": int(
                evidence.get("valid_production_voices") or 0
            ),
        },
    )


def _produce_stage(
    evidence: Mapping[str, Any],
    *,
    cast_stage: Mapping[str, Any],
    compatibility_blockers: list[dict[str, Any]],
) -> dict[str, Any]:
    process = _mapping(evidence.get("process"))
    fingerprints = _mapping(evidence.get("fingerprints"))
    blockers = list(compatibility_blockers)

    if cast_stage.get("state") != "complete":
        blockers.append(
            _blocker(
                "produce_cast_dependency_incomplete",
                stage="produce",
                title="Cast is not complete",
                explanation="Produce requires current valid Voice assignments for every required speaking identity.",
                native_destination="cast",
                target_id="cast:blockers",
                dependency_fingerprint=_text(
                    _mapping(cast_stage.get("fingerprints")).get("voice_config")
                ),
            )
        )
        return _stage(
            "produce",
            state="blocked",
            summary="Produce is waiting for Cast.",
            blockers=blockers,
            safe_next_action=_action(
                "open_cast",
                "Open Cast",
                native_destination="cast",
            ),
            fingerprints=fingerprints,
            operation=process,
        )
    if process.get("running"):
        return _stage(
            "produce",
            state="running",
            summary="Audio generation is running.",
            fingerprints=fingerprints,
            operation=process,
        )
    if evidence.get("resumable") is True:
        return _stage(
            "produce",
            state="resumable",
            summary="Interrupted audio generation can be resumed.",
            safe_next_action=_action(
                "resume_audio_generation",
                "Resume audio generation",
                native_destination="produce",
                target_id="produce:generation",
                endpoint="/api/generate_batch",
            ),
            fingerprints=fingerprints,
            operation=process,
        )

    required_chunks = int(evidence.get("required_chunks") or 0)
    if required_chunks <= 0:
        blockers.append(
            _blocker(
                "produce_chunks_missing",
                stage="produce",
                title="Production chunks are missing",
                explanation="Create current production chunks from the authoritative Script before generating audio.",
                native_destination="produce",
                target_id="produce:chunks",
            )
        )
        return _stage(
            "produce",
            state="blocked",
            summary="No production chunks are available.",
            blockers=blockers,
            fingerprints=fingerprints,
        )

    item_groups = (
        (
            "failed_chunk_ids",
            "produce_audio_failed",
            "Audio generation failed",
            "Regenerate this chunk after correcting the reported failure.",
            "failed",
        ),
        (
            "hash_invalid_chunk_ids",
            "produce_audio_hash_invalid",
            "Audio hash is invalid",
            "The current audio bytes do not match the recorded artifact hash.",
            "failed",
        ),
        (
            "stale_chunk_ids",
            "produce_audio_stale",
            "Audio is stale",
            "The audio does not match the current text, instruction, speaker, Voice, or synthesis settings.",
            "stale",
        ),
        (
            "missing_chunk_ids",
            "produce_audio_missing",
            "Audio is missing",
            "Generate audio for this required chunk.",
            "ready",
        ),
        (
            "review_chunk_ids",
            "produce_review_required",
            "Chunk review is required",
            "Clear the chunk review issue after inspecting the current audio and Script context.",
            "review_required",
        ),
        (
            "listening_chunk_ids",
            "produce_listening_required",
            "Listening review is incomplete",
            "Listen to and explicitly approve the required current audio.",
            "review_required",
        ),
    )
    resulting_states: set[str] = set()
    for field, code, title, explanation, state in item_groups:
        for target_id in _list(evidence.get(field)):
            blockers.append(
                _blocker(
                    code,
                    stage="produce",
                    title=title,
                    explanation=explanation,
                    native_destination="produce",
                    target_id=str(target_id),
                    dependency_fingerprint=_text(
                        fingerprints.get("voice_config")
                    ),
                )
            )
            resulting_states.add(state)

    if blockers:
        if "failed" in resulting_states:
            state = "failed"
            summary = "Some required audio failed validation or generation."
        elif "review_required" in resulting_states:
            state = "review_required"
            summary = "Current audio requires review or listening approval."
        elif "stale" in resulting_states:
            state = "stale"
            summary = "Some required audio is stale."
        else:
            state = "ready"
            summary = "Required audio is ready to generate."
        return _stage(
            "produce",
            state=state,
            summary=summary,
            blockers=blockers,
            safe_next_action=_action(
                "generate_missing_stale_audio",
                "Generate missing and stale audio",
                native_destination="produce",
                target_id="produce:generation",
                endpoint="/api/generate_batch",
            ),
            fingerprints=fingerprints,
            metrics={
                "required_chunks": required_chunks,
                "current_chunks": int(evidence.get("current_chunks") or 0),
            },
        )

    if int(evidence.get("current_chunks") or 0) != required_chunks:
        blockers.append(
            _blocker(
                "produce_audio_incomplete",
                stage="produce",
                title="Audio is incomplete",
                explanation="At least one required chunk is not represented by current validated audio.",
                native_destination="produce",
                target_id="produce:chunks",
            )
        )
        return _stage(
            "produce",
            state="blocked",
            summary="Production audio is incomplete.",
            blockers=blockers,
            fingerprints=fingerprints,
        )

    return _stage(
        "produce",
        state="complete",
        summary="Every required chunk has current validated audio and cleared review gates.",
        safe_next_action=_action(
            "open_export",
            "Open Export",
            native_destination="export",
        ),
        fingerprints=fingerprints,
        metrics={
            "required_chunks": required_chunks,
            "current_chunks": required_chunks,
        },
    )


def _export_stage(
    evidence: Mapping[str, Any],
    *,
    produce_stage: Mapping[str, Any],
    compatibility_blockers: list[dict[str, Any]],
) -> dict[str, Any]:
    process = _mapping(evidence.get("process"))
    fingerprints = _mapping(evidence.get("fingerprints"))
    blockers = list(compatibility_blockers)

    if produce_stage.get("state") != "complete":
        blockers.append(
            _blocker(
                "export_produce_dependency_incomplete",
                stage="export",
                title="Produce is not complete",
                explanation="Export cannot build from missing, stale, failed, hash-invalid, or unreviewed audio.",
                native_destination="produce",
                target_id="produce:blockers",
                dependency_fingerprint=_text(
                    _mapping(produce_stage.get("fingerprints")).get("chunks")
                ),
            )
        )
        return _stage(
            "export",
            state="blocked",
            summary="Export is waiting for current validated production audio.",
            blockers=blockers,
            safe_next_action=_action(
                "open_produce",
                "Open Produce",
                native_destination="produce",
            ),
            fingerprints=fingerprints,
            operation=process,
        )
    if process.get("running"):
        return _stage(
            "export",
            state="running",
            summary="Audiobook build is running.",
            fingerprints=fingerprints,
            operation=process,
        )
    if evidence.get("failed") is True:
        blockers.append(
            _blocker(
                "export_build_failed",
                stage="export",
                title="Audiobook build failed",
                explanation=_text(evidence.get("failure_reason"))
                or "The previous successful output remains preserved.",
                native_destination="export",
                target_id="export:build",
                safe_action_id="build_audiobook",
            )
        )
        return _stage(
            "export",
            state="failed",
            summary="The audiobook build failed.",
            blockers=blockers,
            safe_next_action=_action(
                "build_audiobook",
                "Retry build",
                native_destination="export",
                target_id="export:build",
            ),
            fingerprints=fingerprints,
            operation=process,
        )

    for field in _list(evidence.get("missing_metadata_fields")):
        blockers.append(
            _blocker(
                "export_metadata_incomplete",
                stage="export",
                title="Required metadata is incomplete",
                explanation=f"Complete the required metadata field: {field}.",
                native_destination="export",
                target_id=f"metadata:{field}",
            )
        )
    for chapter_id in _list(evidence.get("invalid_chapter_ids")):
        blockers.append(
            _blocker(
                "export_chapter_metadata_incomplete",
                stage="export",
                title="Chapter metadata is incomplete",
                explanation="Complete the chapter name, order, and source range.",
                native_destination="export",
                target_id=str(chapter_id),
            )
        )
    for format_name in _list(evidence.get("unavailable_formats")):
        blockers.append(
            _blocker(
                "export_format_unavailable",
                stage="export",
                title="Selected format is unavailable",
                explanation=f"The current backend cannot build and validate {format_name}.",
                native_destination="export",
                target_id=f"format:{format_name}",
            )
        )

    output_exists = evidence.get("output_exists") is True
    output_current = evidence.get("output_current") is True
    output_valid = evidence.get("output_valid") is True
    if output_exists and not output_current:
        blockers.append(
            _blocker(
                "export_output_stale",
                stage="export",
                title="Built output is stale",
                explanation="Upstream Script, Cast, Produce, metadata, or chapter dependencies changed after the last build.",
                native_destination="export",
                target_id="export:result",
                dependency_fingerprint=_text(fingerprints.get("build_dependencies")),
            )
        )
    if output_exists and output_current and not output_valid:
        blockers.append(
            _blocker(
                "export_output_invalid",
                stage="export",
                title="Built output failed validation",
                explanation="Rebuild the audiobook while preserving the previous successful output until validation passes.",
                native_destination="export",
                target_id="export:result",
            )
        )

    if blockers:
        state = (
            "stale"
            if any(item.get("code") == "export_output_stale" for item in blockers)
            else "blocked"
        )
        return _stage(
            "export",
            state=state,
            summary="Export has readiness blockers.",
            blockers=blockers,
            safe_next_action=_action(
                "review_export_blockers",
                "Review Export blockers",
                native_destination="export",
                target_id="export:blockers",
            ),
            fingerprints=fingerprints,
        )

    if output_exists and output_current and output_valid:
        return _stage(
            "export",
            state="complete",
            summary="A current validated audiobook build is available.",
            safe_next_action=_action(
                "play_export_result",
                "Play result",
                native_destination="export",
                target_id="export:result",
            ),
            fingerprints=fingerprints,
        )

    return _stage(
        "export",
        state="ready",
        summary="Metadata and production dependencies are ready to build.",
        safe_next_action=_action(
            "build_audiobook",
            "Build audiobook",
            native_destination="export",
            target_id="export:build",
        ),
        fingerprints=fingerprints,
    )


def _compatibility_blockers_for_stage(
    compatibility: Mapping[str, Any],
    *,
    stage: str,
) -> list[dict[str, Any]]:
    if compatibility.get("state") == "current":
        return []
    native = [
        item
        for item in _list(compatibility.get("native_blockers"))
        if isinstance(item, Mapping)
        and (
            not _list(item.get("affected_stages"))
            or stage in _list(item.get("affected_stages"))
        )
    ]
    if native:
        return [
            _blocker(
                _text(item.get("code"))
                or _text(compatibility.get("code"))
                or "project_compatibility_blocked",
                stage=stage,
                title=_text(item.get("title"))
                or _text(compatibility.get("title"))
                or "Project compatibility requires attention",
                explanation=_text(item.get("explanation"))
                or _text(compatibility.get("explanation"))
                or "Open Maintenance to inspect and resolve the project compatibility state.",
                native_destination=_text(item.get("native_destination"))
                or "maintenance",
                target_id=_text(item.get("target_id"))
                or "maintenance:compatibility",
                safe_action_id=_text(item.get("safe_action_id"))
                or "open_maintenance_compatibility",
                dependency_fingerprint=_text(
                    compatibility.get("plan_fingerprint")
                ),
            )
            for item in native
        ]
    if _list(compatibility.get("native_blockers")):
        return []
    return [
        _blocker(
            _text(compatibility.get("code"))
            or "project_compatibility_blocked",
            stage=stage,
            title=_text(compatibility.get("title"))
            or "Project compatibility requires attention",
            explanation=_text(compatibility.get("explanation"))
            or "Open Maintenance to inspect and resolve the project compatibility state.",
            native_destination="maintenance",
            target_id="maintenance:compatibility",
            safe_action_id="open_maintenance_compatibility",
            dependency_fingerprint=_text(
                compatibility.get("plan_fingerprint")
            ),
        )
    ]


def build_project_flow_summary(
    *,
    project: Mapping[str, Any],
    source: Mapping[str, Any],
    script: Mapping[str, Any],
    cast: Mapping[str, Any],
    produce: Mapping[str, Any],
    export: Mapping[str, Any],
    compatibility: Mapping[str, Any] | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    compatibility_value = _mapping(compatibility)
    compatibility_state = _text(compatibility_value.get("state")) or "current"
    if compatibility_state not in {
        "current",
        "migration_required",
        "incompatible",
        "invalid",
        "unavailable",
    }:
        raise ProjectFlowError(
            f"Unsupported compatibility state: {compatibility_state}"
        )
    normalized_compatibility = {
        **dict(compatibility_value),
        "state": compatibility_state,
    }

    stages: list[dict[str, Any]] = []
    script_stage = _script_stage(
        script,
        compatibility_blockers=_compatibility_blockers_for_stage(
            normalized_compatibility,
            stage="script",
        ),
    )
    stages.append(script_stage)
    cast_stage = _cast_stage(
        cast,
        script_stage=script_stage,
        compatibility_blockers=_compatibility_blockers_for_stage(
            normalized_compatibility,
            stage="cast",
        ),
    )
    stages.append(cast_stage)
    produce_stage = _produce_stage(
        produce,
        cast_stage=cast_stage,
        compatibility_blockers=_compatibility_blockers_for_stage(
            normalized_compatibility,
            stage="produce",
        ),
    )
    stages.append(produce_stage)
    export_stage = _export_stage(
        export,
        produce_stage=produce_stage,
        compatibility_blockers=_compatibility_blockers_for_stage(
            normalized_compatibility,
            stage="export",
        ),
    )
    stages.append(export_stage)

    recommended = next(
        (stage for stage in stages if stage["state"] != "complete"),
        stages[-1],
    )
    all_blockers = [
        blocker
        for stage in stages
        for blocker in stage["blockers"]
    ]
    running_stage = next(
        (stage for stage in stages if stage["state"] == "running"),
        None,
    )
    resumable_stage = next(
        (stage for stage in stages if stage["state"] == "resumable"),
        None,
    )
    summary_state = (
        "unavailable"
        if compatibility_state == "unavailable"
        else "stale"
        if any(stage["state"] == "stale" for stage in stages)
        else "current"
    )

    return {
        "schema_version": PROJECT_FLOW_SCHEMA_VERSION,
        "generated_at_utc": generated_at_utc or utc_timestamp(),
        "summary_state": summary_state,
        "project": dict(project),
        "source": dict(source),
        "recommended_stage": recommended["key"],
        "safe_next_action": recommended.get("safe_next_action"),
        "stages": stages,
        "stage_map": {stage["key"]: stage for stage in stages},
        "blocker_count": sum(bool(item.get("blocking")) for item in all_blockers),
        "completion_state": (
            "complete"
            if all(stage["state"] == "complete" for stage in stages)
            else "requires_work"
        ),
        "resumable_operation": (
            {
                "stage": resumable_stage["key"],
                **dict(resumable_stage.get("operation") or {}),
            }
            if resumable_stage is not None
            else None
        ),
        "running_operation": (
            {
                "stage": running_stage["key"],
                **dict(running_stage.get("operation") or {}),
            }
            if running_stage is not None
            else None
        ),
        "compatibility": normalized_compatibility,
    }


def _script_entries(path: Path) -> tuple[list[dict[str, Any]] | None, str | None]:
    value, error = _read_json(path)
    if error:
        return None, error
    if value is None:
        return None, None
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        return None, "annotated_script.json must contain a JSON array of objects."
    return value, None


def _script_structure_valid(entries: list[dict[str, Any]]) -> tuple[bool, bool]:
    structure_valid = True
    attribution_valid = True
    for entry in entries:
        speaker = _text(entry.get("speaker") or entry.get("type"))
        text = entry.get("text")
        instruct = entry.get("instruct")
        if not isinstance(text, str) or not isinstance(instruct, str):
            structure_valid = False
        if speaker is None:
            attribution_valid = False
    return structure_valid, attribution_valid


def inspect_script_evidence(
    *,
    source_status: Mapping[str, Any],
    generation_status: Mapping[str, Any],
    script_path: str | Path,
    lifecycle_status: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    process = _mapping(generation_status.get("process"))
    checkpoint = _mapping(generation_status.get("checkpoint"))
    result = _mapping(generation_status.get("result"))
    entries, script_error = _script_entries(Path(script_path))
    structure_valid = False
    attribution_valid = False
    if entries is not None:
        structure_valid, attribution_valid = _script_structure_valid(entries)

    metadata = _mapping(result.get("metadata"))
    metadata_source = _mapping(metadata.get("source"))
    generation = _mapping(metadata.get("generation"))
    verification = _text(metadata_source.get("verification_status"))
    selected_fingerprint = _text(source_status.get("fingerprint"))
    metadata_fingerprint = _text(metadata_source.get("fingerprint"))
    fidelity_valid: bool | None
    if verification == "verified":
        fidelity_valid = bool(
            selected_fingerprint
            and metadata_fingerprint
            and selected_fingerprint == metadata_fingerprint
        )
    elif verification == "unverified":
        fidelity_valid = None
    else:
        fidelity_valid = False if entries is not None else None

    checkpoint_status = _text(checkpoint.get("status")) or "none"
    lifecycle = _mapping(lifecycle_status)
    lifecycle_available = bool(lifecycle)
    explicit_acceptance = lifecycle.get("accepted") is True
    accepted = bool(explicit_acceptance)
    lifecycle_state = _text(lifecycle.get("state"))
    lifecycle_action = _mapping(lifecycle.get("primary_action"))
    import_candidate_exists = bool(
        entries is None
        and lifecycle_state == "review_required"
        and lifecycle_action.get("id") == "review_imported_script"
    )
    if accepted:
        fidelity_valid = True
    if lifecycle_state == "stale":
        artifact_current = False
    else:
        artifact_current = (
            fidelity_valid is not False
            and checkpoint_status not in {
                "incompatible",
                "invalid",
                "corrupt",
                "unknown",
            }
        ) if entries is not None else None
    review_required = bool(
        import_candidate_exists
        or (
            entries is not None
            and (
                lifecycle_state in {"review_required", "stale"}
                or not accepted
            )
        )
    )
    failure_statuses = {
        "script_corrupt",
        "script_invalid",
        "metadata_corrupt",
        "metadata_invalid",
        "orphan_metadata",
    }
    failed = bool(script_error) or result.get("status") in failure_statuses

    return {
        "source_available": bool(
            source_status.get("persisted")
            and source_status.get("exists")
            and source_status.get("readable")
        ),
        "source_error": source_status.get("error"),
        "process": dict(process),
        "resumable": checkpoint.get("resumable") is True,
        "failed": failed,
        "failure_reason": script_error or "; ".join(
            str(item) for item in _list(result.get("errors"))
        ),
        "artifact_exists": entries is not None,
        "import_candidate_exists": import_candidate_exists,
        "structure_valid": structure_valid if entries is not None else None,
        "attribution_valid": attribution_valid if entries is not None else None,
        "fidelity_valid": fidelity_valid,
        "artifact_current": artifact_current,
        "provenance_recorded": bool(
            (
                lifecycle_available
                and isinstance(lifecycle.get("provenance"), Mapping)
            )
            or (
                metadata
                and generation.get("fingerprint")
                and isinstance(generation.get("effective_identity"), Mapping)
            )
        ) if entries is not None else None,
        "finalization_complete": result.get("status") in {"complete", "legacy"}
        if entries is not None
        else None,
        "review_required": review_required,
        "accepted": accepted,
        "fingerprints": {
            "source": selected_fingerprint,
            "script": _text(result.get("script_fingerprint")),
            "generation": _text(generation.get("fingerprint")),
            "accepted_receipt": _text(
                _mapping(lifecycle.get("fingerprints")).get("accepted_receipt")
            ),
        },
        "provenance": {
            "generation_method": lifecycle.get("generation_method"),
            "verification_status": (
                "verified_at_acceptance" if accepted else verification
            ),
            "acceptance_status": "accepted" if accepted else lifecycle_state,
        },
    }


def _safe_project_path(root: Path, value: Any) -> Path | None:
    text = _text(value)
    if text is None:
        return None
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.expanduser().resolve()
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved


def _voice_configuration_issue(
    *,
    root: Path,
    voice_name: str,
    voice_config: Mapping[str, Any],
) -> tuple[str | None, str | None]:
    config = voice_config.get(voice_name)
    if not isinstance(config, Mapping):
        return "missing", "Production voice configuration is missing."
    try:
        resolution = resolve_voice_alias(voice_name, dict(voice_config))
    except VoiceAliasError as exc:
        return "invalid", str(exc)
    target_name = resolution.resolved_target or voice_name
    target = voice_config.get(target_name)
    if not isinstance(target, Mapping):
        return "invalid", "The resolved Voice target is missing."

    voice_type = _text(target.get("type")) or "custom"
    if voice_type == "custom":
        if not _text(target.get("voice")):
            return "invalid", "Built-in/custom Voice selection is empty."
        return None, None
    if voice_type == "clone":
        audio = _safe_project_path(root, target.get("ref_audio"))
        if audio is None or not audio.is_file():
            return "clone", "Clone reference audio is missing or outside the project."
        if not _text(target.get("ref_text")):
            return "clone", "Clone reference transcript is empty."
        clone_backend = _text(target.get("clone_backend"))
        if clone_backend == "voxcpm2_controlled":
            return (
                "controlled",
                "The legacy VoxCPM2 clone does not provide reliable per-line delivery control. Re-preview with Qwen or use the standard clone.",
            )
        if (
            clone_backend == "qwen3_instruction_controlled"
            and not _text(target.get("controlled_clone_configuration_fingerprint"))
        ):
            return "controlled", "Controlled clone approval is missing or stale."
        return None, None
    if voice_type == "community_qvoice":
        pack = _safe_project_path(root, target.get("community_pack_path"))
        if pack is None or not pack.is_file():
            return "invalid", "The imported community Qwen Voice pack is missing or outside the project."
        expected_hash = _text(target.get("community_pack_sha256"))
        if expected_hash is None or sha256_file(pack) != expected_hash:
            return "invalid", "The imported community Qwen Voice pack failed its integrity check."
        if not _text(target.get("community_pack_approval_fingerprint")):
            return "invalid", "The community Qwen Voice listening approval is missing or stale."
        if not _text(target.get("description") or target.get("character_style")):
            return "invalid", "The community Qwen Voice persistent description is empty."
        return None, None
    if voice_type == "design":
        if not _text(target.get("description")):
            return "invalid", "Designed Voice description is empty."
        return None, None
    if voice_type == "lora":
        adapter_id = _text(target.get("adapter_id"))
        adapter_path = _safe_project_path(
            root,
            target.get("adapter_path") or target.get("mlx_model_path"),
        )
        if not adapter_id or adapter_path is None or not adapter_path.exists():
            return "adapter", "The selected adapter artifact is missing."
        manifests = [
            adapter_path / "mlx_model" / "mlx_export_manifest.json",
            adapter_path / "mlx_export_manifest.json",
            adapter_path / "training_meta.json",
        ]
        manifest = None
        for path in manifests:
            value, error = _read_json(path)
            if error:
                return "adapter", f"Adapter manifest is invalid: {error}"
            if isinstance(value, Mapping):
                manifest = value
                if "production_assignment_supported" in value:
                    break
        if not isinstance(manifest, Mapping):
            return "adapter", "Adapter production manifest is missing."
        if manifest.get("production_assignment_supported") is not True:
            return "adapter", "Adapter is experimental or not approved for production assignment."
        review = _mapping(manifest.get("validation")).get(
            "manual_audio_review_status"
        ) or manifest.get("manual_audio_review_status")
        if review not in {"approved", "complete", "passed"}:
            return "adapter", "Adapter listening review is incomplete."
        return None, None
    return "invalid", f"Unsupported production Voice type: {voice_type}."


def cast_aggregate_to_flow_evidence(
    aggregate: Mapping[str, Any],
    *,
    native_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    native = copy.deepcopy(dict(native_evidence or {}))
    characters = [
        item
        for item in _list(aggregate.get("characters"))
        if isinstance(item, Mapping)
    ]
    required = [
        item
        for item in characters
        if item.get("required_for_completion") is True
    ]

    def native_ids(key: str) -> set[str]:
        return {
            str(value)
            for value in _list(native.get(key))
            if _text(value)
        }

    unresolved = native_ids("unresolved_identity_ids")
    ambiguous = native_ids("ambiguous_mapping_ids")
    missing_voice = native_ids("missing_voice_ids")
    invalid_voice = native_ids("invalid_voice_ids")
    invalid_clone = native_ids("invalid_clone_ids")
    controlled_missing = native_ids(
        "controlled_clone_approval_missing_ids"
    )
    invalid_adapter = native_ids("invalid_adapter_ids")
    stale_voice = native_ids("stale_voice_ids")

    identity_codes = {
        "cast_identity_unresolved",
        "cast_stable_character_id_missing",
        "cast_native_identity_unresolved",
    }
    ambiguous_codes = {
        "cast_script_label_ambiguous",
        "cast_script_label_missing",
        "cast_native_script_label_ambiguous",
    }
    clone_codes = {
        "cast_clone_reference_audio_invalid",
        "cast_clone_reference_transcript_missing",
        "cast_native_clone_invalid",
    }
    controlled_codes = {
        "cast_controlled_clone_approval_missing",
        "cast_native_controlled_clone_approval_missing",
    }
    adapter_codes = {
        "cast_adapter_invalid",
        "cast_adapter_not_approved",
        "cast_native_adapter_invalid",
    }
    invalid_voice_codes = {
        "cast_alias_target_invalid",
        "cast_alias_target_missing",
        "cast_designed_voice_missing",
        "cast_voice_method_unsupported",
        "cast_native_voice_invalid",
    }
    missing_voice_codes = {
        "cast_voice_selection_missing",
        "cast_native_voice_missing",
    }
    stale_codes = {"cast_native_voice_stale"}

    for character in required:
        character_id = _text(character.get("character_id"))
        if character_id is None:
            continue
        blocker_codes = {
            str(item.get("code") or "")
            for item in _list(character.get("blockers"))
            if isinstance(item, Mapping)
        }
        readiness = _text(character.get("readiness_state"))
        voice = _mapping(character.get("voice"))
        if blocker_codes & identity_codes:
            unresolved.add(character_id)
        if blocker_codes & ambiguous_codes:
            ambiguous.add(character_id)
        if blocker_codes & clone_codes:
            invalid_clone.add(character_id)
        if blocker_codes & controlled_codes:
            controlled_missing.add(character_id)
        if blocker_codes & adapter_codes:
            invalid_adapter.add(character_id)
        if blocker_codes & invalid_voice_codes:
            invalid_voice.add(character_id)
        if blocker_codes & missing_voice_codes:
            missing_voice.add(character_id)
        if blocker_codes & stale_codes or readiness == "preview_recommended":
            stale_voice.add(character_id)
        if readiness == "needs_identity_review" and not (
            blocker_codes & ambiguous_codes
        ):
            unresolved.add(character_id)
        if (
            character.get("required_for_completion") is True
            and voice.get("valid") is not True
            and not (
                blocker_codes
                & (
                    clone_codes
                    | controlled_codes
                    | adapter_codes
                    | invalid_voice_codes
                    | ambiguous_codes
                    | identity_codes
                )
            )
        ):
            missing_voice.add(character_id)

    aggregate_valid = sum(
        item.get("readiness_state") == "ready"
        and _mapping(item.get("voice")).get("valid") is True
        for item in required
    )
    native_required_value = native.get("required_speaking_characters")
    native_valid_value = native.get("valid_production_voices")
    required_count = max(
        len(required),
        int(native_required_value or 0),
    )
    valid_count = aggregate_valid
    if native_valid_value is not None:
        valid_count = min(valid_count, int(native_valid_value or 0))

    compatibility = _mapping(aggregate.get("compatibility"))
    roster_source = _text(compatibility.get("roster_source"))
    summary = _mapping(aggregate.get("summary"))
    fingerprints = {
        **dict(_mapping(native.get("fingerprints"))),
        **dict(_mapping(aggregate.get("fingerprints"))),
    }
    result = {
        **native,
        "aggregate_schema_version": aggregate.get("schema_version"),
        "process": dict(_mapping(native.get("process"))),
        "resumable": bool(native.get("resumable")),
        "failed": bool(native.get("failed"))
        or summary.get("state") == "failed",
        "failure_reason": native.get("failure_reason"),
        "roster_exists": (
            native.get("roster_exists")
            if native.get("roster_exists") is not None
            else roster_source not in {None, "missing"}
            or bool(characters)
        ),
        "review_required": bool(native.get("review_required"))
        or roster_source == "draft",
        "roster_approved": (
            native.get("roster_approved")
            if native.get("roster_approved") is not None
            else roster_source == "approved"
        ),
        "roster_current": (
            native.get("roster_current")
            if native.get("roster_current") is not None
            else compatibility.get("state") in {None, "current", "advisory"}
        ),
        "required_speaking_characters": required_count,
        "valid_production_voices": valid_count,
        "unresolved_identity_ids": sorted(unresolved),
        "ambiguous_mapping_ids": sorted(ambiguous),
        "missing_voice_ids": sorted(missing_voice),
        "invalid_voice_ids": sorted(invalid_voice),
        "invalid_clone_ids": sorted(invalid_clone),
        "controlled_clone_approval_missing_ids": sorted(
            controlled_missing
        ),
        "invalid_adapter_ids": sorted(invalid_adapter),
        "stale_voice_ids": sorted(stale_voice),
        "fingerprints": fingerprints,
    }
    return result


def inspect_cast_evidence(
    *,
    root_dir: str | Path,
    roster_status: Mapping[str, Any],
    approved_roster_path: str | Path,
    script_path: str | Path,
    voice_config_path: str | Path,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    process = _mapping(roster_status.get("process"))
    progress = _mapping(roster_status.get("progress"))
    approved_status = _mapping(roster_status.get("approved"))
    draft_status = _mapping(roster_status.get("draft"))
    roster_approved = approved_status.get("status") == "approved"
    roster_current = approved_status.get("compatible_source") is not False
    roster_exists = roster_approved or draft_status.get("status") == "draft"
    review_required = draft_status.get("status") == "draft" and not roster_approved

    roster, roster_error = _read_json(approved_roster_path)
    if not isinstance(roster, Mapping):
        roster = {}
    entries = [item for item in _list(roster.get("entries")) if isinstance(item, Mapping)]
    script_entries, script_error = _script_entries(Path(script_path))
    if script_entries is None:
        script_entries = []
    speakers, line_speakers, speaker_counts = build_script_voice_index(script_entries)
    voice_config, voice_error = _read_json(voice_config_path)
    if not isinstance(voice_config, Mapping):
        voice_config = {}

    unresolved: list[str] = []
    ambiguous: list[str] = []
    missing_voice: list[str] = []
    invalid_voice: list[str] = []
    invalid_clone: list[str] = []
    controlled_missing: list[str] = []
    invalid_adapter: list[str] = []
    required = 0
    valid_voices = 0

    for entry in entries:
        if entry.get("speaking_status") not in {"speaker", "narrator"}:
            continue
        required += 1
        character_id = _text(entry.get("id")) or _stable_identifier(
            "character", entry.get("canonical_name")
        )
        if entry.get("resolution_status") != "resolved":
            unresolved.append(character_id)
            continue
        mapping = resolve_script_voice_name(
            dict(entry),
            speakers=speakers,
            line_speakers=line_speakers,
            speaker_counts=speaker_counts,
        )
        voice_name = _text(mapping.get("script_voice_name"))
        if voice_name is None:
            ambiguous.append(character_id)
            continue
        issue, _ = _voice_configuration_issue(
            root=root,
            voice_name=voice_name,
            voice_config=voice_config,
        )
        if issue is None:
            valid_voices += 1
        elif issue == "missing":
            missing_voice.append(character_id)
        elif issue == "clone":
            invalid_clone.append(character_id)
        elif issue == "controlled":
            controlled_missing.append(character_id)
        elif issue == "adapter":
            invalid_adapter.append(character_id)
        else:
            invalid_voice.append(character_id)

    failed = bool(roster_error or script_error or voice_error)
    progress_status = _text(progress.get("status"))
    if progress_status in {"invalid", "corrupt", "failed"}:
        failed = True

    return {
        "process": dict(process),
        "resumable": progress_status == "resumable",
        "failed": failed,
        "failure_reason": roster_error or script_error or voice_error,
        "roster_exists": roster_exists,
        "review_required": review_required,
        "roster_approved": roster_approved,
        "roster_current": roster_current,
        "required_speaking_characters": required,
        "valid_production_voices": valid_voices,
        "unresolved_identity_ids": unresolved,
        "ambiguous_mapping_ids": ambiguous,
        "missing_voice_ids": missing_voice,
        "invalid_voice_ids": invalid_voice,
        "invalid_clone_ids": invalid_clone,
        "controlled_clone_approval_missing_ids": controlled_missing,
        "invalid_adapter_ids": invalid_adapter,
        "stale_voice_ids": [],
        "fingerprints": {
            "script": fingerprint_value(script_entries) if script_entries else None,
            "roster": _text(approved_status.get("fingerprint")),
            "voice_config": fingerprint_value(dict(voice_config))
            if voice_config
            else None,
        },
    }


def _synthesis_config(config_path: str | Path) -> dict[str, Any]:
    value, _ = _read_json(config_path)
    tts = dict(_mapping(_mapping(value).get("tts")))
    tts.pop("pause_between_speakers_ms", None)
    tts.pop("pause_same_speaker_ms", None)
    return tts


def produce_aggregate_to_flow_evidence(
    aggregate: Mapping[str, Any],
    *,
    native_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    native = copy.deepcopy(dict(native_evidence or {}))
    rows = [
        item
        for item in _list(aggregate.get("chunks"))
        if isinstance(item, Mapping)
    ]

    def native_ids(key: str) -> set[str]:
        return {
            str(value)
            for value in _list(native.get(key))
            if _text(value)
        }

    missing = native_ids("missing_chunk_ids")
    stale = native_ids("stale_chunk_ids")
    failed = native_ids("failed_chunk_ids")
    hash_invalid = native_ids("hash_invalid_chunk_ids")
    review = native_ids("review_chunk_ids")
    listening = native_ids("listening_chunk_ids")
    current_ids: set[str] = set()
    for row in rows:
        chunk_id = _text(row.get("chunk_id"))
        if chunk_id is None:
            continue
        state = _text(row.get("state"))
        reason = _text(row.get("reason"))
        if state == "ready":
            missing.add(chunk_id)
        elif state == "stale":
            stale.add(chunk_id)
        elif state == "failed":
            if reason == "audio_hash_mismatch":
                hash_invalid.add(chunk_id)
            else:
                failed.add(chunk_id)
        elif state == "needs_review":
            review.add(chunk_id)
        elif state == "needs_listening":
            listening.add(chunk_id)
        elif state == "current":
            current_ids.add(chunk_id)

    summary = _mapping(aggregate.get("summary"))
    required = max(
        int(native.get("required_chunks") or 0),
        int(summary.get("required_chunk_count") or len(rows)),
    )
    aggregate_current = int(summary.get("current_count") or len(current_ids))
    native_current = native.get("current_chunks")
    current = (
        min(aggregate_current, int(native_current or 0))
        if native_current is not None
        else aggregate_current
    )
    fingerprints = {
        **dict(_mapping(native.get("fingerprints"))),
        **dict(_mapping(aggregate.get("fingerprints"))),
    }
    process = {
        **dict(_mapping(native.get("process"))),
        **dict(_mapping(aggregate.get("process"))),
    }
    return {
        **native,
        "aggregate_schema_version": aggregate.get("schema_version"),
        "process": process,
        "resumable": bool(native.get("resumable"))
        or (
            process.get("running") is not True
            and required > current
            and int(process.get("cancelled_count") or 0) > 0
        ),
        "required_chunks": required,
        "current_chunks": current,
        "missing_chunk_ids": sorted(missing),
        "stale_chunk_ids": sorted(stale),
        "failed_chunk_ids": sorted(failed),
        "hash_invalid_chunk_ids": sorted(hash_invalid),
        "review_chunk_ids": sorted(review),
        "listening_chunk_ids": sorted(listening),
        "fingerprints": fingerprints,
        "collector_error": native.get("collector_error"),
    }


def inspect_produce_evidence(
    *,
    root_dir: str | Path,
    chunks_path: str | Path,
    voice_config_path: str | Path,
    config_path: str | Path,
    process: Mapping[str, Any] | None = None,
    file_hasher: Callable[[str | Path], str] = sha256_file,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    chunks, chunks_error = _read_json(chunks_path)
    voice_config, voice_error = _read_json(voice_config_path)
    if not isinstance(chunks, list):
        chunks = []
    if not isinstance(voice_config, Mapping):
        voice_config = {}
    synthesis = _synthesis_config(config_path)

    missing: list[str] = []
    stale: list[str] = []
    failed: list[str] = []
    hash_invalid: list[str] = []
    review: list[str] = []
    listening: list[str] = []
    required = 0
    current = 0

    for index, chunk_value in enumerate(chunks):
        if not isinstance(chunk_value, Mapping):
            failed.append(f"chunk:{index}")
            continue
        chunk = dict(chunk_value)
        if not _text(chunk.get("text")):
            continue
        required += 1
        chunk_id = f"chunk:{chunk.get('id', index)}"
        status = _text(chunk.get("status")) or "pending"
        if status == "error" or chunk.get("audio_state") == "failed":
            failed.append(chunk_id)
            continue
        audio_path_value = _text(chunk.get("audio_path"))
        if status != "done" or not audio_path_value:
            missing.append(chunk_id)
            continue
        speaker = _text(chunk.get("speaker")) or ""
        try:
            resolved = resolve_voice_alias(speaker, dict(voice_config)).resolved_target
            expected = audio_binding_fingerprint(
                chunk=chunk,
                resolved_speaker=resolved,
                voice_config=dict(voice_config),
                synthesis_config=synthesis_config_with_generation_seed(
                    synthesis,
                    chunk,
                ),
            )
        except (VoiceAliasError, AudioArtifactError, ValueError, TypeError):
            stale.append(chunk_id)
            continue
        if chunk.get("audio_state") != "current" or chunk.get("audio_fingerprint") != expected:
            stale.append(chunk_id)
            continue
        try:
            audio_path = confined_audio_path(root, audio_path_value)
        except AudioArtifactError:
            failed.append(chunk_id)
            continue
        if not audio_path.is_file():
            missing.append(chunk_id)
            continue
        try:
            actual_hash = file_hasher(audio_path)
        except (OSError, AudioArtifactError):
            failed.append(chunk_id)
            continue
        if not _text(chunk.get("audio_sha256")) or chunk.get("audio_sha256") != actual_hash:
            hash_invalid.append(chunk_id)
            continue
        if chunk.get("audio_research_only") is True:
            review.append(chunk_id)
            continue
        if chunk.get("review_required") is True or chunk.get("review_flag") is True:
            review.append(chunk_id)
            continue
        if (
            chunk.get("listening_required") is True
            and chunk.get("listening_state") not in {"approved", "complete", "passed"}
        ):
            listening.append(chunk_id)
            continue
        current += 1

    process_value = dict(process or {})
    resumable = bool(
        not process_value.get("running")
        and required > current
        and any(chunk.get("status") == "generating" for chunk in chunks if isinstance(chunk, Mapping))
    )
    return {
        "process": process_value,
        "resumable": resumable,
        "required_chunks": required,
        "current_chunks": current,
        "missing_chunk_ids": missing,
        "stale_chunk_ids": stale,
        "failed_chunk_ids": failed,
        "hash_invalid_chunk_ids": hash_invalid,
        "review_chunk_ids": review,
        "listening_chunk_ids": listening,
        "fingerprints": {
            "chunks": fingerprint_value(chunks) if chunks else None,
            "voice_config": fingerprint_value(dict(voice_config))
            if voice_config
            else None,
            "synthesis": fingerprint_value(synthesis),
        },
        "collector_error": chunks_error or voice_error,
    }


def export_aggregate_to_flow_evidence(
    aggregate: Mapping[str, Any],
    *,
    native_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    native = copy.deepcopy(dict(native_evidence or {}))
    metadata = _mapping(aggregate.get("metadata"))
    missing_metadata = [
        field
        for field in ("title", "author")
        if not _text(metadata.get(field))
    ]
    blockers = [
        item
        for item in _list(aggregate.get("blockers"))
        if isinstance(item, Mapping)
    ]
    unavailable_formats = sorted(
        {
            str(value)
            for item in blockers
            if item.get("code") in {
                "export_format_unknown",
                "export_format_unavailable",
            }
            for value in _list(_mapping(aggregate.get("plan")).get("formats"))
            if str(value) not in {"mp3", "m4b", "audacity"}
        }
    )
    invalid_chapters = [
        "export:chapters"
        for item in blockers
        if item.get("code") == "export_chapters_required"
    ]
    selected_outputs = [
        item
        for item in _list(aggregate.get("selected_outputs"))
        if isinstance(item, Mapping)
    ]
    output_exists = bool(selected_outputs) and all(
        item.get("exists") is True for item in selected_outputs
    )
    aggregate_current = bool(selected_outputs) and all(
        item.get("state") == "current" for item in selected_outputs
    )
    aggregate_valid = bool(selected_outputs) and all(
        item.get("state") not in {"invalid", "missing"}
        for item in selected_outputs
    )
    native_output_current = native.get("output_current")
    native_output_valid = native.get("output_valid")
    output_current = aggregate_current
    output_valid = aggregate_valid
    if native.get("output_exists") is True:
        if native_output_current is False:
            output_current = False
        if native_output_valid is False:
            output_valid = False
    process = {
        **dict(_mapping(native.get("process"))),
        **dict(_mapping(aggregate.get("process"))),
    }
    fingerprints = {
        **dict(_mapping(native.get("fingerprints"))),
        "build_dependencies": _mapping(
            aggregate.get("fingerprints")
        ).get("dependencies"),
        "output": next(
            (
                item.get("sha256")
                for item in selected_outputs
                if item.get("state") == "current"
                and _text(item.get("sha256"))
            ),
            None,
        ),
    }
    return {
        **native,
        "aggregate_schema_version": aggregate.get("schema_version"),
        "process": process,
        "failed": bool(native.get("failed"))
        or aggregate.get("state") == "failed"
        or bool(process.get("last_error")),
        "failure_reason": (
            native.get("failure_reason")
            or process.get("last_error")
        ),
        "missing_metadata_fields": missing_metadata,
        "invalid_chapter_ids": invalid_chapters,
        "unavailable_formats": unavailable_formats,
        "output_exists": output_exists,
        "output_current": output_current,
        "output_valid": output_valid,
        "fingerprints": fingerprints,
        "technical": {
            "selected_formats": copy.deepcopy(
                _list(aggregate.get("formats"))
            ),
            "output_relative_path": next(
                (
                    _mapping(item.get("technical_details")).get(
                        "relative_path"
                    )
                    for item in selected_outputs
                    if item.get("exists") is True
                ),
                None,
            ),
        },
    }


def inspect_export_evidence(
    *,
    root_dir: str | Path,
    state_path: str | Path,
    audiobook_path: str | Path,
    m4b_path: str | Path,
    produce_fingerprints: Mapping[str, Any],
    process: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    state_value, state_error = _read_json(state_path)
    state = _mapping(state_value)
    export_meta = _mapping(state.get("export"))
    metadata = _mapping(export_meta.get("metadata"))
    required_fields = ("title", "author")
    missing_fields = [field for field in required_fields if not _text(metadata.get(field))]
    chapters = _list(export_meta.get("chapters"))
    invalid_chapters: list[str] = []
    for index, chapter in enumerate(chapters):
        value = _mapping(chapter)
        if not _text(value.get("name")) or value.get("order") is None:
            invalid_chapters.append(
                _text(value.get("id")) or f"chapter:{index}"
            )
    selected_formats = [str(item).lower() for item in _list(export_meta.get("formats"))]
    supported_formats = {"mp3", "m4b", "audacity", "chapter_separated"}
    unavailable_formats = [
        item for item in selected_formats if item not in supported_formats
    ]

    candidates = [Path(audiobook_path), Path(m4b_path)]
    output = next((path for path in candidates if path.is_file()), None)
    build = _mapping(export_meta.get("last_build"))
    dependency_fingerprint = fingerprint_value(dict(produce_fingerprints))
    output_current = bool(
        output is not None
        and _text(build.get("dependency_fingerprint")) == dependency_fingerprint
    )
    output_valid = False
    if output is not None:
        recorded_hash = _text(build.get("sha256"))
        try:
            output_valid = bool(recorded_hash and recorded_hash == sha256_file(output))
        except (OSError, AudioArtifactError):
            output_valid = False

    return {
        "process": dict(process or {}),
        "failed": bool(state_error or build.get("status") == "failed"),
        "failure_reason": state_error or _text(build.get("error")),
        "missing_metadata_fields": missing_fields,
        "invalid_chapter_ids": invalid_chapters,
        "unavailable_formats": unavailable_formats,
        "output_exists": output is not None,
        "output_current": output_current,
        "output_valid": output_valid,
        "fingerprints": {
            "build_dependencies": dependency_fingerprint,
            "output": sha256_file(output) if output is not None and output_valid else None,
        },
        "technical": {
            "selected_formats": selected_formats,
            "output_relative_path": (
                str(output.resolve().relative_to(root))
                if output is not None and output.resolve().is_relative_to(root)
                else None
            ),
        },
    }


def _migration_blocker_destination(explanation: str) -> dict[str, Any]:
    lowered = explanation.casefold()
    if "approved roster" in lowered or "character roster" in lowered:
        return {
            "code": "project_approved_roster_incompatible",
            "title": "Approved Cast roster is incompatible",
            "native_destination": "cast",
            "target_id": "cast:review",
            "safe_action_id": "review_cast",
            "affected_stages": ["cast", "produce", "export"],
        }
    if "source" in lowered or "script" in lowered:
        return {
            "code": "project_script_dependency_incompatible",
            "title": "Script dependency is incompatible",
            "native_destination": "script",
            "target_id": "script:review",
            "safe_action_id": "review_script",
            "affected_stages": ["script", "cast", "produce", "export"],
        }
    return {
        "code": "project_migration_blocked",
        "title": "Project migration is blocked",
        "native_destination": "maintenance",
        "target_id": "maintenance:compatibility",
        "safe_action_id": "open_maintenance_compatibility",
        "affected_stages": ["script", "cast", "produce", "export"],
    }


def _public_migration_actions(value: Any) -> list[dict[str, Any]]:
    result = []
    for index, item_value in enumerate(_list(value)):
        item = _mapping(item_value)
        action_id = _text(item.get("action"))
        if action_id is None:
            continue
        result.append(
            {
                "id": f"migration:action:{index}",
                "action": action_id,
                "description": _text(item.get("description")),
                "destructive": bool(item.get("destructive")),
                "native_destination": "maintenance",
                "target_id": f"maintenance:migration:{action_id}",
                "technical_detail_available": bool(_text(item.get("path"))),
            }
        )
    return result


def inspect_compatibility_evidence(
    migration_status: Mapping[str, Any] | None,
    migration_error: str | None = None,
) -> dict[str, Any]:
    if migration_error:
        return {
            "state": "unavailable",
            "code": "project_compatibility_unavailable",
            "title": "Project compatibility could not be inspected",
            "explanation": migration_error,
        }
    value = _mapping(migration_status)
    if value.get("migration_blocked") is True:
        native_blockers = []
        for index, explanation_value in enumerate(_list(value.get("blockers"))):
            explanation = str(explanation_value).strip()
            if not explanation:
                continue
            destination = _migration_blocker_destination(explanation)
            native_blockers.append(
                {
                    **destination,
                    "id": f"migration:blocker:{index}",
                    "explanation": explanation,
                }
            )
        return {
            "state": "incompatible",
            "code": "project_migration_blocked",
            "title": "Project migration is blocked",
            "explanation": (
                native_blockers[0]["explanation"]
                if native_blockers
                else "Open Maintenance to inspect the migration blockers before continuing."
            ),
            "plan_fingerprint": value.get("plan_fingerprint"),
            "native_blockers": native_blockers,
            "native_actions": _public_migration_actions(
                value.get("actions")
            ),
        }
    if value.get("migration_required") is True:
        return {
            "state": "migration_required",
            "code": "project_migration_required",
            "title": "Project migration is required",
            "explanation": "Review and apply the current migration plan in Maintenance before production continues.",
            "plan_fingerprint": value.get("plan_fingerprint"),
            "native_actions": _public_migration_actions(
                value.get("actions")
            ),
        }
    return {
        "state": "current",
        "plan_fingerprint": value.get("plan_fingerprint"),
        "native_blockers": [],
        "native_actions": [],
    }


def inspect_project_flow(
    *,
    root_dir: str | Path,
    config_path: str | Path,
    script_path: str | Path,
    script_metadata_path: str | Path,
    chunks_path: str | Path,
    voice_config_path: str | Path,
    roster_path: str | Path,
    state_path: str | Path,
    audiobook_path: str | Path,
    m4b_path: str | Path,
    source_status: Mapping[str, Any],
    generation_status: Mapping[str, Any],
    script_lifecycle_status: Mapping[str, Any] | None = None,
    roster_status: Mapping[str, Any],
    cast_aggregate_status: Mapping[str, Any] | None = None,
    produce_aggregate_status: Mapping[str, Any] | None = None,
    export_aggregate_status: Mapping[str, Any] | None = None,
    audio_process: Mapping[str, Any] | None = None,
    export_process: Mapping[str, Any] | None = None,
    migration_status: Mapping[str, Any] | None = None,
    migration_error: str | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    state_value, state_error = _read_json(state_path)
    state = _mapping(state_value)
    source_path = _text(source_status.get("path"))
    source_basename = _text(source_status.get("basename"))
    project_id = _text(state.get("project_id")) or _stable_identifier(
        "project", root
    )
    project_name = _text(state.get("project_name"))
    if project_name is None and source_basename:
        project_name = Path(source_basename).stem
    project_name = project_name or "Alexandria project"
    source_suffix = Path(source_basename).suffix.lower().lstrip(".") if source_basename else None
    latest_paths = [
        Path(path)
        for path in (
            state_path,
            script_path,
            script_metadata_path,
            chunks_path,
            voice_config_path,
            roster_path,
            audiobook_path,
            m4b_path,
        )
        if Path(path).exists()
    ]
    latest_activity = None
    if latest_paths:
        latest_activity = datetime.fromtimestamp(
            max(path.stat().st_mtime for path in latest_paths),
            tz=timezone.utc,
        ).isoformat().replace("+00:00", "Z")

    source = {
        "selected": bool(source_status.get("persisted")),
        "available": bool(source_status.get("exists") and source_status.get("readable")),
        "title": Path(source_basename).stem if source_basename else None,
        "filename": source_basename,
        "type": source_suffix,
        "source_language": _text(state.get("source_language")),
        "output_language": _text(state.get("output_language")),
        "fingerprint": _text(source_status.get("fingerprint")),
        "error": source_status.get("error"),
    }
    project = {
        "id": project_id,
        "name": project_name,
        "latest_meaningful_activity": latest_activity,
        "archive_state": _text(state.get("archive_state")) or "active",
        "technical_details": {
            "project_path": str(root),
            "source_path": source_path,
            "state_error": state_error,
        },
    }
    script = inspect_script_evidence(
        source_status=source_status,
        generation_status=generation_status,
        script_path=script_path,
        lifecycle_status=script_lifecycle_status,
    )
    native_cast = inspect_cast_evidence(
        root_dir=root,
        roster_status=roster_status,
        approved_roster_path=roster_path,
        script_path=script_path,
        voice_config_path=voice_config_path,
    )
    cast = (
        cast_aggregate_to_flow_evidence(
            cast_aggregate_status,
            native_evidence=native_cast,
        )
        if isinstance(cast_aggregate_status, Mapping)
        else native_cast
    )
    produce_aggregate_available = (
        isinstance(produce_aggregate_status, Mapping)
        and not produce_aggregate_status.get("error")
    )
    native_produce = (
        {}
        if produce_aggregate_available
        else inspect_produce_evidence(
            root_dir=root,
            chunks_path=chunks_path,
            voice_config_path=voice_config_path,
            config_path=config_path,
            process=audio_process,
        )
    )
    produce = (
        produce_aggregate_to_flow_evidence(
            produce_aggregate_status,
            native_evidence=native_produce,
        )
        if produce_aggregate_available
        else native_produce
    )
    native_export = inspect_export_evidence(
        root_dir=root,
        state_path=state_path,
        audiobook_path=audiobook_path,
        m4b_path=m4b_path,
        produce_fingerprints=_mapping(produce.get("fingerprints")),
        process=export_process,
    )
    export = (
        export_aggregate_to_flow_evidence(
            export_aggregate_status,
            native_evidence=native_export,
        )
        if isinstance(export_aggregate_status, Mapping)
        else native_export
    )
    compatibility = inspect_compatibility_evidence(
        migration_status,
        migration_error,
    )
    return build_project_flow_summary(
        project=project,
        source=source,
        script=script,
        cast=cast,
        produce=produce,
        export=export,
        compatibility=compatibility,
        generated_at_utc=generated_at_utc,
    )
