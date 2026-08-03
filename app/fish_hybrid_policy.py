from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from fish_cloud_tts import FishPromptRoute, build_prompt_route


FISH_HYBRID_STYLE_IDS = frozenset({"fear", "grief", "sarcasm", "expressive"})
DEFAULT_FISH_HYBRID_STYLES = (
    "fear",
    "grief",
    "sarcasm",
    "expressive",
)
FISH_HYBRID_POLICY_FIELDS = frozenset(
    {
        "fish_hybrid_enabled",
        "fish_hybrid_styles",
        "fish_hybrid_use_approved_routes",
        "fish_hybrid_fallback_to_local",
    }
)


@dataclass(frozen=True)
class FishHybridDecision:
    use_fish: bool
    route: FishPromptRoute
    reason: str


def _styles(value: Any) -> tuple[str, ...]:
    raw = value if isinstance(value, (list, tuple, set)) else DEFAULT_FISH_HYBRID_STYLES
    result: list[str] = []
    for item in raw:
        style = str(item or "").strip().casefold()
        if style in FISH_HYBRID_STYLE_IDS and style not in result:
            result.append(style)
    return tuple(result or DEFAULT_FISH_HYBRID_STYLES)


def normalized_fish_hybrid_policy(
    voice_data: Mapping[str, Any] | None,
) -> dict[str, Any]:
    voice = dict(voice_data or {})
    return {
        "enabled": bool(voice.get("fish_hybrid_enabled", False)),
        "styles": list(_styles(voice.get("fish_hybrid_styles"))),
        "use_approved_routes": bool(
            voice.get("fish_hybrid_use_approved_routes", True)
        ),
        "fallback_to_local": bool(
            voice.get("fish_hybrid_fallback_to_local", True)
        ),
    }


def fish_hybrid_decision(
    *,
    voice_data: Mapping[str, Any] | None,
    text: str,
    instruction: str,
    approved_prompt_selected: bool,
) -> FishHybridDecision:
    policy = normalized_fish_hybrid_policy(voice_data)
    route = build_prompt_route(text, instruction)
    if not policy["enabled"]:
        return FishHybridDecision(False, route, "policy_disabled")
    if str((voice_data or {}).get("type") or "custom") != "clone":
        return FishHybridDecision(False, route, "not_clone")
    if approved_prompt_selected and policy["use_approved_routes"]:
        return FishHybridDecision(True, route, "approved_prompt_route")
    if route.style in set(policy["styles"]):
        return FishHybridDecision(True, route, f"style:{route.style}")
    return FishHybridDecision(False, route, f"style_not_selected:{route.style}")


def apply_fish_hybrid_policy(
    voice_data: Mapping[str, Any],
    *,
    enabled: bool,
) -> dict[str, Any]:
    voice = dict(voice_data)
    if enabled:
        voice.update(
            {
                "fish_hybrid_enabled": True,
                "fish_hybrid_styles": list(DEFAULT_FISH_HYBRID_STYLES),
                "fish_hybrid_use_approved_routes": True,
                "fish_hybrid_fallback_to_local": True,
            }
        )
    else:
        for field in FISH_HYBRID_POLICY_FIELDS:
            voice.pop(field, None)
    return voice


def eligible_for_fish_hybrid(voice_data: Mapping[str, Any] | None) -> bool:
    voice = dict(voice_data or {})
    return bool(
        voice.get("type") == "clone"
        and not voice.get("alias_of")
        and str(voice.get("ref_audio") or "").strip()
        and str(voice.get("ref_text") or "").strip()
    )
