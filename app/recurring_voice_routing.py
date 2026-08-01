from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from experimental_prompt_routing import parse_prompt_route, sha256_file, strip_prompt_route_tag
from voice_effects import validate_voice_effect_chain


ROUTING_SCHEMA_VERSION = 1
ROUTED_CLONE_BACKEND = "alexandria_responsive_router"
ROUTE_APPROVAL_TIERS = frozenset(
    {
        "strict",
        "restricted_user_accepted",
        "operator_approved_scores_incomplete",
    }
)
ALLOWED_BACKENDS = frozenset(
    {
        "fish_s2_pro_cloud",
        "indextts2_matched_control",
        "voxcpm2_controllable_clone",
        "qwen3_instruction_controlled",
    }
)
ALLOWED_FALLBACK_BACKENDS = frozenset(
    {"qwen3_instruction_controlled", "qwen3_base"}
)
_ALLOWED_AUDIO_ROOTS = frozenset(
    {"clone_voices", "production_prompt_routes"}
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ROUTE_KEY = re.compile(r"[a-z][a-z0-9_]{1,63}")


class RecurringVoiceRoutingError(RuntimeError):
    pass


def routing_fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise RecurringVoiceRoutingError(f"{label} must be text.")
    text = value.strip()
    if not text and not allow_empty:
        raise RecurringVoiceRoutingError(f"{label} must not be empty.")
    return text


def _route_key(value: Any, label: str) -> str:
    key = _text(value, label).casefold().replace("-", "_").replace(" ", "_")
    if not _ROUTE_KEY.fullmatch(key):
        raise RecurringVoiceRoutingError(f"{label} is invalid.")
    return key


def _sha256(value: Any, label: str) -> str:
    fingerprint = _text(value, label)
    if not _SHA256.fullmatch(fingerprint):
        raise RecurringVoiceRoutingError(
            f"{label} must be a lowercase SHA-256 fingerprint."
        )
    return fingerprint


def _keywords(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise RecurringVoiceRoutingError(f"{label} must be a list.")
    result: list[str] = []
    for raw in value:
        keyword = re.sub(r"\s+", " ", _text(raw, label).casefold()).strip()
        if not 2 <= len(keyword) <= 80:
            raise RecurringVoiceRoutingError(
                f"{label} entries must contain 2 to 80 characters."
            )
        if keyword not in result:
            result.append(keyword)
    if len(result) > 32:
        raise RecurringVoiceRoutingError(f"{label} may contain at most 32 entries.")
    return result


def _resolve_audio(
    *,
    project_root: str | Path,
    value: Any,
    label: str,
    expected_sha256: str,
    verify_audio: bool,
) -> tuple[str, Path]:
    relative_text = _text(value, label)
    relative = Path(relative_text)
    if relative.is_absolute() or ".." in relative.parts:
        raise RecurringVoiceRoutingError(f"{label} must be a safe project-relative path.")
    if not relative.parts or relative.parts[0] not in _ALLOWED_AUDIO_ROOTS:
        raise RecurringVoiceRoutingError(
            f"{label} must remain inside clone_voices or production_prompt_routes."
        )
    root = Path(project_root).expanduser().resolve()
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RecurringVoiceRoutingError(f"{label} escaped the project root.") from exc
    if verify_audio:
        if not resolved.is_file():
            raise RecurringVoiceRoutingError(f"{label} is missing: {resolved}")
        actual = sha256_file(resolved)
        if actual != expected_sha256:
            raise RecurringVoiceRoutingError(
                f"{label} changed; expected {expected_sha256}, got {actual}."
            )
    return relative.as_posix(), resolved


def _validate_control(backend: str, value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RecurringVoiceRoutingError(f"{label} must be an object.")
    control = dict(value)
    if backend == "fish_s2_pro_cloud":
        legacy_expected = {
            "reference_id",
            "api_model_header",
            "prompt_mode",
            "tag",
            "temperature",
            "top_p",
            "repetition_penalty",
        }
        zero_shot_expected = {
            "reference_mode",
            "api_model_header",
            "prompt_mode",
            "tag",
            "temperature",
            "top_p",
            "repetition_penalty",
        }
        control_fields = set(control)
        if control_fields != legacy_expected and control_fields != zero_shot_expected:
            raise RecurringVoiceRoutingError(f"{label} has unexpected Fish fields.")
        prompt_mode = _text(control["prompt_mode"], f"{label}.prompt_mode")
        if prompt_mode not in {
            "untagged",
            "simple_tag",
            "rich_tag",
            "full_alexandria_tag",
        }:
            raise RecurringVoiceRoutingError(f"{label}.prompt_mode is unsupported.")
        tag = _text(control["tag"], f"{label}.tag", allow_empty=True)
        if prompt_mode != "untagged" and not tag:
            raise RecurringVoiceRoutingError(f"{label}.tag is required.")
        normalized = {
            "api_model_header": _text(
                control["api_model_header"], f"{label}.api_model_header"
            ),
            "prompt_mode": prompt_mode,
            "tag": tag,
            "temperature": float(control["temperature"]),
            "top_p": float(control["top_p"]),
            "repetition_penalty": float(control["repetition_penalty"]),
        }
        if control_fields == legacy_expected:
            normalized["reference_id"] = _text(
                control["reference_id"],
                f"{label}.reference_id",
            )
        else:
            reference_mode = _text(
                control["reference_mode"],
                f"{label}.reference_mode",
            )
            if reference_mode != "inline_zero_shot":
                raise RecurringVoiceRoutingError(
                    f"{label}.reference_mode must be inline_zero_shot."
                )
            normalized["reference_mode"] = reference_mode
        return normalized
    if backend == "indextts2_matched_control":
        expected = {
            "emotion_strength",
            "diffusion_steps",
            "num_beams",
            "greedy",
            "max_mel_tokens",
        }
        if set(control) != expected:
            raise RecurringVoiceRoutingError(f"{label} has unexpected IndexTTS2 fields.")
        strength = float(control["emotion_strength"])
        if not 0.0 <= strength <= 1.0:
            raise RecurringVoiceRoutingError(
                f"{label}.emotion_strength must be within [0, 1]."
            )
        steps = int(control["diffusion_steps"])
        beams = int(control["num_beams"])
        if steps < 1 or beams != 1 or control["greedy"] is not True:
            raise RecurringVoiceRoutingError(
                f"{label} must use positive diffusion steps, greedy decoding, and one beam."
            )
        return {
            "emotion_strength": strength,
            "diffusion_steps": steps,
            "num_beams": beams,
            "greedy": True,
            "max_mel_tokens": int(control["max_mel_tokens"]),
        }
    if backend == "voxcpm2_controllable_clone":
        expected = {
            "instruction",
            "cfg_value",
            "inference_timesteps",
            "warmup_patches",
            "max_tokens",
        }
        if set(control) != expected:
            raise RecurringVoiceRoutingError(f"{label} has unexpected VoxCPM2 fields.")
        return {
            "instruction": _text(control["instruction"], f"{label}.instruction"),
            "cfg_value": float(control["cfg_value"]),
            "inference_timesteps": int(control["inference_timesteps"]),
            "warmup_patches": int(control["warmup_patches"]),
            "max_tokens": int(control["max_tokens"]),
        }
    if backend == "qwen3_instruction_controlled":
        if control:
            raise RecurringVoiceRoutingError(f"{label} must be empty for Qwen fallback.")
        return {}
    raise RecurringVoiceRoutingError(f"Unsupported routed backend: {backend}.")


def validate_recurring_voice_routing(
    value: Any,
    *,
    project_root: str | Path,
    verify_audio: bool = False,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RecurringVoiceRoutingError("responsive_backend_routing must be an object.")
    expected = {
        "schema_version",
        "enabled",
        "default_route",
        "fallback_backend",
        "evidence_round_id",
        "production_promotion_allowed",
        "routes",
    }
    if set(value) != expected:
        raise RecurringVoiceRoutingError(
            "responsive_backend_routing has unexpected fields."
        )
    if value.get("schema_version") != ROUTING_SCHEMA_VERSION:
        raise RecurringVoiceRoutingError("Responsive backend routing schema is unsupported.")
    if not isinstance(value.get("enabled"), bool):
        raise RecurringVoiceRoutingError("responsive_backend_routing.enabled must be boolean.")
    if value.get("production_promotion_allowed") is not True:
        raise RecurringVoiceRoutingError(
            "Responsive backend routing must be explicitly production-approved."
        )
    fallback = _text(value.get("fallback_backend"), "Responsive fallback backend")
    if fallback not in ALLOWED_FALLBACK_BACKENDS:
        raise RecurringVoiceRoutingError("Responsive fallback backend is unsupported.")
    routes_value = value.get("routes")
    if not isinstance(routes_value, dict) or not routes_value:
        raise RecurringVoiceRoutingError("Responsive backend routes must be a non-empty object.")

    routes: dict[str, dict[str, Any]] = {}
    for raw_key, raw_route in routes_value.items():
        key = _route_key(raw_key, "Responsive backend route key")
        if not isinstance(raw_route, dict):
            raise RecurringVoiceRoutingError(f"Responsive backend route {key} must be an object.")
        route_required = {
            "backend",
            "instruction_keywords",
            "identity_audio",
            "identity_audio_sha256",
            "identity_text",
            "performance_audio",
            "performance_audio_sha256",
            "performance_text",
            "control",
            "production_promotion_allowed",
        }
        route_allowed = route_required | {"effect_chain", "approval_tier"}
        if not route_required <= set(raw_route) or not set(raw_route) <= route_allowed:
            raise RecurringVoiceRoutingError(
                f"Responsive backend route {key} has unexpected fields."
            )
        backend = _text(raw_route.get("backend"), f"Route {key}.backend")
        if backend not in ALLOWED_BACKENDS:
            raise RecurringVoiceRoutingError(f"Route {key} backend is unsupported.")
        if raw_route.get("production_promotion_allowed") is not True:
            raise RecurringVoiceRoutingError(f"Route {key} is not production-approved.")
        identity_sha = _sha256(
            raw_route.get("identity_audio_sha256"),
            f"Route {key}.identity_audio_sha256",
        )
        identity_relative, _identity_resolved = _resolve_audio(
            project_root=project_root,
            value=raw_route.get("identity_audio"),
            label=f"Route {key}.identity_audio",
            expected_sha256=identity_sha,
            verify_audio=verify_audio,
        )
        performance_value = raw_route.get("performance_audio")
        performance_sha_value = raw_route.get("performance_audio_sha256")
        performance_relative: str | None = None
        performance_resolved: Path | None = None
        performance_sha: str | None = None
        performance_text_value = raw_route.get("performance_text")
        performance_text: str | None = None
        if (
            performance_value is not None
            or performance_sha_value is not None
            or performance_text_value is not None
        ):
            if (
                performance_value is None
                or performance_sha_value is None
                or performance_text_value is None
            ):
                raise RecurringVoiceRoutingError(
                    f"Route {key} must provide performance audio, fingerprint, and transcript together."
                )
            performance_sha = _sha256(
                performance_sha_value,
                f"Route {key}.performance_audio_sha256",
            )
            performance_relative, performance_resolved = _resolve_audio(
                project_root=project_root,
                value=performance_value,
                label=f"Route {key}.performance_audio",
                expected_sha256=performance_sha,
                verify_audio=verify_audio,
            )
            performance_text = _text(
                performance_text_value,
                f"Route {key}.performance_text",
            )
        if backend == "indextts2_matched_control" and performance_relative is None:
            raise RecurringVoiceRoutingError(
                f"IndexTTS2 route {key} requires a same-character performance reference."
            )
        approval_tier = raw_route.get("approval_tier", "strict")
        if approval_tier not in ROUTE_APPROVAL_TIERS:
            raise RecurringVoiceRoutingError(
                f"Route {key}.approval_tier is unsupported."
            )
        routes[key] = {
            "backend": backend,
            "instruction_keywords": _keywords(
                raw_route.get("instruction_keywords"),
                f"Route {key}.instruction_keywords",
            ),
            "identity_audio": identity_relative,
            "identity_audio_sha256": identity_sha,
            "identity_text": _text(raw_route.get("identity_text"), f"Route {key}.identity_text"),
            "performance_audio": performance_relative,
            "performance_audio_sha256": performance_sha,
            "performance_text": performance_text,
            "control": _validate_control(backend, raw_route.get("control"), f"Route {key}.control"),
            "effect_chain": validate_voice_effect_chain(
                raw_route.get("effect_chain")
            ),
            "approval_tier": approval_tier,
            "production_promotion_allowed": True,
        }

    default_route = _route_key(value.get("default_route"), "Responsive default route")
    if default_route not in routes:
        raise RecurringVoiceRoutingError("Responsive default route does not exist.")
    normalized = {
        "schema_version": ROUTING_SCHEMA_VERSION,
        "enabled": bool(value["enabled"]),
        "default_route": default_route,
        "fallback_backend": fallback,
        "evidence_round_id": _text(
            value.get("evidence_round_id"), "Responsive routing evidence round"
        ),
        "production_promotion_allowed": True,
        "routes": routes,
    }
    return normalized


def _keyword_match(text: str, keyword: str) -> bool:
    escaped = re.escape(keyword).replace(r"\ ", r"\s+")
    return re.search(rf"(?<!\w){escaped}(?!\w)", text) is not None


def resolve_recurring_voice_route(
    *,
    voice_data: Mapping[str, Any],
    instruction: str,
    project_root: str | Path,
    verify_audio: bool = True,
) -> dict[str, Any] | None:
    raw = voice_data.get("responsive_backend_routing")
    if raw is None:
        return None
    policy = validate_recurring_voice_routing(
        raw,
        project_root=project_root,
        verify_audio=verify_audio,
    )
    if not policy["enabled"]:
        return None
    explicit = parse_prompt_route(instruction)
    mapping_reason = "default_route"
    if explicit is not None:
        if explicit not in policy["routes"]:
            raise RecurringVoiceRoutingError(
                f"No responsive backend route exists for {explicit!r}."
            )
        selected_key = explicit
        mapping_reason = "explicit_tag"
    else:
        text = re.sub(
            r"\s+", " ", strip_prompt_route_tag(instruction).casefold()
        ).strip()
        scored: list[tuple[tuple[int, int, int], str]] = []
        for key, route in policy["routes"].items():
            matched = [
                keyword
                for keyword in route["instruction_keywords"]
                if _keyword_match(text, keyword)
            ]
            if not matched:
                continue
            score = (
                sum(len(keyword.split()) for keyword in matched),
                sum(len(keyword) for keyword in matched),
                len(matched),
            )
            scored.append((score, key))
        if scored:
            best = max(score for score, _ in scored)
            winners = sorted(key for score, key in scored if score == best)
            selected_key = winners[0] if len(winners) == 1 else policy["default_route"]
            mapping_reason = (
                "instruction_keyword_match"
                if len(winners) == 1
                else "ambiguous_keywords_defaulted"
            )
        else:
            selected_key = policy["default_route"]
    selected = dict(policy["routes"][selected_key])
    identity_relative, identity_resolved = _resolve_audio(
        project_root=project_root,
        value=selected["identity_audio"],
        label=f"Route {selected_key}.identity_audio",
        expected_sha256=selected["identity_audio_sha256"],
        verify_audio=verify_audio,
    )
    performance_resolved: Path | None = None
    if selected.get("performance_audio"):
        _, performance_resolved = _resolve_audio(
            project_root=project_root,
            value=selected["performance_audio"],
            label=f"Route {selected_key}.performance_audio",
            expected_sha256=selected["performance_audio_sha256"],
            verify_audio=verify_audio,
        )
    selected.update(
        {
            "route_key": selected_key,
            "identity_audio": identity_relative,
            "identity_audio_path": str(identity_resolved),
            "performance_audio_path": (
                str(performance_resolved) if performance_resolved is not None else None
            ),
            "fallback_backend": policy["fallback_backend"],
            "evidence_round_id": policy["evidence_round_id"],
            "routing_fingerprint": routing_fingerprint(policy),
            "mapping_reason": mapping_reason,
        }
    )
    return selected


def recurring_voice_chunk_fields(selection: Mapping[str, Any] | None) -> dict[str, Any]:
    if selection is None:
        return {
            "responsive_voice_route": None,
            "responsive_voice_backend": None,
            "responsive_voice_fallback_backend": None,
            "responsive_voice_used_backend": None,
            "responsive_voice_fallback_used": False,
            "responsive_voice_backend_error": None,
            "responsive_voice_specialist_attempt_count": None,
            "responsive_voice_repair_strategy": None,
            "responsive_voice_text_verification": None,
            "responsive_voice_mapping_reason": None,
            "responsive_voice_evidence_round_id": None,
            "responsive_voice_routing_fingerprint": None,
            "responsive_voice_effect_chain": None,
            "responsive_voice_approval_tier": None,
        }
    return {
        "responsive_voice_route": selection.get("route_key"),
        "responsive_voice_backend": selection.get("backend"),
        "responsive_voice_fallback_backend": selection.get("fallback_backend"),
        "responsive_voice_used_backend": None,
        "responsive_voice_fallback_used": False,
        "responsive_voice_backend_error": None,
        "responsive_voice_specialist_attempt_count": None,
        "responsive_voice_repair_strategy": None,
        "responsive_voice_text_verification": None,
        "responsive_voice_mapping_reason": selection.get("mapping_reason"),
        "responsive_voice_evidence_round_id": selection.get("evidence_round_id"),
        "responsive_voice_routing_fingerprint": selection.get("routing_fingerprint"),
        "responsive_voice_effect_chain": selection.get("effect_chain"),
        "responsive_voice_approval_tier": selection.get("approval_tier"),
    }
