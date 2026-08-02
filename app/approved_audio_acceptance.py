from __future__ import annotations

import copy
import hashlib
import json
import re
import stat
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from approved_audio import (
    active_approved_audio_lock,
    approved_audio_content_fingerprint,
    clear_approved_audio_fields,
)
from audio_artifacts import AudioArtifactError, plan_verified_audio_install, sha256_file
from audio_crash_reconciliation import apply_audio_transition, audio_project_lock
from audio_takes import (
    AUDIO_TAKE_REGISTRY_FILENAME,
    AudioTakeError,
    audio_take_registry_lock,
    build_take_record,
    chunk_key,
    load_registry,
    normalize_registry,
    take_directory,
    take_filename_base,
)
from generation_state import fingerprint_value
from synthesis_windows import synthesis_receipt_reset_fields
from voice_aliases import VoiceAliasError, resolve_voice_alias


ACCEPTANCE_SCHEMA_VERSION = 1
ACCEPTANCE_HISTORY_DIRNAME = "approved_audio_acceptance_history"
_SAFE_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class ApprovedAudioAcceptanceError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.context = copy.deepcopy(dict(context or {}))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_chunks(root: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads((root / "chunks.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_chunks_invalid",
            f"Project chunks could not be read: {exc}",
        ) from exc
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_chunks_invalid",
            "Project chunks must be a JSON array of objects.",
        )
    return value


def _effective_voice_configuration(
    root: Path,
    speaker: str,
) -> tuple[str, dict[str, Any], str]:
    path = root / "voice_config.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_voice_configuration_invalid",
            f"Voice configuration could not be read: {exc}",
        ) from exc
    if not isinstance(value, dict):
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_voice_configuration_invalid",
            "Voice configuration must be a JSON object.",
        )
    try:
        resolution = resolve_voice_alias(speaker, value)
    except VoiceAliasError as exc:
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_voice_configuration_invalid",
            str(exc),
            context=exc.detail(),
        ) from exc
    configuration = copy.deepcopy(dict(value.get(resolution.resolved_target, {})))
    return (
        resolution.resolved_target,
        configuration,
        fingerprint_value(configuration),
    )


def _find_chunk(
    chunks: list[dict[str, Any]], chunk_key_value: str
) -> tuple[int, dict[str, Any]]:
    matches = [
        (index, chunk)
        for index, chunk in enumerate(chunks)
        if chunk_key(chunk, index) == chunk_key_value
    ]
    if len(matches) != 1:
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_chunk_ambiguous",
            "The stable chunk identity must resolve to exactly one chunk.",
            context={"chunk_key": chunk_key_value, "match_count": len(matches)},
        )
    return matches[0]


def _regular_source(root: Path, relative_path: str) -> Path:
    value = Path(relative_path)
    if (
        not relative_path
        or value.is_absolute()
        or ".." in value.parts
        or not value.parts
        or value.parts[0] != "voicelines"
    ):
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_source_unsafe",
            "Current approved audio must be project-confined under voicelines.",
        )
    if value.suffix.casefold() not in {".mp3", ".wav", ".wave"}:
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_format_unsupported",
            "Current approved audio must be an MP3 or WAV file.",
        )
    current = root
    for part in value.parts:
        current = current / part
        if current.is_symlink():
            raise ApprovedAudioAcceptanceError(
                "approved_audio_acceptance_source_symlink",
                "Current approved audio must not traverse a symbolic link.",
            )
    try:
        mode = current.lstat().st_mode
    except FileNotFoundError as exc:
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_source_missing",
            "Current approved audio is missing.",
        ) from exc
    if not stat.S_ISREG(mode):
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_source_not_regular",
            "Current approved audio must be a regular file.",
        )
    return current


def _load_registry(root: Path) -> dict[str, Any]:
    try:
        return load_registry(root)
    except AudioTakeError as exc:
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_registry_invalid",
            f"Audio Takes could not be read: {exc}",
        ) from exc


