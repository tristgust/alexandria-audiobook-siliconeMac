from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


PROMPT_ROUTING_SCHEMA_VERSION = 2
LEGACY_PROMPT_ROUTING_SCHEMA_VERSION = 1
PROMPT_ROUTE_TAG = re.compile(
    r"\[\s*prompt-route\s*:\s*([a-z][a-z0-9_]{1,63})\s*\]",
    re.IGNORECASE,
)
_ROUTE_KEY = re.compile(r"[a-z][a-z0-9_]{1,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ALLOWED_PROMPT_ROLES = {"legacy_reference", "validated_bank"}
_ALLOWED_SCOPES = {"research_only", "production_opt_in"}
_ALLOWED_GENERAL_ROUTING = {"disabled", "instruction_keywords"}
_ALLOWED_APPROVAL_BASES = {
    "paired_seed_human_review",
    "operator_approved_after_listening",
}
_ALLOWED_AUDIO_ROOTS = (
    "clone_voices",
    "designed_voices",
    "voice_training_projects",
    "experimental_prompts",
    "production_prompt_routes",
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
            "Prompt-route audio must be a safe project-relative path."
        )
    resolved = (root / relative).resolve()
    allowed = [(root / name).resolve() for name in _ALLOWED_AUDIO_ROOTS]
    if not any(resolved.is_relative_to(directory) for directory in allowed):
        raise ExperimentalPromptRoutingError(
            "Prompt-route audio must remain inside an approved project voice directory."
        )
    return resolved


def _instruction_keywords(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ExperimentalPromptRoutingError(f"{label} must be a list.")
    normalized: list[str] = []
    for raw in value:
        keyword = _text(raw, label).casefold()
        keyword = re.sub(r"\s+", " ", keyword).strip()
        if not 2 <= len(keyword) <= 80:
            raise ExperimentalPromptRoutingError(
                f"{label} entries must contain 2 to 80 characters."
            )
        if keyword not in normalized:
            normalized.append(keyword)
    if len(normalized) > 32:
        raise ExperimentalPromptRoutingError(
            f"{label} may contain at most 32 entries."
        )
    return normalized


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

    schema_version = value.get("schema_version")
    if schema_version not in {
        LEGACY_PROMPT_ROUTING_SCHEMA_VERSION,
        PROMPT_ROUTING_SCHEMA_VERSION,
    }:
        raise ExperimentalPromptRoutingError(
            "Experimental prompt routing schema is unsupported."
        )
    enabled = value.get("enabled")
    if not isinstance(enabled, bool):
        raise ExperimentalPromptRoutingError(
            "experimental_prompt_routing.enabled must be boolean."
        )

    scope = _text(value.get("scope"), "Experimental prompt routing scope")
    general_routing = _text(
        value.get("general_routing"),
        "Experimental prompt general routing",
    )
    production_allowed = value.get("production_promotion_allowed")
    if not isinstance(production_allowed, bool):
        raise ExperimentalPromptRoutingError(
            "experimental_prompt_routing.production_promotion_allowed must be boolean."
        )

    if schema_version == LEGACY_PROMPT_ROUTING_SCHEMA_VERSION:
        if scope != "research_only":
            raise ExperimentalPromptRoutingError(
                "Legacy experimental prompt routing must remain research-only."
            )
        if general_routing != "disabled":
            raise ExperimentalPromptRoutingError(
                "Legacy general prompt routing must remain disabled."
            )
        if production_allowed:
            raise ExperimentalPromptRoutingError(
                "Legacy experimental prompt routing may not enable production promotion."
            )
    else:
        if scope not in _ALLOWED_SCOPES:
            raise ExperimentalPromptRoutingError(
                "Prompt routing scope must be research_only or production_opt_in."
            )
        if general_routing not in _ALLOWED_GENERAL_ROUTING:
            raise ExperimentalPromptRoutingError(
                "Prompt routing must be disabled or use instruction keywords."
            )
        if scope == "research_only":
            if general_routing != "disabled" or production_allowed:
                raise ExperimentalPromptRoutingError(
                    "Research-only prompt routing cannot use automatic routing or production export."
                )
        elif not production_allowed:
            raise ExperimentalPromptRoutingError(
                "Production opt-in prompt routing must explicitly allow production output."
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
        if schema_version == PROMPT_ROUTING_SCHEMA_VERSION:
            route_expected.update(
                {
                    "instruction_keywords",
                    "approval_basis",
                    "operator_approved_at_utc",
                }
            )
        if set(raw_route) != route_expected:
            raise ExperimentalPromptRoutingError(
                f"Experimental prompt route {key} has unexpected fields."
            )

        route_status = _text(
            raw_route.get("status"),
            f"Experimental prompt route {key}.status",
        )
        route_production_allowed = raw_route.get("production_promotion_allowed")
        if not isinstance(route_production_allowed, bool):
            raise ExperimentalPromptRoutingError(
                f"Experimental prompt route {key}.production_promotion_allowed must be boolean."
            )
        if scope == "research_only":
            if route_status != "research_preferred" or route_production_allowed:
                raise ExperimentalPromptRoutingError(
                    f"Experimental prompt route {key} must remain research-only."
                )
        else:
            if route_status != "production_opt_in" or not route_production_allowed:
                raise ExperimentalPromptRoutingError(
                    f"Production prompt route {key} must be explicitly production-opted-in."
                )

        prompt_role = _text(
            raw_route.get("prompt_role"),
            f"Experimental prompt route {key}.prompt_role",
        )
        if prompt_role not in _ALLOWED_PROMPT_ROLES:
            raise ExperimentalPromptRoutingError(
                f"Experimental prompt route {key} has an unsupported prompt role."
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

        instruction_keywords: list[str] = []
        approval_basis: str | None = None
        operator_approved_at_utc: str | None = None
        if schema_version == PROMPT_ROUTING_SCHEMA_VERSION:
            instruction_keywords = _instruction_keywords(
                raw_route.get("instruction_keywords"),
                f"Experimental prompt route {key}.instruction_keywords",
            )
            approval_basis = _text(
                raw_route.get("approval_basis"),
                f"Experimental prompt route {key}.approval_basis",
            )
            if approval_basis not in _ALLOWED_APPROVAL_BASES:
                raise ExperimentalPromptRoutingError(
                    f"Experimental prompt route {key} has an unsupported approval basis."
                )
            operator_approved_at_utc = _text(
                raw_route.get("operator_approved_at_utc"),
                f"Experimental prompt route {key}.operator_approved_at_utc",
            )
            if (
                scope == "production_opt_in"
                and general_routing == "instruction_keywords"
                and not instruction_keywords
            ):
                raise ExperimentalPromptRoutingError(
                    f"Production prompt route {key} requires instruction keywords."
                )

        normalized_route = {
            "status": route_status,
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
            "production_promotion_allowed": route_production_allowed,
        }
        if schema_version == PROMPT_ROUTING_SCHEMA_VERSION:
            normalized_route.update(
                {
                    "instruction_keywords": instruction_keywords,
                    "approval_basis": approval_basis,
                    "operator_approved_at_utc": operator_approved_at_utc,
                }
            )
        normalized_routes[key] = normalized_route

    return {
        "schema_version": schema_version,
        "enabled": enabled,
        "scope": scope,
        "general_routing": general_routing,
        "production_promotion_allowed": production_allowed,
        "evidence_round_id": evidence_round_id,
        "routes": normalized_routes,
    }


def _automatic_route_key(
    policy: dict[str, Any],
    instruction: str,
) -> str | None:
    if policy.get("general_routing") != "instruction_keywords":
        return None
    text = strip_prompt_route_tag(instruction).casefold()
    text = re.sub(r"\s+", " ", text).strip()
    scored: list[tuple[int, str]] = []
    for key, route in policy["routes"].items():
        keywords = route.get("instruction_keywords") or []
        score = sum(1 for keyword in keywords if keyword in text)
        if score > 0:
            scored.append((score, key))
    if not scored:
        return None
    best_score = max(score for score, _ in scored)
    winners = sorted(key for score, key in scored if score == best_score)
    return winners[0] if len(winners) == 1 else None


def experimental_prompt_chunk_fields(
    selection: dict[str, Any] | None,
) -> dict[str, Any]:
    if selection is None:
        return {
            "audio_research_only": False,
            "audio_production_prompt_approved": False,
            "experimental_prompt_route": None,
            "experimental_prompt_role": None,
            "experimental_prompt_mapping_reason": None,
            "experimental_prompt_evidence_round_id": None,
            "experimental_prompt_routing_fingerprint": None,
            "experimental_prompt_reference_sha256": None,
            "production_promotion_allowed": False,
        }
    production_allowed = bool(selection["production_promotion_allowed"])
    return {
        "audio_research_only": not production_allowed,
        "audio_production_prompt_approved": production_allowed,
        "experimental_prompt_route": selection["route_key"],
        "experimental_prompt_role": selection["prompt_role"],
        "experimental_prompt_mapping_reason": selection["mapping_reason"],
        "experimental_prompt_evidence_round_id": selection[
            "evidence_round_id"
        ],
        "experimental_prompt_routing_fingerprint": selection[
            "routing_fingerprint"
        ],
        "experimental_prompt_reference_sha256": selection[
            "ref_audio_sha256"
        ],
        "production_promotion_allowed": production_allowed,
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

    explicit_route = parse_prompt_route(instruction)
    if explicit_route is not None:
        route_key = explicit_route
        mapping_reason = "explicit_tag"
    else:
        route_key = _automatic_route_key(policy, instruction)
        mapping_reason = "instruction_keyword_match"
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
        "mapping_reason": mapping_reason,
        "scope": policy["scope"],
        "status": route["status"],
        "production_promotion_allowed": bool(
            policy["production_promotion_allowed"]
            and route["production_promotion_allowed"]
        ),
    }
