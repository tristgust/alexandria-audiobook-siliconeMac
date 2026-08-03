from __future__ import annotations

from typing import Any, Mapping

from generation_state import fingerprint_value


APPROVED_AUDIO_LOCK_SCHEMA_VERSION = 1
APPROVED_AUDIO_LOCK_FIELD = "approved_audio_lock"
APPROVED_AUDIO_ORIGIN_FIELD = "approved_audio_origin"


class ApprovedAudioError(RuntimeError):
    pass


class ApprovedAudioLockedError(ApprovedAudioError):
    def __init__(self, *, chunk_id: Any, candidate_id: str | None = None):
        self.code = "approved_audio_regeneration_locked"
        self.chunk_id = chunk_id
        self.candidate_id = candidate_id
        super().__init__(
            "This chunk uses an approved adaptation performance. Regeneration is "
            "disabled until the authored text, speaker, or direction changes, or "
            "the approved import is explicitly removed."
        )


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def approved_audio_content_fingerprint(chunk: Mapping[str, Any]) -> str:
    return fingerprint_value(
        {
            "speaker": str(chunk.get("speaker") or ""),
            "text": str(chunk.get("text") or ""),
            "instruct": str(chunk.get("instruct") or ""),
        }
    )


def approved_audio_binding_fingerprint(
    chunk: Mapping[str, Any],
    lock: Mapping[str, Any] | None = None,
) -> str | None:
    value = lock if isinstance(lock, Mapping) else chunk.get(APPROVED_AUDIO_LOCK_FIELD)
    if not isinstance(value, Mapping):
        return None
    promotion_id = _text(value.get("promotion_id"))
    candidate_id = _text(value.get("candidate_id"))
    direct_tier = _text(value.get("direct_placement_tier"))
    source_sha256 = _text(value.get("source_audio_sha256"))
    if not all((promotion_id, candidate_id, direct_tier, source_sha256)):
        return None
    return fingerprint_value(
        {
            "contract": "approved_adaptation_audio_binding_v1",
            "promotion_id": promotion_id,
            "candidate_id": candidate_id,
            "direct_placement_tier": direct_tier,
            "source_audio_sha256": source_sha256,
            "content_fingerprint": approved_audio_content_fingerprint(chunk),
        }
    )


def active_approved_audio_lock(
    chunk: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(chunk, Mapping):
        return None
    raw = chunk.get(APPROVED_AUDIO_LOCK_FIELD)
    if not isinstance(raw, Mapping):
        return None
    if raw.get("schema_version") != APPROVED_AUDIO_LOCK_SCHEMA_VERSION:
        return None
    if raw.get("status") != "locked":
        return None
    content = _text(raw.get("content_fingerprint"))
    binding = _text(raw.get("binding_fingerprint"))
    computed_binding = approved_audio_binding_fingerprint(chunk, raw)
    if (
        content != approved_audio_content_fingerprint(chunk)
        or binding is None
        or binding != computed_binding
    ):
        return None
    return dict(raw)


def approved_audio_lock_fields(
    *,
    chunk: Mapping[str, Any],
    promotion_id: str,
    candidate_id: str,
    source_round_id: str | None,
    direct_placement_tier: str,
    source_audio_path: str,
    source_audio_sha256: str,
    manifest_path: str,
    installed_at_utc: str,
    reference_bank_eligible: bool,
) -> dict[str, Any]:
    lock_seed = {
        "schema_version": APPROVED_AUDIO_LOCK_SCHEMA_VERSION,
        "status": "locked",
        "promotion_id": promotion_id,
        "candidate_id": candidate_id,
        "source_round_id": source_round_id,
        "direct_placement_tier": direct_placement_tier,
        "source_audio_sha256": source_audio_sha256,
        "content_fingerprint": approved_audio_content_fingerprint(chunk),
        "installed_at_utc": installed_at_utc,
    }
    lock = {
        **lock_seed,
        "binding_fingerprint": approved_audio_binding_fingerprint(chunk, lock_seed),
    }
    return {
        APPROVED_AUDIO_LOCK_FIELD: lock,
        APPROVED_AUDIO_ORIGIN_FIELD: {
            "schema_version": 1,
            "promotion_id": promotion_id,
            "manifest_path": manifest_path,
            "candidate_id": candidate_id,
            "source_round_id": source_round_id,
            "direct_placement_tier": direct_placement_tier,
            "source_audio_path": source_audio_path,
            "source_audio_sha256": source_audio_sha256,
            "reference_bank_eligible": bool(reference_bank_eligible),
            "installed_at_utc": installed_at_utc,
        },
    }


def clear_approved_audio_fields(chunk: dict[str, Any]) -> None:
    chunk.pop(APPROVED_AUDIO_LOCK_FIELD, None)
    chunk.pop(APPROVED_AUDIO_ORIGIN_FIELD, None)


def require_regeneration_unlocked(chunk: Mapping[str, Any]) -> None:
    lock = active_approved_audio_lock(chunk)
    if lock is None:
        return
    raise ApprovedAudioLockedError(
        chunk_id=chunk.get("id"),
        candidate_id=_text(lock.get("candidate_id")),
    )