def _validated_state(
    root: Path,
    *,
    chunk_key_value: str,
) -> dict[str, Any]:
    chunks = _read_chunks(root)
    index, chunk = _find_chunk(chunks, chunk_key_value)
    if chunk.get("status") == "generating":
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_generation_active",
            "Approved audio cannot be accepted while its chunk is generating.",
        )
    lock = active_approved_audio_lock(chunk)
    if lock is None:
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_lock_changed",
            "The active approved-audio lock is missing or changed.",
        )
    origin = chunk.get("approved_audio_origin")
    if not isinstance(origin, Mapping):
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_origin_missing",
            "Approved audio origin lineage is missing.",
        )
    relative_path = str(chunk.get("audio_path") or "").strip()
    if chunk.get("audio_state") != "current" or not relative_path:
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_audio_not_current",
            "The approved chunk no longer has non-generating current audio.",
        )
    expected_sha = str(chunk.get("audio_sha256") or "").strip().casefold()
    lock_sha = str(lock.get("source_audio_sha256") or "").strip().casefold()
    origin_sha = str(origin.get("source_audio_sha256") or "").strip().casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha) or not (
        expected_sha == lock_sha == origin_sha
    ):
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_source_identity_changed",
            "Current audio and approved lineage hashes no longer match.",
        )
    binding = str(lock.get("binding_fingerprint") or "")
    content = str(lock.get("content_fingerprint") or "")
    if (
        binding != str(chunk.get("audio_fingerprint") or "")
        or content != approved_audio_content_fingerprint(chunk)
    ):
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_binding_changed",
            "Current audio no longer matches the approved lock binding.",
        )
    source = _regular_source(root, relative_path)
    if sha256_file(source) != expected_sha:
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_source_hash_mismatch",
            "Current approved audio bytes no longer match their reviewed hash.",
        )
    registry = _load_registry(root)
    resolved_speaker, voice_configuration, voice_configuration_fingerprint = (
        _effective_voice_configuration(root, str(chunk.get("speaker") or ""))
    )
    chunks_fingerprint = fingerprint_value(chunks)
    state_seed = {
        "schema_version": ACCEPTANCE_SCHEMA_VERSION,
        "operation": "materialize_and_detach_approved_audio",
        "chunk_key": chunk_key_value,
        "chunk_index": index,
        "chunks_fingerprint": chunks_fingerprint,
        "registry_fingerprint": registry["registry_fingerprint"],
        "content_fingerprint": content,
        "binding_fingerprint": binding,
        "resolved_speaker": resolved_speaker,
        "voice_configuration": voice_configuration,
        "voice_configuration_fingerprint": voice_configuration_fingerprint,
        "current_audio_path": relative_path,
        "current_audio_sha256": expected_sha,
        "approved_audio_lock": copy.deepcopy(lock),
        "approved_audio_origin": copy.deepcopy(dict(origin)),
    }
    state_fingerprint = fingerprint_value(state_seed)
    take_id = f"take_accept_{state_fingerprint[:24]}"
    action = {
        **state_seed,
        "take_id": take_id,
        "destination_directory": take_directory(root, chunk_key_value)
        .relative_to(root)
        .as_posix(),
    }
    return {
        "chunks": chunks,
        "chunk": chunk,
        "index": index,
        "lock": lock,
        "origin": copy.deepcopy(dict(origin)),
        "source": source,
        "registry": registry,
        "action": action,
        "action_fingerprint": fingerprint_value(action),
    }


