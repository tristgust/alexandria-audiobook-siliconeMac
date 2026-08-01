from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from approved_audio import active_approved_audio_lock
from audio_invalidation import (
    AUDIO_INVALIDATION_HISTORY_DIRNAME,
    AudioInvalidationError,
    apply_audio_invalidation_transaction,
)
from generation_state import fingerprint_value


PRONUNCIATION_REGISTRY_SCHEMA_VERSION = 1
PRONUNCIATION_REQUEST_SCHEMA_VERSION = 1
PRONUNCIATION_REGISTRY_FILENAME = "pronunciation_registry.json"

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_REVIEW_STATES = {"draft", "approved", "rejected"}
_FALLBACK_STRATEGIES = {"bypass", "spoken_form"}


class PronunciationRegistryError(RuntimeError):
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


def _text(value: Any) -> str:
    return str(value or "").strip()


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise PronunciationRegistryError(
            "pronunciation_registry_field_invalid",
            "Pronunciation limit fields must contain arrays of strings.",
        )
    return sorted(
        {
            item
            for item in (_text(item) for item in value)
            if item
        },
        key=str.casefold,
    )


def empty_pronunciation_registry() -> dict[str, Any]:
    registry = {
        "schema_version": PRONUNCIATION_REGISTRY_SCHEMA_VERSION,
        "entries": [],
    }
    registry["registry_fingerprint"] = fingerprint_value(registry["entries"])
    return registry


