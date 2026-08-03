from __future__ import annotations

import copy
import json
import platform
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from audio_artifacts import (
    AudioArtifactError,
    audio_binding_fingerprint,
    confined_audio_path,
    sha256_file,
)
from approved_audio import (
    active_approved_audio_lock,
    approved_audio_binding_fingerprint,
)
from audio_failure import public_audio_failure
from audio_generation_policy import synthesis_config_with_generation_seed
from audio_generation_provenance import resolve_audio_generation_provenance
from audio_processing import generated_speech_duration_bounds
from audio_synthesis_config import synthesis_binding_config
from audio_takes import (
    public_take as public_audio_take,
    registry_view as audio_take_registry_view,
)
from cast_aggregate import inspect_cast_project
from chapter_assembly import (
    build_chapters as build_final_listen_chapters,
    source_order_fingerprint as chapter_source_order_fingerprint,
    transition_context as final_listen_transition_context,
)
from dialogue_continuity import (
    effective_delivery_instruction,
    effective_pause_after_ms,
    resolve_spoken_continuity,
)
from generation_state import fingerprint_value
from produce_blocker_routing import missing_voice_blocker_route
from voice_aliases import VoiceAliasError, resolve_voice_alias


SCHEMA_VERSION = 1
PRODUCE_STATES = frozenset(
    {
        "ready",
        "generating",
        "needs_listening",
        "needs_review",
        "current",
        "stale",
        "failed",
        "missing_voice",
    }
)
PRODUCE_FILTERS = frozenset(
    {
        "all",
        "needs_generation",
        "ready",
        "stale",
        "needs_review",
        "needs_listening",
        "current",
        "failed",
        "missing_voice",
    }
)
PRODUCE_PLAN_MODES = frozenset(
    {
        "missing_stale",
        "ready_only",
        "retry_failed",
        "regenerate_all",
        "selected",
    }
)