def _preview_from_state(state: Mapping[str, Any]) -> dict[str, Any]:
    action = state["action"]
    chunk = state["chunk"]
    return {
        "schema_version": ACCEPTANCE_SCHEMA_VERSION,
        "operation": action["operation"],
        "chunk_key": action["chunk_key"],
        "chunk_index": action["chunk_index"],
        "authored": {
            "speaker": chunk.get("speaker"),
            "text": chunk.get("text"),
            "direction": chunk.get("instruct"),
        },
        "source": {
            "relative_path": action["current_audio_path"],
            "sha256": action["current_audio_sha256"],
            "format": Path(action["current_audio_path"]).suffix.casefold().lstrip("."),
        },
        "proposed_take_id": action["take_id"],
        "destination_directory": action["destination_directory"],
        "effects": {
            "copy_exact_bytes": True,
            "take_current": False,
            "clear_current_selection": True,
            "clear_approved_lock": True,
            "next_audio_state": "stale",
            "regeneration_policy": "ordinary_retry_and_regeneration",
        },
        "approved_audio_lock": copy.deepcopy(action["approved_audio_lock"]),
        "approved_audio_origin": copy.deepcopy(action["approved_audio_origin"]),
        "voice": {
            "resolved_speaker": action["resolved_speaker"],
            "configuration": copy.deepcopy(action["voice_configuration"]),
        },
        "chunks_fingerprint": action["chunks_fingerprint"],
        "registry_fingerprint": action["registry_fingerprint"],
        "voice_configuration_fingerprint": action[
            "voice_configuration_fingerprint"
        ],
        "action_fingerprint": state["action_fingerprint"],
    }


def preview_approved_audio_acceptance(
    *,
    project_root: str | Path,
    chunks_lock: AbstractContextManager[bool],
    chunk_key_value: str,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    with audio_project_lock(root), chunks_lock, audio_take_registry_lock(root):
        return _preview_from_state(
            _validated_state(root, chunk_key_value=chunk_key_value)
        )


def _record_fingerprint(record: Mapping[str, Any]) -> str:
    return fingerprint_value(
        {key: copy.deepcopy(value) for key, value in record.items() if key != "record_fingerprint"}
    )


def _planned_registry(
    registry: Mapping[str, Any],
    *,
    chunk_key_value: str,
    record: Mapping[str, Any],
) -> dict[str, Any]:
    planned = copy.deepcopy(dict(registry))
    take_id = str(record["take_id"])
    if take_id in planned["takes"]:
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_take_exists",
            "The proposed immutable Take already exists without this receipt.",
        )
    entry = planned["chunks"].setdefault(
        chunk_key_value,
        {"chunk_key": chunk_key_value, "current_take_id": None, "take_ids": []},
    )
    for existing_id in entry["take_ids"]:
        planned["takes"][existing_id]["current"] = False
        planned["takes"][existing_id]["record_fingerprint"] = _record_fingerprint(
            planned["takes"][existing_id]
        )
    archived = copy.deepcopy(dict(record))
    archived["current"] = False
    archived["legacy"] = False
    archived["record_fingerprint"] = _record_fingerprint(archived)
    planned["takes"][take_id] = archived
    entry["take_ids"].append(take_id)
    entry["current_take_id"] = None
    planned["updated_at_utc"] = _utc_now()
    planned["registry_fingerprint"] = None
    return normalize_registry(planned)


def _receipt_path(root: Path, idempotency_key: str) -> Path:
    return root / ACCEPTANCE_HISTORY_DIRNAME / idempotency_key / "receipt.json"


def _read_receipt(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_receipt_invalid",
            "The existing acceptance receipt is unreadable.",
        ) from exc
    if not isinstance(value, dict):
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_receipt_invalid",
            "The existing acceptance receipt is malformed.",
        )
    expected = fingerprint_value(
        {key: copy.deepcopy(item) for key, item in value.items() if key != "receipt_fingerprint"}
    )
    if value.get("receipt_fingerprint") != expected:
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_receipt_invalid",
            "The existing acceptance receipt fingerprint is invalid.",
        )
    return value