def _normalize_review(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    state = _text(raw.get("state") or "draft").casefold()
    if state not in _REVIEW_STATES:
        raise PronunciationRegistryError(
            "pronunciation_review_state_invalid",
            f"Pronunciation review state must be one of {sorted(_REVIEW_STATES)}.",
        )
    return {
        "state": state,
        "reviewer": _text(raw.get("reviewer")) or None,
        "reviewed_at_utc": _text(raw.get("reviewed_at_utc")) or None,
        "notes": _text(raw.get("notes")) or None,
    }


def _normalize_fallback(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    strategy = _text(raw.get("strategy") or "bypass").casefold()
    if strategy not in _FALLBACK_STRATEGIES:
        raise PronunciationRegistryError(
            "pronunciation_fallback_invalid",
            f"Pronunciation fallback must be one of {sorted(_FALLBACK_STRATEGIES)}.",
        )
    spoken_form = _text(raw.get("spoken_form")) or None
    if strategy == "spoken_form" and not spoken_form:
        raise PronunciationRegistryError(
            "pronunciation_fallback_invalid",
            "A spoken-form fallback requires fallback.spoken_form.",
        )
    return {
        "strategy": strategy,
        "spoken_form": spoken_form,
        "reason": _text(raw.get("reason")) or None,
    }


def normalize_pronunciation_entry(
    value: Mapping[str, Any],
    *,
    chunks: list[dict[str, Any]] | None = None,
    require_current_anchor: bool = False,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PronunciationRegistryError(
            "pronunciation_entry_invalid",
            "Pronunciation entries must be JSON objects.",
        )
    entry_id = _text(value.get("pronunciation_id") or value.get("id"))
    if not _ID_PATTERN.fullmatch(entry_id):
        raise PronunciationRegistryError(
            "pronunciation_id_invalid",
            "Pronunciation IDs may contain letters, numbers, period, dash, and underscore.",
        )
    try:
        chunk_index = int(value.get("chunk_index"))
        start_char = int(value.get("start_char"))
        end_char = int(value.get("end_char"))
    except (TypeError, ValueError) as exc:
        raise PronunciationRegistryError(
            "pronunciation_source_span_invalid",
            "Pronunciation chunk index and character offsets must be integers.",
        ) from exc
    original = str(value.get("original") or "")
    if chunk_index < 0 or start_char < 0 or end_char <= start_char or not original:
        raise PronunciationRegistryError(
            "pronunciation_source_span_invalid",
            "Pronunciation source span is incomplete or invalid.",
        )
    spoken_form = _text(value.get("spoken_form")) or None
    phonetic_hint = _text(value.get("phonetic_hint")) or None
    if not spoken_form and not phonetic_hint:
        raise PronunciationRegistryError(
            "pronunciation_output_missing",
            "Add a spoken form or phonetic hint.",
        )
    chunk_text_sha256 = _text(value.get("chunk_text_sha256")) or None
    if chunks is not None:
        if not 0 <= chunk_index < len(chunks):
            raise PronunciationRegistryError(
                "pronunciation_chunk_missing",
                f"Pronunciation references missing chunk {chunk_index}.",
            )
        chunk = chunks[chunk_index]
        chunk_text = str(chunk.get("text") or "") if isinstance(chunk, Mapping) else ""
        expected_hash = _text_sha256(chunk_text)
        if require_current_anchor:
            if end_char > len(chunk_text) or chunk_text[start_char:end_char] != original:
                raise PronunciationRegistryError(
                    "pronunciation_source_span_mismatch",
                    "The pronunciation source span does not match the current chunk text.",
                    context={"chunk_index": chunk_index},
                )
            if chunk_text_sha256 and chunk_text_sha256 != expected_hash:
                raise PronunciationRegistryError(
                    "pronunciation_source_fingerprint_mismatch",
                    "The pronunciation belongs to an older version of this chunk.",
                    context={"chunk_index": chunk_index},
                )
        chunk_text_sha256 = expected_hash
    if not chunk_text_sha256 or not re.fullmatch(r"[0-9a-f]{64}", chunk_text_sha256):
        raise PronunciationRegistryError(
            "pronunciation_source_fingerprint_invalid",
            "Pronunciation entries require a valid chunk-text SHA-256 fingerprint.",
        )
    source = value.get("source") if isinstance(value.get("source"), Mapping) else {}
    source_kind = _text(source.get("kind") or "accepted_script_chunk")
    if source_kind != "accepted_script_chunk":
        raise PronunciationRegistryError(
            "pronunciation_source_kind_unsupported",
            "Pronunciation entries currently require an accepted Script chunk source span.",
        )
    engine_source = (
        value.get("engine_source")
        if isinstance(value.get("engine_source"), Mapping)
        else {}
    )
    provenance = (
        value.get("provenance")
        if isinstance(value.get("provenance"), Mapping)
        else {}
    )
    normalized = {
        "pronunciation_id": entry_id,
        "scope": "exact_occurrence",
        "chunk_index": chunk_index,
        "start_char": start_char,
        "end_char": end_char,
        "original": original,
        "chunk_text_sha256": chunk_text_sha256,
        "source": {
            "kind": source_kind,
            "chunk_index": chunk_index,
            "start_char": start_char,
            "end_char": end_char,
            "quote": original,
            "chunk_text_sha256": chunk_text_sha256,
        },
        "spoken_form": spoken_form,
        "phonetic_hint": phonetic_hint,
        "languages": _string_list(value.get("languages")),
        "character_labels": _string_list(value.get("character_labels")),
        "voice_ids": _string_list(value.get("voice_ids")),
        "engine_ids": _string_list(value.get("engine_ids")),
        "engine_source": {
            "kind": _text(engine_source.get("kind") or "manual"),
            "engine": _text(engine_source.get("engine")) or None,
            "revision": _text(engine_source.get("revision")) or None,
            "phoneme_alphabet": _text(engine_source.get("phoneme_alphabet")) or None,
        },
        "fallback": _normalize_fallback(value.get("fallback")),
        "review": _normalize_review(value.get("review")),
        "provenance": {
            "source": _text(provenance.get("source") or source.get("kind") or "manual"),
            "created_at_utc": _text(provenance.get("created_at_utc")) or None,
            "evidence": copy.deepcopy(provenance.get("evidence")),
        },
    }
    normalized["entry_fingerprint"] = fingerprint_value(normalized)
    return normalized


def normalize_pronunciation_registry(
    value: Any,
    *,
    chunks: list[dict[str, Any]] | None = None,
    require_current_anchors: bool = False,
) -> dict[str, Any]:
    if value in (None, {}):
        return empty_pronunciation_registry()
    if not isinstance(value, Mapping):
        raise PronunciationRegistryError(
            "pronunciation_registry_invalid",
            "Pronunciation registry must be a JSON object.",
        )
    raw_entries = value.get("entries")
    if not isinstance(raw_entries, list):
        raise PronunciationRegistryError(
            "pronunciation_registry_invalid",
            "Pronunciation registry entries must be a JSON array.",
        )
    entries = [
        normalize_pronunciation_entry(
            item,
            chunks=chunks,
            require_current_anchor=require_current_anchors,
        )
        for item in raw_entries
    ]
    ids = [item["pronunciation_id"] for item in entries]
    if len(ids) != len(set(ids)):
        raise PronunciationRegistryError(
            "pronunciation_id_duplicate",
            "Pronunciation registry contains duplicate IDs.",
        )
    approved = [item for item in entries if item["review"]["state"] == "approved"]
    for index, left in enumerate(approved):
        for right in approved[index + 1 :]:
            if left["chunk_index"] != right["chunk_index"]:
                continue
            if max(left["start_char"], right["start_char"]) < min(
                left["end_char"], right["end_char"]
            ):
                raise PronunciationRegistryError(
                    "pronunciation_overlap_conflict",
                    "Approved pronunciation entries cannot overlap in one chunk.",
                    context={
                        "left": left["pronunciation_id"],
                        "right": right["pronunciation_id"],
                    },
                )
    entries.sort(
        key=lambda item: (
            item["chunk_index"],
            item["start_char"],
            item["end_char"],
            item["pronunciation_id"].casefold(),
        )
    )
    registry = {
        "schema_version": PRONUNCIATION_REGISTRY_SCHEMA_VERSION,
        "entries": entries,
    }
    registry["registry_fingerprint"] = fingerprint_value(entries)
    return registry


def load_pronunciation_registry(project_root: str | Path) -> dict[str, Any]:
    path = Path(project_root).expanduser().resolve() / PRONUNCIATION_REGISTRY_FILENAME
    if not path.is_file():
        return empty_pronunciation_registry()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PronunciationRegistryError(
            "pronunciation_registry_unreadable",
            f"Pronunciation registry could not be read: {exc}",
        ) from exc
    return normalize_pronunciation_registry(value)


def _voice_identity(voice_data: Mapping[str, Any]) -> str | None:
    for key in ("library_voice_id", "adapter_id", "voice", "clone_backend", "type"):
        value = _text(voice_data.get(key))
        if value:
            return value
    return None


def resolve_pronunciation_request(
    *,
    registry: Mapping[str, Any],
    chunk_index: int,
    text: str,
    speaker: str,
    resolved_speaker: str,
    voice_data: Mapping[str, Any] | None = None,
    language: str | None = None,
    engine_id: str | None = None,
    supports_phonetic_hint: bool = False,
) -> dict[str, Any]:
    normalized = normalize_pronunciation_registry(registry)
    chunk_hash = _text_sha256(text)
    voice_id = _voice_identity(voice_data or {})
    decisions: list[dict[str, Any]] = []
    applicable: list[tuple[dict[str, Any], str, str]] = []
    for entry in normalized["entries"]:
        if entry["chunk_index"] != int(chunk_index):
            continue
        status = "bypassed"
        reason = None
        replacement = None
        if entry["review"]["state"] != "approved":
            reason = "not_approved"
        elif entry["chunk_text_sha256"] != chunk_hash:
            reason = "chunk_text_changed"
        elif entry["end_char"] > len(text) or text[
            entry["start_char"] : entry["end_char"]
        ] != entry["original"]:
            reason = "source_span_mismatch"
        elif entry["languages"] and _text(language).casefold() not in {
            value.casefold() for value in entry["languages"]
        }:
            reason = "language_limit"
        elif entry["character_labels"] and not {
            _text(speaker).casefold(),
            _text(resolved_speaker).casefold(),
        }.intersection(value.casefold() for value in entry["character_labels"]):
            reason = "character_limit"
        elif entry["voice_ids"] and _text(voice_id).casefold() not in {
            value.casefold() for value in entry["voice_ids"]
        }:
            reason = "voice_limit"
        elif entry["engine_ids"] and _text(engine_id).casefold() not in {
            value.casefold() for value in entry["engine_ids"]
        }:
            reason = "engine_limit"
        elif entry["spoken_form"]:
            replacement = entry["spoken_form"]
            status = "applied"
            reason = "spoken_form"
        elif entry["phonetic_hint"] and supports_phonetic_hint:
            replacement = entry["phonetic_hint"]
            status = "applied"
            reason = "phonetic_hint"
        elif entry["fallback"]["strategy"] == "spoken_form":
            replacement = entry["fallback"]["spoken_form"]
            status = "applied"
            reason = "phonetic_hint_fallback"
        else:
            reason = "phonetic_hint_unsupported"
        decision = {
            "pronunciation_id": entry["pronunciation_id"],
            "entry_fingerprint": entry["entry_fingerprint"],
            "chunk_index": entry["chunk_index"],
            "start_char": entry["start_char"],
            "end_char": entry["end_char"],
            "original": entry["original"],
            "spoken_form": entry["spoken_form"],
            "phonetic_hint": entry["phonetic_hint"],
            "status": status,
            "reason": reason,
            "replacement": replacement,
            "engine_source": copy.deepcopy(entry["engine_source"]),
            "fallback": copy.deepcopy(entry["fallback"]),
            "review": copy.deepcopy(entry["review"]),
            "provenance": copy.deepcopy(entry["provenance"]),
        }
        decisions.append(decision)
        if status == "applied" and replacement is not None:
            applicable.append((entry, replacement, reason))
    synthesis_text = text
    for entry, replacement, _reason in sorted(
        applicable,
        key=lambda item: item[0]["start_char"],
        reverse=True,
    ):
        synthesis_text = (
            synthesis_text[: entry["start_char"]]
            + replacement
            + synthesis_text[entry["end_char"] :]
        )
    chunk_entry_fingerprint = fingerprint_value(
        [
            item["entry_fingerprint"]
            for item in decisions
        ]
    )
    receipt = {
        "schema_version": PRONUNCIATION_REQUEST_SCHEMA_VERSION,
        "registry_fingerprint": normalized["registry_fingerprint"],
        "chunk_entry_fingerprint": chunk_entry_fingerprint,
        "chunk_index": int(chunk_index),
        "chunk_text_sha256": chunk_hash,
        "synthesis_text_sha256": _text_sha256(synthesis_text),
        "applied_count": sum(item["status"] == "applied" for item in decisions),
        "bypassed_count": sum(item["status"] == "bypassed" for item in decisions),
        "decisions": decisions,
    }
    receipt["request_fingerprint"] = fingerprint_value(
        {
            "schema_version": receipt["schema_version"],
            "chunk_entry_fingerprint": chunk_entry_fingerprint,
            "chunk_index": receipt["chunk_index"],
            "chunk_text_sha256": receipt["chunk_text_sha256"],
            "synthesis_text_sha256": receipt["synthesis_text_sha256"],
            "applied_count": receipt["applied_count"],
            "bypassed_count": receipt["bypassed_count"],
            "decisions": decisions,
        }
    )
    return {
        "source_text": text,
        "synthesis_text": synthesis_text,
        "receipt": receipt,
    }


def pronunciation_chunk_fields(resolution: Mapping[str, Any]) -> dict[str, Any]:
    receipt = resolution.get("receipt") if isinstance(resolution, Mapping) else None
    receipt = receipt if isinstance(receipt, Mapping) else {}
    decisions = copy.deepcopy(receipt.get("decisions") or [])
    if not decisions:
        return {
            "pronunciation_registry_fingerprint": None,
            "pronunciation_chunk_entry_fingerprint": None,
            "pronunciation_request_fingerprint": None,
            "pronunciation_synthesis_text_sha256": None,
            "pronunciation_applied_count": None,
            "pronunciation_bypassed_count": None,
            "pronunciation_decisions": None,
        }
    return {
        "pronunciation_registry_fingerprint": receipt.get("registry_fingerprint"),
        "pronunciation_chunk_entry_fingerprint": receipt.get(
            "chunk_entry_fingerprint"
        ),
        "pronunciation_request_fingerprint": receipt.get("request_fingerprint"),
        "pronunciation_synthesis_text_sha256": receipt.get("synthesis_text_sha256"),
        "pronunciation_applied_count": int(receipt.get("applied_count") or 0),
        "pronunciation_bypassed_count": int(receipt.get("bypassed_count") or 0),
        "pronunciation_decisions": decisions,
    }


def pronunciation_binding_fields(chunk: Mapping[str, Any]) -> dict[str, Any] | None:
    request_fingerprint = _text(chunk.get("pronunciation_request_fingerprint"))
    if not request_fingerprint:
        return None
    return {
        "chunk_entry_fingerprint": chunk.get(
            "pronunciation_chunk_entry_fingerprint"
        ),
        "request_fingerprint": request_fingerprint,
        "synthesis_text_sha256": chunk.get("pronunciation_synthesis_text_sha256"),
        "applied_count": int(chunk.get("pronunciation_applied_count") or 0),
        "bypassed_count": int(chunk.get("pronunciation_bypassed_count") or 0),
    }


def upsert_pronunciation_entry(
    registry: Mapping[str, Any],
    entry: Mapping[str, Any],
    *,
    chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized = normalize_pronunciation_registry(registry)
    replacement = normalize_pronunciation_entry(
        entry,
        chunks=chunks,
        require_current_anchor=True,
    )
    entries = [
        item
        for item in normalized["entries"]
        if item["pronunciation_id"] != replacement["pronunciation_id"]
    ]
    entries.append(replacement)
    return normalize_pronunciation_registry(
        {"entries": entries},
        chunks=chunks,
        require_current_anchors=True,
    )


def remove_pronunciation_entry(
    registry: Mapping[str, Any],
    pronunciation_id: str,
) -> dict[str, Any]:
    normalized = normalize_pronunciation_registry(registry)
    retained = [
        item
        for item in normalized["entries"]
        if item["pronunciation_id"] != pronunciation_id
    ]
    if len(retained) == len(normalized["entries"]):
        raise PronunciationRegistryError(
            "pronunciation_entry_missing",
            f"Pronunciation entry {pronunciation_id!r} does not exist.",
        )
    return normalize_pronunciation_registry({"entries": retained})


def changed_pronunciation_chunk_indices(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> list[int]:
    before_entries = {
        item["pronunciation_id"]: item
        for item in normalize_pronunciation_registry(before)["entries"]
    }
    after_entries = {
        item["pronunciation_id"]: item
        for item in normalize_pronunciation_registry(after)["entries"]
    }
    changed = {
        key
        for key in set(before_entries) | set(after_entries)
        if fingerprint_value(before_entries.get(key))
        != fingerprint_value(after_entries.get(key))
    }
    return sorted(
        {
            int(entry["chunk_index"])
            for key in changed
            for entry in (before_entries.get(key), after_entries.get(key))
            if isinstance(entry, Mapping)
        }
    )


def apply_pronunciation_registry_change(
    *,
    project_root: str | Path,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    operation_id: str,
    operation: str,
    at_utc: str,
    reason: str,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    registry_path = root / PRONUNCIATION_REGISTRY_FILENAME
    chunks_path = root / "chunks.json"
    try:
        chunks = (
            json.loads(chunks_path.read_text(encoding="utf-8"))
            if chunks_path.is_file()
            else []
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PronunciationRegistryError(
            "pronunciation_chunks_unreadable",
            f"Project chunks could not be read: {exc}",
        ) from exc
    if not isinstance(chunks, list):
        raise PronunciationRegistryError(
            "pronunciation_chunks_invalid",
            "Project chunks must contain a JSON array.",
        )
    affected_indices = changed_pronunciation_chunk_indices(before, after)
    changed_chunks = copy.deepcopy(chunks)
    invalidations: list[dict[str, Any]] = []
    for index in affected_indices:
        if not 0 <= index < len(changed_chunks):
            continue
        chunk = changed_chunks[index]
        if not isinstance(chunk, dict) or active_approved_audio_lock(chunk) is not None:
            continue
        old_path = _text(chunk.get("audio_path")) or None
        if old_path is None and chunk.get("status") != "done":
            continue
        invalidations.append(
            {
                "chunk_id": chunk.get("id", index),
                "speaker": chunk.get("speaker"),
                "audio_path": old_path,
                "reason": reason,
                "dependency_kind": "pronunciation_registry",
                "pronunciation_fingerprint": chunk.get(
                    "pronunciation_request_fingerprint"
                ),
            }
        )
        chunk.update(
            {
                "status": "pending",
                "audio_path": None,
                "stale_audio_path": old_path,
                "audio_state": "stale" if old_path else "pending",
                "invalidated_by_operation": operation_id,
                "audio_invalidation_reason": reason,
            }
        )
        for field in (
            "audio_fingerprint",
            "audio_sha256",
            "audio_size_bytes",
            "audio_duration_ms",
            "audio_format",
            "error",
            "error_code",
            "pronunciation_registry_fingerprint",
            "pronunciation_chunk_entry_fingerprint",
            "pronunciation_request_fingerprint",
            "pronunciation_synthesis_text_sha256",
            "pronunciation_applied_count",
            "pronunciation_bypassed_count",
            "pronunciation_decisions",
        ):
            chunk[field] = None
    changes: dict[Path, Any] = {
        registry_path: after if after.get("entries") else None,
    }
    if invalidations:
        changes[chunks_path] = changed_chunks
    try:
        return apply_audio_invalidation_transaction(
            project_root=root,
            operation_dir=(
                root / AUDIO_INVALIDATION_HISTORY_DIRNAME / operation_id
            ),
            operation_id=operation_id,
            operation=operation,
            at_utc=at_utc,
            changes=changes,
            invalidations=invalidations,
            default_reason=reason,
            note=(
                "Pronunciation registry changed. Only audio anchored to changed "
                "occurrences was invalidated."
            ),
            record_metadata={
                "affected_chunk_indices": affected_indices,
                "pronunciation_registry_before_fingerprint": before.get(
                    "registry_fingerprint"
                ),
                "pronunciation_registry_after_fingerprint": after.get(
                    "registry_fingerprint"
                ),
            },
        )
    except AudioInvalidationError as exc:
        raise PronunciationRegistryError(exc.code, str(exc)) from exc
