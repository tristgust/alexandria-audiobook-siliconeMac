from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


PROMPT_ROUTING_SCHEMA_VERSION = 1
PROMPT_ROUTE_TAG = re.compile(
    r"\[\s*prompt-route\s*:\s*([a-z][a-z0-9_]{1,63})\s*\]",
    re.IGNORECASE,
)
_ROUTE_KEY = re.compile(r"[a-z][a-z0-9_]{1,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ALLOWED_PROMPT_ROLES = {"legacy_reference", "validated_bank"}
_ALLOWED_AUDIO_ROOTS = (
    "clone_voices",
    "designed_voices",
    "voice_training_projects",
    "experimental_prompts",
)


class ExperimentalPromptRoutingError(RuntimeError):
    pass


def _text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ExperimentalPromptRoutingError(f"{label} must be text.")
    text = value.strip()
    if not text and not allow_empty:
        raise ExperimentalPromptRoutingError(f"{label} must not be empty.")
    return text


def _route_key(value: Any, label: str = "Prompt route") -> str:
    text = _text(value, label).casefold().replace("-", "_").replace(" ", "_")
    if not _ROUTE_KEY.fullmatch(text):
        raise ExperimentalPromptRoutingError(f"{label} is invalid.")
    return text


def _sha256(value: Any, label: str) -> str:
    text = _text(value, label)
    if not _SHA256.fullmatch(text):
        raise ExperimentalPromptRoutingError(
            f"{label} must be a lowercase SHA-256 fingerprint."
        )
    return text


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prompt_routing_fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def parse_prompt_route(instruction: str) -> str | None:
    text = instruction if isinstance(instruction, str) else str(instruction or "")
    match = PROMPT_ROUTE_TAG.search(text)
    return _route_key(match.group(1)) if match else None


def strip_prompt_route_tag(instruction: str) -> str:
    text = instruction if isinstance(instruction, str) else str(instruction or "")
    return re.sub(r"\s+", " ", PROMPT_ROUTE_TAG.sub(" ", text)).strip()


def _resolve_audio_path(
    *,
    project_root: str | Path,
    relative_path: str,
) -> Path:
    root = Path(project_root).expanduser().resolve()
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ExperimentalPromptRoutingError(
            "Experimental prompt audio must be a safe project-relative path."
        )
    resolved = (root / relative).resolve()
    allowed = [(root / name).resolve() for name in _ALLOWED_AUDIO_ROOTS]
    if not any(resolved.is_relative_to(directory) for directory in allowed):
        raise ExperimentalPromptRoutingError(
            "Experimental prompt audio must remain inside an approved project voice directory."
        )
    return resolved


def validate_experimental_prompt_routing(
    value: Any,
    *,
    project_root: str | Path | None = None,
    verify_audio: bool = False,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExperimentalPromptRoutingError(
            "experimental_prompt_routing must be an object."
        )
    expected = {
        "schema_version",
        "enabled",
        "scope",
        "general_routing",
        "production_promotion_allowed",
        "evidence_round_id",
        "routes",
    }
    if set(value) != expected:
        raise ExperimentalPromptRoutingError(
            "experimental_prompt_routing has unexpected fields."
        )
    if value.get("schema_version") != PROMPT_ROUTING_SCHEMA_VERSION:
        raise ExperimentalPromptRoutingError(
            "Experimental prompt routing schema is unsupported."
        )
    enabled = value.get("enabled")
    if not isinstance(enabled, bool):
        raise ExperimentalPromptRoutingError(
            "experimental_prompt_routing.enabled must be boolean."
        )
    if value.get("scope") != "research_only":
        raise ExperimentalPromptRoutingError(
            "Experimental prompt routing must remain research-only."
        )
    if value.get("general_routing") != "disabled":
        raise ExperimentalPromptRoutingError(
            "General prompt routing must remain disabled."
        )
    if value.get("production_promotion_allowed") is not False:
        raise ExperimentalPromptRoutingError(
            "Experimental prompt routing may not enable production promotion."
        )
    evidence_round_id = _text(
        value.get("evidence_round_id"),
        "Experimental prompt evidence round",
    )
    routes = value.get("routes")
    if not isinstance(routes, dict):
        raise ExperimentalPromptRoutingError(
            "experimental_prompt_routing.routes must be an object."
        )
    normalized_routes: dict[str, dict[str, Any]] = {}
    for raw_key, raw_route in routes.items():
        key = _route_key(raw_key, "Experimental prompt route key")
        if key in normalized_routes:
            raise ExperimentalPromptRoutingError(
                f"Duplicate experimental prompt route: {key}."
            )
        if not isinstance(raw_route, dict):
            raise ExperimentalPromptRoutingError(
                f"Experimental prompt route {key} must be an object."
            )
        route_expected = {
            "status",
            "prompt_role",
            "reference_key",
            "validated_bank_clip_id",
            "ref_audio",
            "ref_audio_sha256",
            "ref_text",
            "production_promotion_allowed",
        }
        if set(raw_route) != route_expected:
            raise ExperimentalPromptRoutingError(
                f"Experimental prompt route {key} has unexpected fields."
            )
        if raw_route.get("status") != "research_preferred":
            raise ExperimentalPromptRoutingError(
                f"Experimental prompt route {key} is not research-preferred."
            )
        prompt_role = _text(
            raw_route.get("prompt_role"),
            f"Experimental prompt route {key}.prompt_role",
        )
        if prompt_role not in _ALLOWED_PROMPT_ROLES:
            raise ExperimentalPromptRoutingError(
                f"Experimental prompt route {key} has an unsupported prompt role."
            )
        if raw_route.get("production_promotion_allowed") is not False:
            raise ExperimentalPromptRoutingError(
                f"Experimental prompt route {key} may not enable production promotion."
            )
        ref_audio = _text(
            raw_route.get("ref_audio"),
            f"Experimental prompt route {key}.ref_audio",
        )
        audio_sha = _sha256(
            raw_route.get("ref_audio_sha256"),
            f"Experimental prompt route {key}.ref_audio_sha256",
        )
        if project_root is not None:
            audio_path = _resolve_audio_path(
                project_root=project_root,
                relative_path=ref_audio,
            )
            if verify_audio:
                if not audio_path.is_file():
                    raise ExperimentalPromptRoutingError(
                        f"Experimental prompt audio is missing for route {key}."
                    )
                if sha256_file(audio_path) != audio_sha:
                    raise ExperimentalPromptRoutingError(
                        f"Experimental prompt audio changed for route {key}."
                    )
        normalized_routes[key] = {
            "status": "research_preferred",
            "prompt_role": prompt_role,
            "reference_key": _text(
                raw_route.get("reference_key"),
                f"Experimental prompt route {key}.reference_key",
            ),
            "validated_bank_clip_id": _text(
                raw_route.get("validated_bank_clip_id"),
                f"Experimental prompt route {key}.validated_bank_clip_id",
            ),
            "ref_audio": Path(ref_audio).as_posix(),
            "ref_audio_sha256": audio_sha,
            "ref_text": _text(
                raw_route.get("ref_text"),
                f"Experimental prompt route {key}.ref_text",
            ),
            "production_promotion_allowed": False,
        }
    return {
        "schema_version": PROMPT_ROUTING_SCHEMA_VERSION,
        "enabled": enabled,
        "scope": "research_only",
        "general_routing": "disabled",
        "production_promotion_allowed": False,
        "evidence_round_id": evidence_round_id,
        "routes": normalized_routes,
    }


def experimental_prompt_chunk_fields(
    selection: dict[str, Any] | None,
) -> dict[str, Any]:
    if selection is None:
        return {
            "audio_research_only": False,
            "experimental_prompt_route": None,
            "experimental_prompt_role": None,
            "experimental_prompt_evidence_round_id": None,
            "production_promotion_allowed": False,
        }
    return {
        "audio_research_only": True,
        "experimental_prompt_route": selection["route_key"],
        "experimental_prompt_role": selection["prompt_role"],
        "experimental_prompt_evidence_round_id": selection[
            "evidence_round_id"
        ],
        "production_promotion_allowed": False,
    }


def resolve_experimental_prompt_override(
    *,
    voice_data: dict[str, Any],
    instruction: str,
    project_root: str | Path,
) -> dict[str, Any] | None:
    raw = voice_data.get("experimental_prompt_routing")
    if raw is None:
        return None
    policy = validate_experimental_prompt_routing(
        raw,
        project_root=project_root,
        verify_audio=True,
    )
    if not policy["enabled"]:
        return None
    route_key = parse_prompt_route(instruction)
    if route_key is None:
        return None
    route = policy["routes"].get(route_key)
    if route is None:
        raise ExperimentalPromptRoutingError(
            f"No approved experimental prompt route exists for {route_key!r}."
        )
    audio = _resolve_audio_path(
        project_root=project_root,
        relative_path=route["ref_audio"],
    )
    return {
        "route_key": route_key,
        "prompt_role": route["prompt_role"],
        "reference_key": route["reference_key"],
        "validated_bank_clip_id": route["validated_bank_clip_id"],
        "ref_audio": str(audio),
        "ref_text": route["ref_text"],
        "ref_audio_sha256": route["ref_audio_sha256"],
        "evidence_round_id": policy["evidence_round_id"],
        "routing_fingerprint": prompt_routing_fingerprint(policy),
        "production_promotion_allowed": False,
    }