def _verify_after_state(root: Path, receipt: Mapping[str, Any]) -> None:
    chunks = _read_chunks(root)
    registry = _load_registry(root)
    after = receipt.get("after")
    take = receipt.get("take")
    if not isinstance(after, Mapping) or not isinstance(take, Mapping):
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_receipt_invalid",
            "The acceptance receipt has no complete after-state.",
        )
    take_artifact = take.get("artifact")
    if not isinstance(take_artifact, Mapping):
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_receipt_invalid",
            "The acceptance receipt has no valid Take artifact.",
        )
    relative = str(take_artifact.get("relative_path") or "")
    expected_sha = str(take_artifact.get("sha256") or "")
    try:
        artifact = _regular_source(root, relative)
    except ApprovedAudioAcceptanceError as exc:
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_after_state_changed",
            "The accepted immutable Take artifact no longer verifies.",
        ) from exc
    if (
        fingerprint_value(chunks) != after.get("chunks_fingerprint")
        or registry.get("registry_fingerprint") != after.get("registry_fingerprint")
        or sha256_file(artifact) != expected_sha
        or registry.get("takes", {}).get(take.get("take_id")) != take
    ):
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_after_state_changed",
            "The receipt-backed acceptance after-state no longer verifies.",
        )


def confirm_approved_audio_acceptance(
    *,
    project_root: str | Path,
    chunks_lock: AbstractContextManager[bool],
    chunk_index_value: int,
    chunk_key_value: str,
    action_fingerprint: str,
    chunks_fingerprint: str,
    registry_fingerprint: str,
    voice_configuration_fingerprint: str,
    idempotency_key: str,
    confirm_acceptance: bool,
    crash_point: str | None = None,
) -> dict[str, Any]:
    if confirm_acceptance is not True:
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_confirmation_required",
            "Materialize-and-detach acceptance requires explicit confirmation.",
        )
    if not _SAFE_IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        raise ApprovedAudioAcceptanceError(
            "approved_audio_acceptance_idempotency_invalid",
            "The idempotency key must be one safe opaque component.",
        )
    root = Path(project_root).expanduser().resolve()
    receipt_path = _receipt_path(root, idempotency_key)
    with audio_project_lock(root), chunks_lock, audio_take_registry_lock(root):
        if receipt_path.is_file() and not receipt_path.is_symlink():
            receipt = _read_receipt(receipt_path)
            if (
                receipt.get("chunk_key") != chunk_key_value
                or receipt.get("chunk_index") != chunk_index_value
                or receipt.get("action_fingerprint") != action_fingerprint
                or receipt.get("voice_configuration_fingerprint")
                != voice_configuration_fingerprint
                or receipt.get("idempotency_key") != idempotency_key
            ):
                raise ApprovedAudioAcceptanceError(
                    "approved_audio_acceptance_idempotency_conflict",
                    "The idempotency key belongs to a different acceptance action.",
                )
            _verify_after_state(root, receipt)
            return receipt
        if receipt_path.exists():
            raise ApprovedAudioAcceptanceError(
                "approved_audio_acceptance_receipt_invalid",
                "The acceptance receipt path is not a regular file.",
            )
        current_chunks = _read_chunks(root)
        if fingerprint_value(current_chunks) != chunks_fingerprint:
            raise ApprovedAudioAcceptanceError(
                "approved_audio_acceptance_chunks_changed",
                "Project chunks changed after the acceptance preview.",
            )
        current_registry = _load_registry(root)
        if current_registry["registry_fingerprint"] != registry_fingerprint:
            raise ApprovedAudioAcceptanceError(
                "approved_audio_acceptance_registry_changed",
                "Audio Takes changed after the acceptance preview.",
            )
        state = _validated_state(root, chunk_key_value=chunk_key_value)
        action = state["action"]
        if action["chunk_index"] != chunk_index_value:
            raise ApprovedAudioAcceptanceError(
                "approved_audio_acceptance_chunk_changed",
                "The target chunk index no longer matches its stable identity.",
            )
        if action["chunks_fingerprint"] != chunks_fingerprint:
            raise ApprovedAudioAcceptanceError(
                "approved_audio_acceptance_chunks_changed",
                "Project chunks changed after the acceptance preview.",
            )
        if action["registry_fingerprint"] != registry_fingerprint:
            raise ApprovedAudioAcceptanceError(
                "approved_audio_acceptance_registry_changed",
                "Audio Takes changed after the acceptance preview.",
            )
        if (
            action["voice_configuration_fingerprint"]
            != voice_configuration_fingerprint
        ):
            raise ApprovedAudioAcceptanceError(
                "approved_audio_acceptance_voice_configuration_changed",
                "The Voice configuration changed after the acceptance preview.",
                context={
                    "chunk_key": chunk_key_value,
                    "current_voice_configuration_fingerprint": action[
                        "voice_configuration_fingerprint"
                    ],
                },
            )
        if state["action_fingerprint"] != action_fingerprint:
            raise ApprovedAudioAcceptanceError(
                "approved_audio_acceptance_action_changed",
                "The approved-audio acceptance action changed after preview.",
            )
        take_id = action["take_id"]
        try:
            install = plan_verified_audio_install(
                root_dir=root,
                voicelines_dir=take_directory(root, chunk_key_value),
                source_audio_path=state["source"],
                filename_base=take_filename_base(take_id),
                binding_fingerprint=action["binding_fingerprint"],
                expected_sha256=action["current_audio_sha256"],
                text=str(state["chunk"].get("text") or ""),
            )
        except (AudioArtifactError, AudioTakeError) as exc:
            raise ApprovedAudioAcceptanceError(
                getattr(exc, "code", "approved_audio_acceptance_audio_invalid"),
                str(exc),
            ) from exc
        artifact = install["artifact"]
        destination = root / artifact["audio_path"]
        if destination.exists() or destination.is_symlink():
            raise ApprovedAudioAcceptanceError(
                "approved_audio_acceptance_artifact_exists",
                "The proposed immutable Take path already exists without a receipt.",
            )
        created_at = _utc_now()
        chunk = state["chunk"]
        record = build_take_record(
            take_id=take_id,
            chunk_key_value=chunk_key_value,
            chunk_index=state["index"],
            kind="raw",
            source_take_id=None,
            root_take_id=take_id,
            artifact={
                "relative_path": artifact["audio_path"],
                "sha256": artifact["audio_sha256"],
                "size_bytes": artifact["audio_size_bytes"],
                "duration_ms": artifact["audio_duration_ms"],
                "format": artifact["audio_format"],
                "sample_rate": artifact.get("audio_sample_rate"),
                "sample_count": artifact.get("audio_sample_count"),
                "channels": artifact.get("audio_channels"),
                "installed_sample_width": artifact.get("audio_sample_width"),
            },
            authored={
                "text": str(chunk.get("text") or ""),
                "text_fingerprint": fingerprint_value(str(chunk.get("text") or "")),
                "speaker": str(chunk.get("speaker") or ""),
                "resolved_speaker": action["resolved_speaker"],
                "direction": str(chunk.get("instruct") or ""),
                "effective_direction": str(chunk.get("instruct") or ""),
                "pause_after_ms": chunk.get("pause_after"),
            },
            voice={
                "resolved_speaker": action["resolved_speaker"],
                "configuration": copy.deepcopy(action["voice_configuration"]),
                "configuration_fingerprint": action[
                    "voice_configuration_fingerprint"
                ],
                "binding_fingerprint": action["binding_fingerprint"],
                "approved_audio_lock": copy.deepcopy(state["lock"]),
                "approved_audio_origin": copy.deepcopy(state["origin"]),
            },
            generation={
                "audio_fingerprint": action["binding_fingerprint"],
                "request_id": f"approved-audio-acceptance-{idempotency_key}",
                "request_fingerprint": action_fingerprint,
                "provenance": {
                    "operation": action["operation"],
                    "approved_audio_lock": copy.deepcopy(state["lock"]),
                    "approved_audio_origin": copy.deepcopy(state["origin"]),
                    "prior_generation_provenance": copy.deepcopy(
                        chunk.get("generation_provenance")
                    ),
                },
                "source_audio_path": action["current_audio_path"],
                "source_audio_sha256": action["current_audio_sha256"],
            },
            synthesis={
                "source_kind": "approved_adaptation_performance",
                "segment_count": 1,
                "original_sample_count": artifact.get("audio_sample_count"),
                "sample_rate": artifact.get("audio_sample_rate"),
            },
            review={
                "state": "approved",
                "review_required": False,
                "listening_required": False,
                "promotion_id": state["lock"].get("promotion_id"),
                "candidate_id": state["lock"].get("candidate_id"),
            },
            processing={"operation": action["operation"]},
            created_at_utc=created_at,
        )
        planned_registry = _planned_registry(
            state["registry"],
            chunk_key_value=chunk_key_value,
            record=record,
        )
        archived = copy.deepcopy(planned_registry["takes"][take_id])
        updated_chunks = copy.deepcopy(state["chunks"])
        updated = updated_chunks[state["index"]]
        clear_approved_audio_fields(updated)
        updated.update(
            {
                "status": "pending",
                "audio_state": "stale",
                "audio_path": None,
                "stale_audio_path": artifact["audio_path"],
                "current_take_id": None,
                "take_record_fingerprint": None,
                "take_registry_fingerprint": planned_registry["registry_fingerprint"],
                "audio_fingerprint": None,
                "audio_sha256": None,
                "audio_size_bytes": None,
                "audio_duration_ms": None,
                "audio_format": None,
                "generated_at_utc": None,
                "generation_provenance": None,
                "error": None,
                "error_code": None,
                "invalidated_by_operation": f"approved-audio-acceptance-{idempotency_key}",
                **synthesis_receipt_reset_fields(),
            }
        )
        before = {
            "chunks_fingerprint": action["chunks_fingerprint"],
            "registry_fingerprint": action["registry_fingerprint"],
            "chunk_fingerprint": fingerprint_value(chunk),
            "content_fingerprint": action["content_fingerprint"],
            "binding_fingerprint": action["binding_fingerprint"],
            "current_audio_path": action["current_audio_path"],
            "current_audio_sha256": action["current_audio_sha256"],
        }
        after = {
            "chunks_fingerprint": fingerprint_value(updated_chunks),
            "registry_fingerprint": planned_registry["registry_fingerprint"],
            "chunk_fingerprint": fingerprint_value(updated),
        }
        required_artifacts = [
            {"relative_path": artifact["audio_path"], "sha256": artifact["audio_sha256"]}
        ]
        receipt = {
            "schema_version": ACCEPTANCE_SCHEMA_VERSION,
            "operation": action["operation"],
            "status": "accepted",
            "operation_id": f"approved-audio-acceptance-{idempotency_key}",
            "idempotency_key": idempotency_key,
            "action_fingerprint": action_fingerprint,
            "voice_configuration_fingerprint": action[
                "voice_configuration_fingerprint"
            ],
            "chunk_key": chunk_key_value,
            "chunk_index": state["index"],
            "created_at_utc": created_at,
            "regeneration_policy": "ordinary_retry_and_regeneration",
            "before": before,
            "after": after,
            "take": archived,
            "required_artifacts": required_artifacts,
        }
        receipt["receipt_fingerprint"] = fingerprint_value(receipt)
        apply_audio_transition(
            root,
            transition="immutable_take_installation",
            operation_id=f"approved-audio-acceptance-{idempotency_key}",
            binary_writes={artifact["audio_path"]: install["content"]},
            json_writes={
                AUDIO_TAKE_REGISTRY_FILENAME: planned_registry,
                "chunks.json": updated_chunks,
                receipt_path.relative_to(root).as_posix(): receipt,
            },
            required_artifacts={artifact["audio_path"]: artifact["audio_sha256"]},
            crash_point=crash_point,
        )
        return receipt
