from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1
FILENAME = "voice_route_listening_decisions.json"
STATUSES = frozenset(
    {
        "approved",
        "restricted",
        "return_to_preparation",
        "rejected",
    }
)
PRODUCTION_ACTIONS = frozenset(
    {
        "keep_current",
        "replace_route",
        "preserve_prior_routes",
        "preparation_required",
    }
)
APPROVAL_TIERS = frozenset({"strict", "restricted_user_accepted"})


class VoiceRouteListeningDecisionError(RuntimeError):
    pass


def _text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise VoiceRouteListeningDecisionError(f"{label} must be text.")
    result = value.strip()
    if not result and not allow_empty:
        raise VoiceRouteListeningDecisionError(f"{label} must not be empty.")
    return result


def _optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _text(value, label)


def _sha256(value: Any, label: str) -> str:
    result = _text(value, label)
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise VoiceRouteListeningDecisionError(
            f"{label} must be a lowercase SHA-256 fingerprint."
        )
    return result


def _text_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise VoiceRouteListeningDecisionError(f"{label} must be a list.")
    result: list[str] = []
    for item in value:
        text = _text(item, label)
        if text not in result:
            result.append(text)
    return result


def _decision(value: Any, voice_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise VoiceRouteListeningDecisionError(
            f"Decision for {voice_name!r} must be an object."
        )
    expected = {
        "status",
        "primary_method",
        "primary_candidate_id",
        "summary",
        "production_action",
        "preserve_prior_routes",
        "route_key",
        "approval_tier",
        "evidence_sample_ids",
        "unresolved_requirements",
    }
    if set(value) != expected:
        raise VoiceRouteListeningDecisionError(
            f"Decision for {voice_name!r} has unexpected or missing fields."
        )
    status = _text(value["status"], f"{voice_name}.status")
    if status not in STATUSES:
        raise VoiceRouteListeningDecisionError(
            f"Decision for {voice_name!r} has an unsupported status."
        )
    action = _text(
        value["production_action"],
        f"{voice_name}.production_action",
    )
    if action not in PRODUCTION_ACTIONS:
        raise VoiceRouteListeningDecisionError(
            f"Decision for {voice_name!r} has an unsupported production action."
        )
    if not isinstance(value["preserve_prior_routes"], bool):
        raise VoiceRouteListeningDecisionError(
            f"{voice_name}.preserve_prior_routes must be boolean."
        )
    approval_tier = _optional_text(
        value["approval_tier"],
        f"{voice_name}.approval_tier",
    )
    if approval_tier is not None and approval_tier not in APPROVAL_TIERS:
        raise VoiceRouteListeningDecisionError(
            f"Decision for {voice_name!r} has an unsupported approval tier."
        )
    route_key = _optional_text(value["route_key"], f"{voice_name}.route_key")
    primary_method = _optional_text(
        value["primary_method"],
        f"{voice_name}.primary_method",
    )
    primary_candidate_id = _optional_text(
        value["primary_candidate_id"],
        f"{voice_name}.primary_candidate_id",
    )
    if action == "replace_route" and not all(
        (route_key, primary_method, primary_candidate_id, approval_tier)
    ):
        raise VoiceRouteListeningDecisionError(
            f"Route replacement for {voice_name!r} requires route, candidate, method, and approval tier."
        )
    return {
        "status": status,
        "primary_method": primary_method,
        "primary_candidate_id": primary_candidate_id,
        "summary": _text(value["summary"], f"{voice_name}.summary"),
        "production_action": action,
        "preserve_prior_routes": bool(value["preserve_prior_routes"]),
        "route_key": route_key,
        "approval_tier": approval_tier,
        "evidence_sample_ids": _text_list(
            value["evidence_sample_ids"],
            f"{voice_name}.evidence_sample_ids",
        ),
        "unresolved_requirements": _text_list(
            value["unresolved_requirements"],
            f"{voice_name}.unresolved_requirements",
        ),
    }


def normalize_voice_route_listening_decisions(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise VoiceRouteListeningDecisionError(
            "Voice route listening decisions must be an object."
        )
    expected = {
        "schema_version",
        "round_id",
        "completed_at",
        "review_sha256",
        "answer_key_sha256",
        "evidence_path",
        "decisions",
    }
    if set(value) != expected:
        raise VoiceRouteListeningDecisionError(
            "Voice route listening decisions have unexpected or missing fields."
        )
    if value["schema_version"] != SCHEMA_VERSION:
        raise VoiceRouteListeningDecisionError(
            "Voice route listening decision schema is unsupported."
        )
    decisions_value = value["decisions"]
    if not isinstance(decisions_value, Mapping) or not decisions_value:
        raise VoiceRouteListeningDecisionError(
            "Voice route listening decisions require at least one Voice."
        )
    decisions: dict[str, dict[str, Any]] = {}
    normalized_keys: set[str] = set()
    for raw_name, raw_decision in decisions_value.items():
        name = _text(raw_name, "Voice decision name")
        normalized = name.casefold()
        if normalized in normalized_keys:
            raise VoiceRouteListeningDecisionError(
                f"Duplicate Voice decision name: {name!r}."
            )
        normalized_keys.add(normalized)
        decisions[name] = _decision(raw_decision, name)
    return {
        "schema_version": SCHEMA_VERSION,
        "round_id": _text(value["round_id"], "round_id"),
        "completed_at": _text(value["completed_at"], "completed_at"),
        "review_sha256": _sha256(value["review_sha256"], "review_sha256"),
        "answer_key_sha256": _sha256(
            value["answer_key_sha256"],
            "answer_key_sha256",
        ),
        "evidence_path": _text(value["evidence_path"], "evidence_path"),
        "decisions": decisions,
    }


def decision_fingerprint(value: Mapping[str, Any]) -> str:
    normalized = normalize_voice_route_listening_decisions(value)
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_voice_route_listening_decisions(
    project_root: str | Path,
) -> dict[str, Any] | None:
    path = Path(project_root).expanduser().resolve() / FILENAME
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VoiceRouteListeningDecisionError(
            f"Voice route listening decisions could not be read: {exc}"
        ) from exc
    return normalize_voice_route_listening_decisions(value)


def decision_for_voice(
    project_root: str | Path,
    voice_name: str | None,
) -> dict[str, Any] | None:
    if not voice_name:
        return None
    document = load_voice_route_listening_decisions(project_root)
    if document is None:
        return None
    target = str(voice_name).strip().casefold()
    for name, decision in document["decisions"].items():
        if name.casefold() == target:
            return {
                **decision,
                "round_id": document["round_id"],
                "completed_at": document["completed_at"],
                "review_sha256": document["review_sha256"],
                "evidence_path": document["evidence_path"],
                "decision_fingerprint": decision_fingerprint(document),
            }
    return None