class ProduceAggregateError(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        detail: str,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.context = copy.deepcopy(dict(context or {}))

    def as_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.detail,
            "context": copy.deepcopy(self.context),
        }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _read_json(path: Path, *, required: bool = False) -> Any:
    if not path.exists():
        if required:
            raise ProduceAggregateError(
                status_code=409,
                code="produce_artifact_missing",
                detail=f"{path.name} is missing.",
                context={"filename": path.name},
            )
        return None
    if not path.is_file() or path.is_symlink():
        raise ProduceAggregateError(
            status_code=409,
            code="produce_artifact_invalid",
            detail=f"{path.name} is not a safe regular file.",
            context={"filename": path.name},
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProduceAggregateError(
            status_code=409,
            code="produce_artifact_invalid_json",
            detail=f"{path.name} is invalid JSON: {exc}",
            context={"filename": path.name},
        ) from exc


def _synthesis_config(
    config: Mapping[str, Any],
    *,
    voice_data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return synthesis_binding_config(
        _mapping(config.get("tts")),
        voice_data=voice_data,
    )


def _cast_label_index(cast: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in _list(cast.get("characters")):
        if not isinstance(value, Mapping):
            continue
        connection = _mapping(value.get("script_connection"))
        label = _text(connection.get("resolved_script_voice_label"))
        if label:
            result[_normalized(label)] = dict(value)
    return result


def _invalidated_chunk_ids(audio_validity: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    for item in _list(audio_validity.get("invalidated_chunks")):
        value = _mapping(item)
        chunk_id = value.get("chunk_id")
        if chunk_id is not None:
            result.add(str(chunk_id))
    return result


def _audio_url(
    relative_path: str | None,
    *,
    content_sha256: str | None = None,
) -> str | None:
    if not relative_path:
        return None
    value = str(relative_path).replace("\\", "/").lstrip("/")
    if not value.startswith("voicelines/"):
        return None
    digest = str(content_sha256 or "").strip().casefold()
    version = digest if len(digest) == 64 and all(
        character in "0123456789abcdef" for character in digest
    ) else None
    return f"/{value}?v={version}" if version else "/" + value


def _blocker(
    *,
    code: str,
    title: str,
    explanation: str,
    target_id: str,
    native_destination: str = "produce",
    blocking: bool = True,
) -> dict[str, Any]:
    return {
        "code": code,
        "title": title,
        "explanation": explanation,
        "native_destination": native_destination,
        "target_id": target_id,
        "blocking": bool(blocking),
    }


def _voice_context(
    *,
    speaker: str,
    cast_by_label: Mapping[str, Mapping[str, Any]],
    voice_config: Mapping[str, Any],
) -> dict[str, Any]:
    character = cast_by_label.get(_normalized(speaker))
    character_id = _text(_mapping(character).get("character_id"))
    character_name = _text(_mapping(character).get("display_name")) or speaker
    cast_voice = _mapping(_mapping(character).get("voice"))
    cast_valid = cast_voice.get("valid") is True if character else None
    try:
        resolution = resolve_voice_alias(speaker, dict(voice_config))
    except VoiceAliasError as exc:
        return {
            "valid": False,
            "error": str(exc),
            "resolved_speaker": None,
            "character_id": character_id,
            "character_name": character_name,
            "configuration_key": cast_voice.get("configuration_key"),
            "method": cast_voice.get("selected_production_method"),
        }
    resolved = _text(resolution.resolved_target) or speaker
    resolved_config = voice_config.get(resolved)
    config_valid = isinstance(resolved_config, Mapping)
    if cast_valid is False:
        config_valid = False
    return {
        "valid": config_valid,
        "error": (
            None
            if config_valid
            else "The mapped production Voice is missing or invalid."
        ),
        "resolved_speaker": resolved,
        "character_id": character_id,
        "character_name": character_name,
        "configuration_key": (
            cast_voice.get("configuration_key") or resolved
        ),
        "method": cast_voice.get("selected_production_method"),
    }


def _chunk_state(
    *,
    root: Path,
    chunk: Mapping[str, Any],
    index: int,
    expected_fingerprint: str | None,
    voice: Mapping[str, Any],
    invalidated_ids: set[str],
    file_hasher: Callable[[str | Path], str],
) -> dict[str, Any]:
    raw_id = chunk.get("id", index)
    chunk_id = f"chunk:{raw_id}"
    target_id = chunk_id
    status = _text(chunk.get("status")) or "pending"
    audio_state = _text(chunk.get("audio_state"))
    persisted_error = chunk.get("error")
    persisted_error_text = (
        persisted_error if isinstance(persisted_error, str) else None
    )
    persisted_code = chunk.get("error_code")
    persisted_code_text = persisted_code if isinstance(persisted_code, str) else None
    failure = public_audio_failure(persisted_error_text, persisted_code_text)
    error = failure.message if failure is not None else None
    error_code = failure.code if failure is not None else None
    audio_path_value = _text(chunk.get("audio_path"))
    stale_path_value = _text(chunk.get("stale_audio_path"))
    invalidated = str(raw_id) in invalidated_ids
    approved_lock = active_approved_audio_lock(chunk)
    regeneration_locked = approved_lock is not None
    recorded_provenance = _mapping(chunk.get("generation_provenance"))
    if not recorded_provenance and _text(chunk.get("cloud_provider")):
        recorded_provenance = {
            "schema_version": 1,
            "source": "generation",
            "recorded": True,
            "runtime": "fish-audio-cloud",
            "model_id": _text(chunk.get("cloud_model")) or "Fish Audio S2.1",
            "model_revision": None,
            "base_model_id": None,
            "voice_type": "clone",
            "voice_method": _text(chunk.get("cloud_provider")),
            "detail": _text(chunk.get("cloud_prompt_variant")),
        }
    inferred_provenance = _mapping(voice.get("generation_provenance"))
    generation_provenance = copy.deepcopy(
        recorded_provenance or inferred_provenance
    )
    blockers: list[dict[str, Any]] = []
    reason: str | None = None
    state: str
    actual_size: int | None = None
    actual_hash: str | None = None

    if voice.get("valid") is not True and not regeneration_locked:
        state = "missing_voice"
        reason = "voice_missing_or_invalid"
        character_id = _text(voice.get("character_id"))
        blocker_route = missing_voice_blocker_route(
            character_id=character_id,
            speaker=_text(chunk.get("speaker")),
        )
        blockers.append(
            _blocker(
                code="produce_voice_missing",
                title="Production Voice is not ready",
                explanation=str(
                    voice.get("error")
                    or "Assign or repair the mapped production Voice before generation."
                ),
                **blocker_route,
            )
        )
    elif status == "generating":
        state = "generating"
        reason = "generation_running"
    elif status == "error" or audio_state == "failed":
        state = "failed"
        reason = "generation_failed"
        blockers.append(
            _blocker(
                code="produce_generation_failed",
                title="Audio generation failed",
                explanation=(
                    error
                    or "Retry this chunk after inspecting the generation log."
                ),
                target_id=target_id,
            )
        )
    elif status != "done":
        if stale_path_value or audio_state == "stale" or invalidated:
            state = "stale"
            reason = "audio_invalidated"
        else:
            state = "ready"
            reason = "audio_not_generated"
    elif not audio_path_value:
        state = "failed"
        reason = "audio_path_missing"
        blockers.append(
            _blocker(
                code="produce_audio_path_missing",
                title="Generated audio path is missing",
                explanation="Regenerate this chunk; the completed record has no audio file path.",
                target_id=target_id,
            )
        )
    elif audio_state != "current":
        state = "stale"
        reason = "audio_not_current"
    elif chunk.get("audio_research_only") is True:
        state = "needs_review"
        reason = "experimental_prompt_research_only"
        blockers.append(
            _blocker(
                code="produce_experimental_prompt_research_only",
                title="Research-only prompt audio cannot be published",
                explanation=(
                    "Regenerate this chunk without the [prompt-route: ...] tag "
                    "before building or exporting the audiobook."
                ),
                target_id=target_id,
            )
        )
    elif expected_fingerprint is None or chunk.get("audio_fingerprint") != expected_fingerprint:
        state = "stale"
        reason = "audio_fingerprint_mismatch"
    else:
        try:
            audio_path = confined_audio_path(root, audio_path_value)
        except AudioArtifactError as exc:
            state = "failed"
            reason = exc.code
            blockers.append(
                _blocker(
                    code="produce_audio_path_invalid",
                    title="Audio path is invalid",
                    explanation="The saved audio path is outside the current project.",
                    target_id=target_id,
                )
            )
        else:
            if not audio_path.is_file():
                state = "failed"
                reason = "audio_file_missing"
                blockers.append(
                    _blocker(
                        code="produce_audio_file_missing",
                        title="Audio file is missing",
                        explanation="Regenerate this chunk because the recorded audio file is unavailable.",
                        target_id=target_id,
                    )
                )
            else:
                try:
                    actual_size = audio_path.stat().st_size
                    actual_hash = file_hasher(audio_path)
                except (OSError, AudioArtifactError) as exc:
                    state = "failed"
                    reason = "audio_file_unreadable"
                    blockers.append(
                        _blocker(
                            code="produce_audio_file_unreadable",
                            title="Audio file cannot be verified",
                            explanation=str(exc),
                            target_id=target_id,
                        )
                    )
                else:
                    recorded_hash = _text(chunk.get("audio_sha256"))
                    recorded_size = chunk.get("audio_size_bytes")
                    recorded_duration = chunk.get("audio_duration_ms")
                    recorded_format = _text(chunk.get("audio_format"))
                    if not recorded_hash or recorded_hash != actual_hash:
                        state = "failed"
                        reason = "audio_hash_mismatch"
                        blockers.append(
                            _blocker(
                                code="produce_audio_hash_invalid",
                                title="Audio file hash changed",
                                explanation="Regenerate this chunk; the saved audio bytes no longer match the recorded artifact.",
                                target_id=target_id,
                            )
                        )
                    elif (
                        not isinstance(recorded_size, int)
                        or recorded_size != actual_size
                        or not isinstance(recorded_duration, int)
                        or recorded_duration <= 0
                        or not recorded_format
                    ):
                        state = "failed"
                        reason = "audio_metadata_incomplete"
                        blockers.append(
                            _blocker(
                                code="produce_audio_metadata_invalid",
                                title="Audio artifact metadata is incomplete",
                                explanation="Regenerate this chunk so duration, size, format, and hash are recorded together.",
                                target_id=target_id,
                            )
                        )
                    elif (
                        recorded_duration / 1000.0
                        > generated_speech_duration_bounds(
                            str(chunk.get("text") or "")
                        )[1]
                    ):
                        state = "stale"
                        reason = "audio_duration_excessive"
                        blockers.append(
                            _blocker(
                                code="produce_audio_duration_excessive",
                                title="Generated audio is implausibly long",
                                explanation=(
                                    "Regenerate this chunk; its duration is not "
                                    "credible for the authored text."
                                ),
                                target_id=target_id,
                            )
                        )
                    elif chunk.get("review_required") is True or chunk.get("review_flag") is True:
                        state = "needs_review"
                        reason = "operator_review_required"
                    elif (
                        chunk.get("listening_required") is True
                        and _text(chunk.get("listening_state"))
                        not in {"approved", "complete", "passed"}
                    ):
                        state = "needs_listening"
                        reason = "listening_required"
                    else:
                        state = "current"
                        reason = None

    if state not in PRODUCE_STATES:
        raise ProduceAggregateError(
            status_code=500,
            code="produce_state_invalid",
            detail=f"Unsupported Produce state: {state}",
            context={"chunk_id": chunk_id},
        )

    playable = state in {"current", "needs_review", "needs_listening"}
    audio_url = (
        _audio_url(
            audio_path_value,
            content_sha256=_text(chunk.get("audio_sha256")),
        )
        if playable
        else None
    )
    regenerate_action = (
        None
        if regeneration_locked or state in {"generating", "missing_voice"}
        else {
            "id": "regenerate_chunk" if state != "ready" else "generate_chunk",
            "label": "Regenerate" if state != "ready" else "Generate",
            "endpoint": f"/api/chunks/{index}/generate",
            "method": "POST",
            "native_destination": "produce",
            "target_id": target_id,
        }
    )
    return {
        "chunk_id": chunk_id,
        "index": index,
        "source_chunk_id": raw_id,
        "speaker": _text(chunk.get("speaker")) or "UNKNOWN",
        "character_id": voice.get("character_id"),
        "character_name": voice.get("character_name"),
        "voice": {
            "valid": voice.get("valid") is True,
            "configuration_key": voice.get("configuration_key"),
            "resolved_speaker": voice.get("resolved_speaker"),
            "method": voice.get("method"),
        },
        "text": str(chunk.get("text") or ""),
        "text_excerpt": str(chunk.get("text") or "")[:240],
        "delivery_direction": str(chunk.get("instruct") or ""),
        "effective_delivery_direction": str(
            chunk.get("effective_instruct") or chunk.get("instruct") or ""
        ),
        "effective_fish_direction": str(
            chunk.get("effective_fish_instruct")
            or chunk.get("effective_instruct")
            or chunk.get("instruct")
            or ""
        ),
        "backend_render_plan": (
            {
                "plan_fingerprint": _text(
                    chunk.get("backend_render_plan_fingerprint")
                ),
                "qwen_instruction": str(
                    chunk.get("qwen_render_instruction") or ""
                ),
                "fish_direction": str(
                    chunk.get("fish_render_instruction") or ""
                ),
                "fish_inline_plan": copy.deepcopy(
                    chunk.get("fish_render_plan")
                )
                if isinstance(chunk.get("fish_render_plan"), Mapping)
                else None,
                "warnings": [
                    str(value)
                    for value in _list(
                        chunk.get("backend_render_plan_warnings")
                    )
                ],
                "applied_to_current_audio": (
                    chunk.get("backend_render_plan_applied") is not None
                ),
                "repair_recommended": bool(
                    chunk.get("backend_render_plan_fingerprint")
                    and chunk.get("backend_render_plan_applied") is None
                ),
            }
            if chunk.get("backend_render_plan_fingerprint")
            else None
        ),
        "fish_render_plan": copy.deepcopy(chunk.get("fish_render_plan"))
        if isinstance(chunk.get("fish_render_plan"), Mapping)
        else None,
        "spoken_continuity": (
            {
                **dict(chunk.get("spoken_continuity") or {}),
                "applied_to_current_audio": (
                    chunk.get("spoken_continuity_applied") is not None
                ),
                "repair_recommended": bool(
                    chunk.get("spoken_continuity")
                    and chunk.get("spoken_continuity_applied") is None
                ),
            }
            if chunk.get("spoken_continuity") is not None
            else None
        ),
        "pause_after_ms": effective_pause_after_ms(chunk),
        "generation_provenance": generation_provenance,
        "generated_at_utc": _text(chunk.get("generated_at_utc")),
        "duration_ms": chunk.get("audio_duration_ms"),
        "state": state,
        "reason": reason,
        "error": error,
        "error_code": error_code,
        "selected": False,
        "required_for_completion": True,
        "fish_generation": (
            {
                "provider": _text(chunk.get("cloud_provider")),
                "model": _text(chunk.get("cloud_model")),
                "style_route": _text(chunk.get("cloud_style_route")),
                "prompt_variant": _text(chunk.get("cloud_prompt_variant")),
                "candidate_count": chunk.get("cloud_candidate_count"),
                "text_validation_passed": chunk.get(
                    "cloud_text_validation_passed"
                ),
                "terminal_text_validation_passed": chunk.get(
                    "cloud_terminal_text_validation_passed"
                ),
                "word_error_rate": chunk.get("cloud_word_error_rate"),
                "identity_score": chunk.get("cloud_identity_score"),
                "delivery_score": chunk.get("cloud_delivery_score"),
                "instruction_delivery_score": chunk.get(
                    "cloud_instruction_delivery_score"
                ),
                "quality_score": chunk.get("cloud_quality_score"),
                "selection_score": chunk.get("cloud_selection_score"),
                "render_plan_fingerprint": _text(
                    chunk.get("cloud_render_plan_fingerprint")
                ),
                "inline_cue_count": chunk.get("cloud_inline_cue_count"),
                "route_mode": _text(chunk.get("fish_route_mode")),
                "route_reason": _text(chunk.get("fish_route_reason")),
                "hybrid_attempted": chunk.get("fish_hybrid_attempted"),
                "fallback_used": chunk.get("fish_hybrid_fallback_used"),
            }
            if _text(chunk.get("cloud_provider"))
            or chunk.get("fish_hybrid_attempted") is True
            else None
        ),
        "audio": {
            "available": bool(audio_url),
            "url": audio_url,
            "relative_path": audio_path_value if playable else None,
            "stale_audio_available": bool(stale_path_value or invalidated),
            "recorded_sha256": _text(chunk.get("audio_sha256")),
            "actual_sha256": actual_hash,
            "recorded_size_bytes": chunk.get("audio_size_bytes"),
            "actual_size_bytes": actual_size,
            "verification_level": "binding_and_hash",
        },
        "review": {
            "required": state == "needs_review",
            "listening_required": state == "needs_listening",
            "listening_state": _text(chunk.get("listening_state")),
        },
        "blockers": blockers,
        "regenerate_action": regenerate_action,
        "regeneration_lock": (
            {
                "locked": True,
                "code": "approved_adaptation_audio",
                "label": "Approved adaptation performance",
                "explanation": (
                    "This reviewed performance is locked against TTS regeneration. "
                    "Editing the authored text, speaker, or direction clears the lock."
                ),
                "promotion_id": approved_lock.get("promotion_id"),
                "candidate_id": approved_lock.get("candidate_id"),
                "direct_placement_tier": approved_lock.get(
                    "direct_placement_tier"
                ),
            }
            if approved_lock is not None
            else {"locked": False}
        ),
        "technical_details": {
            "status": status,
            "audio_state": audio_state,
            "expected_audio_fingerprint": expected_fingerprint,
            "recorded_audio_fingerprint": chunk.get("audio_fingerprint"),
        },
    }


def _process_public(process: Mapping[str, Any]) -> dict[str, Any]:
    queued_ids = [str(value) for value in _list(process.get("queued_chunk_ids"))]
    return {
        "running": bool(process.get("running")),
        "cancel_requested": bool(process.get("cancel")),
        "operation_id": process.get("operation_id"),
        "mode": process.get("mode"),
        "plan_fingerprint": process.get("plan_fingerprint"),
        "chunks_fingerprint": process.get("chunks_fingerprint"),
        "total_count": int(process.get("total_count") or 0),
        "completed_count": int(process.get("completed_count") or 0),
        "failed_count": int(process.get("failed_count") or 0),
        "cancelled_count": int(process.get("cancelled_count") or 0),
        "queued_chunk_ids": queued_ids[:200],
        "queued_chunk_ids_truncated": len(queued_ids) > 200,
        "worker_limit": process.get("worker_limit"),
        "started_at": process.get("started_at"),
        "finished_at": process.get("finished_at"),
        "last_error": process.get("last_error"),
        "logs": [str(value) for value in _list(process.get("logs"))[-200:]],
    }


def build_produce_aggregate(
    *,
    root_dir: str | Path,
    chunks: list[Any],
    voice_config: Mapping[str, Any],
    config: Mapping[str, Any],
    cast: Mapping[str, Any],
    audio_validity: Mapping[str, Any] | None = None,
    process: Mapping[str, Any] | None = None,
    selected_chunk_id: str | None = None,
    filter_key: str = "all",
    search: str | None = None,
    file_hasher: Callable[[str | Path], str] = sha256_file,
) -> dict[str, Any]:
    if filter_key not in PRODUCE_FILTERS:
        raise ProduceAggregateError(
            status_code=422,
            code="produce_filter_invalid",
            detail="The requested Produce filter is invalid.",
            context={"filter": filter_key},
        )
    root = Path(root_dir).expanduser().resolve()
    synthesis = _synthesis_config(config)
    tts_config = _mapping(config.get("tts"))
    tts_mode = _text(tts_config.get("mode")) or "external"
    use_mlx = (
        tts_mode == "local"
        and platform.system() == "Darwin"
        and platform.machine() == "arm64"
    )
    cast_by_label = _cast_label_index(cast)
    invalidated_ids = _invalidated_chunk_ids(_mapping(audio_validity))
    take_registry = audio_take_registry_view(root, chunks)
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    continuity_chunks = [
        dict(value) if isinstance(value, Mapping) else {}
        for value in chunks
    ]
    for index, value in enumerate(chunks):
        if not isinstance(value, Mapping):
            raise ProduceAggregateError(
                status_code=409,
                code="produce_chunk_invalid",
                detail=f"Chunk {index + 1} is not a JSON object.",
                context={"index": index},
            )
        if not _text(value.get("text")):
            continue
        chunk = dict(value)
        continuity = resolve_spoken_continuity(continuity_chunks, index)
        if continuity is not None or chunk.get("spoken_continuity_applied") is not None:
            chunk["spoken_continuity"] = continuity
        qwen_instruction = (
            chunk.get("qwen_render_instruction")
            or chunk.get("instruct", "")
        )
        fish_instruction = (
            chunk.get("fish_render_instruction")
            or qwen_instruction
        )
        chunk["effective_instruct"] = effective_delivery_instruction(
            qwen_instruction,
            continuity,
        )
        chunk["effective_fish_instruct"] = effective_delivery_instruction(
            fish_instruction,
            continuity,
        )
        chunk_id = f"chunk:{chunk.get('id', index)}"
        if chunk_id in seen_ids:
            raise ProduceAggregateError(
                status_code=409,
                code="produce_chunk_id_duplicate",
                detail="Produce requires unique current chunk IDs.",
                context={"chunk_id": chunk_id},
            )
        seen_ids.add(chunk_id)
        speaker = _text(chunk.get("speaker")) or "UNKNOWN"
        voice = _voice_context(
            speaker=speaker,
            cast_by_label=cast_by_label,
            voice_config=voice_config,
        )
        resolved_voice = voice_config.get(voice.get("resolved_speaker"), {})
        if isinstance(resolved_voice, Mapping):
            voice = {
                **voice,
                "generation_provenance": resolve_audio_generation_provenance(
                    resolved_voice,
                    mode=tts_mode,
                    use_mlx=use_mlx,
                    source="current_voice_config",
                    fish_model=_text(tts_config.get("fish_model")),
                    external_url=_text(tts_config.get("url")),
                ),
            }
        expected: str | None = None
        approved_expected = approved_audio_binding_fingerprint(chunk)
        if approved_expected is not None:
            expected = approved_expected
        elif voice.get("valid") is True:
            try:
                expected = audio_binding_fingerprint(
                    chunk=chunk,
                    resolved_speaker=str(voice["resolved_speaker"]),
                    voice_config=dict(voice_config),
                    synthesis_config=synthesis_config_with_generation_seed(
                        _synthesis_config(
                            config,
                            voice_data=(
                                resolved_voice
                                if isinstance(resolved_voice, Mapping)
                                else {}
                            ),
                        ),
                        chunk,
                    ),
                )
            except (AudioArtifactError, ValueError, TypeError):
                voice = {
                    **voice,
                    "valid": False,
                    "error": "The current Voice binding cannot be computed.",
                }
        row = _chunk_state(
                root=root,
                chunk=chunk,
                index=index,
                expected_fingerprint=expected,
                voice=voice,
                invalidated_ids=invalidated_ids,
                file_hasher=file_hasher,
            )
        take_entry = take_registry["chunks"].get(
            chunk_id,
            {
                "current_take_id": None,
                "take_ids": [],
            },
        )
        take_values = []
        for take_id in take_entry.get("take_ids") or []:
            public = public_audio_take(
                take_registry["takes"][take_id],
                registry_fingerprint=take_registry[
                    "registry_fingerprint"
                ],
            )
            recorded = _text(
                _mapping(public.get("generation")).get("audio_fingerprint")
            )
            public["promotable"] = bool(
                public.get("current")
                or (
                    expected
                    and recorded
                    and recorded == expected
                    and _mapping(public.get("audio")).get("available") is True
                )
            )
            public["promotion_blocked_reason"] = (
                None
                if public["promotable"]
                else "This Take belongs to an older text, Voice, pronunciation, route, or synthesis dependency."
            )
            take_values.append(public)
        row["takes"] = {
            "current_take_id": take_entry.get("current_take_id"),
            "take_count": len(take_values),
            "items": take_values,
            "registry_fingerprint": take_registry[
                "registry_fingerprint"
            ],
        }
        pinned_take = next(
            (
                item
                for item in take_values
                if item.get("final_listen_pinned") is True
            ),
            None,
        )
        current_take = next(
            (item for item in take_values if item.get("current") is True),
            None,
        )
        row["final_listen"] = {
            "current_take_id": take_entry.get("current_take_id"),
            "pinned_take_id": (
                pinned_take.get("take_id")
                if isinstance(pinned_take, Mapping)
                else None
            ),
            "current_take_pinned": bool(
                isinstance(current_take, Mapping)
                and isinstance(pinned_take, Mapping)
                and current_take.get("take_id") == pinned_take.get("take_id")
            ),
            "current_take_review_state": (
                _text(_mapping(current_take.get("review")).get("state"))
                if isinstance(current_take, Mapping)
                else None
            ),
            "can_process": bool(
                isinstance(current_take, Mapping)
                and _mapping(current_take.get("audio")).get("available") is True
                and row.get("state") == "current"
            ),
        }
        rows.append(row)

    by_id = {row["chunk_id"]: row for row in rows}
    if selected_chunk_id is not None and selected_chunk_id not in by_id:
        raise ProduceAggregateError(
            status_code=404,
            code="produce_chunk_not_found",
            detail="The requested Produce chunk was not found.",
            context={"chunk_id": selected_chunk_id},
        )
    selected = by_id.get(selected_chunk_id) if selected_chunk_id else None
    if selected is not None:
        selected["selected"] = True

    source_order_fingerprint = chapter_source_order_fingerprint(
        [
            dict(value) if isinstance(value, Mapping) else {}
            for value in chunks
        ]
    )
    final_listen_chapters = build_final_listen_chapters(
        rows,
        config=config,
        mode="smart",
    )
    for row in rows:
        row["final_listen"]["source_order_fingerprint"] = (
            source_order_fingerprint
        )
    if selected is not None:
        transition = final_listen_transition_context(
            rows,
            selected_chunk_id=selected["chunk_id"],
            config=config,
            mode="smart",
        )
        if transition is not None:
            transition["source_order_fingerprint"] = source_order_fingerprint
        selected["final_listen"]["transition"] = transition

    counts = {state: 0 for state in sorted(PRODUCE_STATES)}
    for row in rows:
        counts[row["state"]] += 1
    needs_generation = sum(
        counts[state] for state in ("ready", "stale")
    )
    review_count = counts["needs_review"] + counts["needs_listening"]
    process_value = _process_public(_mapping(process))
    blocker_count = sum(
        1
        for row in rows
        for blocker in row["blockers"]
        if blocker.get("blocking") is True
    )
    if process_value["running"]:
        state = "running"
    elif counts["missing_voice"]:
        state = "blocked"
    elif counts["failed"]:
        state = "failed"
    elif needs_generation or review_count:
        state = "ready"
    elif rows and counts["current"] == len(rows):
        state = "complete"
    else:
        state = "not_started"

    def visible(row: Mapping[str, Any]) -> bool:
        query = _normalized(search)
        if query:
            searchable = " ".join(
                [
                    str(row.get("speaker") or ""),
                    str(row.get("character_name") or ""),
                    str(row.get("text") or ""),
                    str(row.get("delivery_direction") or ""),
                ]
            )
            if query not in _normalized(searchable):
                return False
        row_state = row.get("state")
        if filter_key == "needs_generation":
            return row_state in {"ready", "stale"}
        if filter_key == "ready":
            return row_state == "ready"
        if filter_key == "stale":
            return row_state == "stale"
        if filter_key == "needs_review":
            return row_state in {"needs_review", "needs_listening"}
        if filter_key == "needs_listening":
            return row_state == "needs_listening"
        if filter_key == "current":
            return row_state == "current"
        if filter_key == "failed":
            return row_state == "failed"
        if filter_key == "missing_voice":
            return row_state == "missing_voice"
        return True

    visible_rows = [row for row in rows if visible(row)]
    primary_action = None
    if process_value["running"]:
        primary_action = {
            "id": "cancel_produce_generation",
            "label": "Cancel generation",
            "endpoint": "/api/produce/cancel",
            "method": "POST",
        }
    elif needs_generation:
        primary_action = {
            "id": "generate_missing_stale_audio",
            "label": "Generate missing and stale audio",
            "endpoint": "/api/produce/generate",
            "method": "POST",
            "mode": "missing_stale",
        }
    elif counts["failed"]:
        primary_action = {
            "id": "retry_failed_audio",
            "label": "Retry failed audio",
            "endpoint": "/api/produce/retry-failed",
            "method": "POST",
            "mode": "retry_failed",
        }
    elif review_count:
        primary_action = {
            "id": "review_produce_audio",
            "label": "Review audio",
            "native_destination": "produce",
            "target_id": "produce:review",
        }

    aggregate_fingerprint = fingerprint_value(
        {
            "chunks": chunks,
            "voice_config": dict(voice_config),
            "synthesis": synthesis,
            "audio_validity": dict(_mapping(audio_validity)),
            "takes": take_registry["registry_fingerprint"],
        }
    )
    final_listen_fingerprint = fingerprint_value(
        {
            "source_order_fingerprint": source_order_fingerprint,
            "take_registry_fingerprint": take_registry[
                "registry_fingerprint"
            ],
            "chapters": final_listen_chapters,
            "pauses": [
                {
                    "chunk_id": row["chunk_id"],
                    "pause_after_ms": row.get("pause_after_ms"),
                }
                for row in rows
            ],
        }
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "state": state,
        "summary": {
            "required_chunk_count": len(rows),
            "current_count": counts["current"],
            "needs_generation_count": needs_generation,
            "needs_review_count": review_count,
            "failed_count": counts["failed"],
            "missing_voice_count": counts["missing_voice"],
            "blocker_count": blocker_count,
            "take_count": sum(
                int(row.get("takes", {}).get("take_count") or 0)
                for row in rows
            ),
            "complete": state == "complete",
        },
        "counts": counts,
        "filters": {
            "available": sorted(PRODUCE_FILTERS),
            "active": filter_key,
            "search": search,
        },
        "chunks": visible_rows,
        "all_chunk_count": len(rows),
        "visible_chunk_count": len(visible_rows),
        "selected_chunk_id": selected_chunk_id,
        "selected_chunk": copy.deepcopy(selected),
        "selection_visible": bool(
            selected_chunk_id
            and any(row["chunk_id"] == selected_chunk_id for row in visible_rows)
        ),
        "process": process_value,
        "primary_action": primary_action,
        "secondary_actions": [
            {
                "id": "regenerate_all_audio",
                "label": "Regenerate all audio",
                "endpoint": "/api/produce/generate",
                "method": "POST",
                "mode": "regenerate_all",
                "destructive": True,
            }
        ],
        "final_listen": {
            "schema_version": 1,
            "chapter_mode": "smart",
            "chapters": final_listen_chapters,
            "chapter_count": len(final_listen_chapters),
            "source_order_fingerprint": source_order_fingerprint,
            "current_take_count": sum(
                1
                for row in rows
                if row.get("final_listen", {}).get("current_take_id")
            ),
            "pinned_current_take_count": sum(
                1
                for row in rows
                if row.get("final_listen", {}).get(
                    "current_take_pinned"
                )
            ),
            "fingerprint": final_listen_fingerprint,
        },
        "fingerprints": {
            "aggregate": aggregate_fingerprint,
            "chunks": fingerprint_value(chunks),
            "voice_config": fingerprint_value(dict(voice_config)),
            "synthesis": fingerprint_value(synthesis),
            "audio_validity": fingerprint_value(dict(_mapping(audio_validity))),
            "takes": take_registry["registry_fingerprint"],
            "final_listen": final_listen_fingerprint,
        },
        "technical_details": {
            "project_path": str(root),
            "audio_verification": "binding_and_hash",
        },
    }


def inspect_produce_project(
    *,
    root_dir: str | Path,
    config_path: str | Path | None = None,
    selected_chunk_id: str | None = None,
    filter_key: str = "all",
    search: str | None = None,
    process: Mapping[str, Any] | None = None,
    cast: Mapping[str, Any] | None = None,
    file_hasher: Callable[[str | Path], str] = sha256_file,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    chunks = _read_json(root / "chunks.json", required=True)
    if not isinstance(chunks, list):
        raise ProduceAggregateError(
            status_code=409,
            code="produce_chunks_invalid",
            detail="chunks.json must contain a JSON array.",
        )
    voice_config = _read_json(root / "voice_config.json") or {}
    if not isinstance(voice_config, Mapping):
        raise ProduceAggregateError(
            status_code=409,
            code="produce_voice_config_invalid",
            detail="voice_config.json must contain a JSON object.",
        )
    effective_config_path = (
        Path(config_path).expanduser().resolve()
        if config_path is not None
        else root / "app" / "config.json"
    )
    config = _read_json(effective_config_path) or {}
    if not isinstance(config, Mapping):
        config = {}
    audio_validity = _read_json(root / "audio_validity.json") or {}
    if not isinstance(audio_validity, Mapping):
        audio_validity = {}
    cast_value = (
        dict(cast)
        if isinstance(cast, Mapping)
        else inspect_cast_project(root_dir=root)
    )
    return build_produce_aggregate(
        root_dir=root,
        chunks=chunks,
        voice_config=voice_config,
        config=config,
        cast=cast_value,
        audio_validity=audio_validity,
        process=process,
        selected_chunk_id=selected_chunk_id,
        filter_key=filter_key,
        search=search,
        file_hasher=file_hasher,
    )


def build_produce_generation_plan(
    aggregate: Mapping[str, Any],
    *,
    mode: str = "missing_stale",
    selected_chunk_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    if mode not in PRODUCE_PLAN_MODES:
        raise ProduceAggregateError(
            status_code=422,
            code="produce_plan_mode_invalid",
            detail="The requested Produce generation mode is invalid.",
            context={"mode": mode},
        )
    rows = [
        item
        for item in _list(aggregate.get("chunks"))
        if isinstance(item, Mapping)
    ]
    by_id = {str(item.get("chunk_id")): item for item in rows}
    requested = {
        str(value) for value in (selected_chunk_ids or []) if _text(value)
    }
    if mode == "selected":
        if not requested:
            raise ProduceAggregateError(
                status_code=422,
                code="produce_selected_chunks_required",
                detail="Choose at least one current Produce chunk.",
            )
        unknown = sorted(requested - set(by_id))
        if unknown:
            raise ProduceAggregateError(
                status_code=409,
                code="produce_selected_chunk_stale",
                detail="One or more selected chunks no longer exist.",
                context={"chunk_ids": unknown},
            )

    if mode == "missing_stale":
        eligible_states = {"ready", "stale"}
    elif mode == "ready_only":
        eligible_states = {"ready"}
    elif mode == "retry_failed":
        eligible_states = {"failed"}
    elif mode == "regenerate_all":
        eligible_states = {
            "ready",
            "stale",
            "failed",
            "needs_review",
            "needs_listening",
            "current",
        }
    else:
        eligible_states = {
            "ready",
            "stale",
            "failed",
            "needs_review",
            "needs_listening",
            "current",
        }

    selected_rows = []
    locked_rows = []
    for row in rows:
        chunk_id = str(row.get("chunk_id"))
        if mode == "selected" and chunk_id not in requested:
            continue
        if row.get("state") not in eligible_states:
            continue
        if _mapping(row.get("regeneration_lock")).get("locked") is True:
            locked_rows.append(row)
            continue
        if _mapping(row.get("voice")).get("valid") is not True:
            continue
        if row.get("state") == "generating":
            continue
        selected_rows.append(row)

    blockers: list[dict[str, Any]] = []
    if _mapping(aggregate.get("process")).get("running") is True:
        blockers.append(
            _blocker(
                code="produce_generation_already_running",
                title="Audio generation is already running",
                explanation="Cancel or finish the current queue before starting another.",
                target_id="produce:queue",
            )
        )
    blocked_voice_count = sum(
        item.get("state") == "missing_voice" for item in rows
    )
    if blocked_voice_count:
        blockers.append(
            _blocker(
                code="produce_voice_blockers_remain",
                title="Some chunks still need a production Voice",
                explanation=(
                    f"{blocked_voice_count} chunk"
                    + (" is" if blocked_voice_count == 1 else "s are")
                    + " excluded from generation until Cast is repaired."
                ),
                native_destination="cast",
                target_id="cast:needs-attention",
                blocking=False,
            )
        )

    selected_rows.sort(key=lambda item: int(item.get("index") or 0))
    chunk_ids = [str(item["chunk_id"]) for item in selected_rows]
    indices = [int(item["index"]) for item in selected_rows]
    chunks_fingerprint = _mapping(aggregate.get("fingerprints")).get("chunks")
    seed = {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "chunk_ids": chunk_ids,
        "indices": indices,
        "chunks_fingerprint": chunks_fingerprint,
        "aggregate_fingerprint": _mapping(aggregate.get("fingerprints")).get(
            "aggregate"
        ),
    }
    plan_fingerprint = fingerprint_value(seed)
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "destructive": mode == "regenerate_all",
        "plan_fingerprint": plan_fingerprint,
        "chunks_fingerprint": chunks_fingerprint,
        "chunk_ids": chunk_ids,
        "indices": indices,
        "total_count": len(indices),
        "preserved_current_count": sum(
            item.get("state") == "current" and item not in selected_rows
            for item in rows
        ),
        "preserved_locked_count": len(locked_rows),
        "state_counts": {
            state: sum(item.get("state") == state for item in selected_rows)
            for state in sorted(PRODUCE_STATES)
        },
        "blockers": blockers,
        "safe_to_execute": bool(indices)
        and not any(item.get("blocking") is True for item in blockers),
        "empty_reason": (
            None
            if indices
            else "No current chunks match this generation mode."
        ),
        "worker_limit": _mapping(aggregate.get("process")).get("worker_limit"),
    }
