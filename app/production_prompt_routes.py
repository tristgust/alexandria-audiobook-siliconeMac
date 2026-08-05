from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import soundfile as sf

from audio_invalidation import apply_project_audio_invalidation
from controlled_clone_preview import build_controlled_clone_configuration_fingerprint
from chris_roz_recurring_voices import (
    ALIASES as CHRIS_ROZ_VOICE_ALIASES,
    VOICE_NAMES as CHRIS_ROZ_VOICES,
)
from experimental_prompt_routing import (
    PROMPT_ROUTING_SCHEMA_VERSION,
    prompt_routing_fingerprint,
    sha256_file,
    validate_experimental_prompt_routing,
)
from generation_state import atomic_json_write, fingerprint_value
from model_registry import (
    INSTRUCTION_CONTROLLED_ENGINE_ID,
    STANDARD_CLONE_ENGINE_ID,
)
from recurring_voice_routing import (
    ROUTED_CLONE_BACKEND,
    routing_fingerprint as recurring_routing_fingerprint,
    validate_recurring_voice_routing,
)
from voice_aliases import validate_voice_aliases


PACK_ID = "alexandria_primary_responsive_voices_v1"
EVIDENCE_ROUND_ID = "alexandria_three_voice_paired_seed_reliability_review_applied_v1"
BENNY_SOURCE_SHA256 = "2716019d7cc6072ea495176ba97997f3a47de2d5cf4f38d5228c19a43f340f6c"
DOCTOR_SOURCE_SHA256 = "6eac1515ea9b5b5ff697ff8a2a82049c54e40f9d544719d0716e91e0a71b991c"
BENNY_ROUTE_TEXT = "I'm trapped in a pyramid. Yes, a pyramid. My guide's dead."
DOCTOR_ROUTE_TEXT = (
    "Hello, I'm the Doctor, and this is my friend John Watson. Well, Sherlock's "
    "friend John Watson, really, but I don't have one of my own available just now."
)
PRIMARY_VOICES = ("NARRATOR", "BERNICE", "THE DOCTOR")
RECURRING_VOICES = PRIMARY_VOICES + CHRIS_ROZ_VOICES
PRIMARY_VOICE_ALIASES = {
    "DOCTOR": "THE DOCTOR",
    "SEVENTH DOCTOR": "THE DOCTOR",
    "THE SEVENTH DOCTOR": "THE DOCTOR",
    "BENNY": "BERNICE",
    "BERNICE SUMMERFIELD": "BERNICE",
    "NARRATOR (BENNY)": "BERNICE",
}
RECURRING_VOICE_ALIASES = {
    **PRIMARY_VOICE_ALIASES,
    **CHRIS_ROZ_VOICE_ALIASES,
}
PACK_RECEIPT_FILENAME = "primary_responsive_voice_pack.json"
_ALLOWED_PACK_ASSET_ROOTS = frozenset(
    {
        "clone_voices",
        "production_prompt_routes",
        "community_qwen_packs",
    }
)
PRODUCTION_GENERATION_SEED = 130363
EXPRESSIVE_PROMOTION_EVIDENCE_ROUND_ID = (
    "alexandria_three_voice_validated_bank_operator_promotion_v1"
)
DEFAULT_EXPRESSIVE_TARGET_VOICES = {
    "narrator": "NARRATOR",
    "benny": "BERNICE",
    "doctor": "THE DOCTOR",
}
SUPPORTED_EXPRESSIVE_BANK_SCHEMAS = frozenset({1, 3})
APPROVED_EXPRESSIVE_REFERENCE_STATUSES = frozenset(
    {
        "approved_final_boundary_human_validated",
        "approved_source_reference_final",
        "approved_source_reference_human_validated",
        "approved_source_separation_final_boundary_human_validated",
    }
)
NARRATOR_WARM_MANIFEST_SHA256 = (
    "299500067b7947d97f3ec905b11db16c4a0989a525878a830c6e51e2a38ae7f0"
)
NARRATOR_WARM_MANIFEST_TEXT = (
    "That was lovely. No concerns about where it was all going. No confusion. "
    "Just a blank slate. Yes, that's what I want. It's all so fresh in my "
    "memory. They were such wonderful moments."
)
NARRATOR_WARM_ROUTE_TEXT = (
    "That was lovely. No concerns about where it was all going. No confusion. "
    "Just a blank slate. Yes, that's what I want."
)
NARRATOR_WARM_REPAIRED_SHA256 = (
    "3ed8c2d5fc8fc0b2feabd8509c1d5bdafbdc99a2ae916f83484c91913c3e9394"
)
EXPRESSIVE_REFERENCE_REPAIRS: dict[
    tuple[str, str], dict[str, Any]
] = {
    ("narrator", "narrator_demo_warm_nostalgia"): {
        "manifest_audio_sha256": NARRATOR_WARM_MANIFEST_SHA256,
        "manifest_ref_text": NARRATOR_WARM_MANIFEST_TEXT,
        "source": (
            Path(__file__).resolve().parent.parent
            / "production_prompt_routes"
            / "expressive"
            / "narrator"
            / "narrator_demo_warm_nostalgia.wav"
        ),
        "audio_sha256": NARRATOR_WARM_REPAIRED_SHA256,
        "ref_text": NARRATOR_WARM_ROUTE_TEXT,
    },
}
EXPRESSIVE_ROUTE_SUBSTITUTIONS: dict[
    tuple[str, str], dict[str, Any]
] = {
    ("BERNICE", "benny_hesitation_fatalistic_dread"): {
        "manifest_audio_sha256": (
            "c1e66b1b26ff8028be80e228364155b3bb813efed4595948342176926841a4d2"
        ),
        "manifest_ref_text": (
            "It wasn't bad luck that they'd found the Doctor. It was inevitable."
        ),
        "route_key": "credible_fear",
        "route_evidence": {
            "status": "production_opt_in",
            "prompt_role": "legacy_reference",
            "reference_key": "benny-urgent_fear.wav",
            "validated_bank_clip_id": "benny_hesitation_fatalistic_dread",
            "ref_audio": "production_prompt_routes/benny_credible_fear.wav",
            "ref_audio_sha256": BENNY_SOURCE_SHA256,
            "ref_text": BENNY_ROUTE_TEXT,
            "production_promotion_allowed": True,
            "instruction_keywords": [
                "fatalistic dread",
                "dread",
                "fearful",
                "fear",
                "afraid",
                "frightened",
                "terrified",
                "panic",
                "panicked",
                "uneasy",
                "ominous realization",
                "threat awareness",
            ],
            "approval_basis": "operator_approved_after_listening",
        },
    },
    ("THE DOCTOR", "doctor_acf_playful_introduction"): {
        "manifest_audio_sha256": DOCTOR_SOURCE_SHA256,
        "manifest_ref_text": DOCTOR_ROUTE_TEXT,
        "route_key": "ordinary_identity",
        "route_evidence": {
            "status": "production_opt_in",
            "prompt_role": "validated_bank",
            "reference_key": "doctor_acf_playful_introduction",
            "validated_bank_clip_id": "doctor_acf_playful_introduction",
            "ref_audio": "production_prompt_routes/doctor_playful_identity.wav",
            "ref_audio_sha256": DOCTOR_SOURCE_SHA256,
            "ref_text": DOCTOR_ROUTE_TEXT,
            "production_promotion_allowed": True,
            "instruction_keywords": [
                "playful",
                "playfully",
                "dryly amused",
                "dry amusement",
                "wry",
                "eccentric",
                "comic",
                "lightly amused",
                "mischievous",
                "quirky",
                "probing",
                "restlessly thoughtful",
            ],
            "approval_basis": "operator_approved_after_listening",
        },
    },
}
EXPRESSIVE_ROUTE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "narrator_demo_warm_nostalgia": (
        "warm narration",
        "warm third-person",
        "warm period",
        "warm nostalgia",
        "tender",
        "affectionate",
        "fond",
        "gentle",
        "soften into wonder",
        "thoughtful and intimate",
        "intimate and restrained",
        "physical closeness emotionally specific",
        "dawning concern",
    ),
    "narrator_official_rallying_determination": (
        "rallying",
        "determined",
        "determination",
        "resolute",
        "resolve",
        "defiant",
        "formal authority",
    ),
    "narrator_skip_abandonment_terror": (
        "abandonment",
        "abandoned",
        "betrayal",
        "betrayed",
        "rejected",
        "resentful helplessness",
    ),
    "narrator_skip_desperate_surrender": (
        "desperate",
        "desperation",
        "pleading",
        "surrender",
        "helpless",
    ),
    "narrator_skip_existential_dread": (
        "existential dread",
        "dread",
        "foreboding",
        "tense suspense",
        "suspense",
        "afraid",
        "fearful",
        "terrified",
        "bitter despair",
        "tense narrative drive",
        "tense and unsparing",
        "violent image",
    ),
    "narrator_skip_lonely_deprivation": (
        "lonely",
        "loneliness",
        "isolated",
        "isolation",
        "emotional deprivation",
    ),
    "narrator_ud_bittersweet_nostalgia": (
        "grief-weighted",
        "grief",
        "grieving",
        "sorrow",
        "sorrowful",
        "mourning",
        "mournful",
        "regret",
        "regretful",
        "bittersweet",
        "sadness",
        "painful entry",
        "reflective and increasingly troubled",
        "increasingly troubled",
    ),
    "narrator_ud_contemptuous_disbelief": (
        "contempt",
        "contemptuous",
        "derisive",
        "scolding",
        "incredulous",
        "disbelief",
    ),
    "narrator_ud_creative_insecurity": (
        "dry comic",
        "comic narration",
        "social comedy",
        "dryly amused narration",
        "lightly amused",
        "self-conscious",
        "self conscious",
        "creative insecurity",
        "dry narrative understatement",
        "brisk comic attribution",
        "absurd or comic detail",
    ),
    "narrator_ud_ecstatic_bucket_affection": (
        "ecstatic",
        "overjoyed",
        "delighted",
        "joyful",
        "joy",
        "possessive delight",
    ),
    "narrator_ud_explosive_indignation": (
        "explosive indignation",
        "indignant",
        "anger",
        "angry",
        "furious",
        "fury",
        "rage",
        "resentment",
        "resentful",
        "offended",
    ),
    "narrator_ud_manic_victory": (
        "manic",
        "triumph",
        "triumphant",
        "victory",
        "victorious",
        "grandiose",
    ),
    "narrator_ud_petulant_hurt": (
        "petulant",
        "sulking",
        "childish hurt",
        "wounded pride",
        "immediate remorse",
    ),
    "narrator_ud_separation_panic": (
        "separation panic",
        "panicked",
        "panic",
        "frantic",
        "refusing an ending",
    ),
    "narrator_ud_shame_and_guilt": (
        "shame",
        "ashamed",
        "guilt",
        "guilty",
        "deflated shame",
        "taking responsibility",
    ),
    "narrator_ud_warm_reconciliation": (
        "reconciliation",
        "reconcile",
        "forgiving",
        "forgiveness",
        "repairing a relationship",
        "hopeful repair",
    ),
    "benny_criminal_incredulous_concern": (
        "curious",
        "skeptical",
        "sceptical",
        "searching but composed",
        "wary concern",
        "concerned but composed",
        "inquisitive",
        "investigative tension",
        "emotionally alert",
        "attentive but doubtful",
        "doubtful",
    ),
    "benny_criminal_moral_authority": (
        "grave authority",
        "moral challenge",
        "authoritative",
        "authority",
        "commanding",
        "decisive",
        "indignant",
        "angry",
        "controlled and purposeful",
        "project clearly and urgently",
        "urgently",
        "decisively",
        "controlled warning",
    ),
    "benny_criminal_restrained_relief": (
        "restrained relief",
        "relieved",
        "relief",
        "reassurance after danger",
    ),
    "benny_criminal_sardonic_concern": (
        "dry, self-aware",
        "dry self-aware",
        "dryly inquisitive",
        "restrained irony",
        "dry sarcasm",
        "sarcastic",
        "sardonic",
        "dry amusement",
        "dryly amused",
        "underplay the punch line",
        "dryly observant and conversational",
        "underlying irony",
    ),
    "benny_diary_buoyant_confidence": (
        "buoyant",
        "confident",
        "confidence",
        "exuberant",
        "intimate playfulness",
        "playful",
        "genuinely amused",
        "exuberantly amused",
        "lightly amused",
        "cheerful greeting",
        "warmth spontaneous",
        "affectionate and emotionally open",
        "inviting and conversational",
    ),
    "benny_hesitation_baffled_protest": (
        "baffled",
        "puzzled",
        "confused",
        "confusion",
        "dry irritation",
        "conversational challenge",
        "hesitant and searching",
    ),
    "benny_hesitation_cold_temptation": (
        "cold temptation",
        "dissociated menace",
        "menacing",
        "threatening",
        "possessed internal threat",
        "controlled hostility",
        "press the threat",
    ),
    "benny_hesitation_fatalistic_dread": (
        "fatalistic dread",
        "dread",
        "ominous realization",
    ),
    "benny_hesitation_fearful_vigilance": (
        "fearful vigilance",
        "vigilant",
        "vigilance",
        "uneasy",
        "suspenseful",
        "threat awareness",
        "tense",
    ),
    "benny_hesitation_grave_reflection": (
        "grief-weighted",
        "grief",
        "grieving",
        "grave reflection",
        "reflective and candid",
        "first-person reflection",
        "wit and vulnerability",
        "somber",
        "sombre",
        "mournful",
        "regretful",
        "fearful awe",
        "vulnerable and searching",
        "request for reassurance",
        "very quiet and inward",
    ),
    "doctor_acf_dismissive_contempt": (
        "dismissive contempt",
        "contempt",
        "dismissive",
        "disdain",
        "defiant put-down",
        "threatening",
        "dry irritation",
    ),
    "doctor_acf_fond_reminiscence": (
        "fond warmth",
        "fond nostalgia",
        "fond reminiscence",
        "affectionate",
        "remorseful",
        "compassionate",
        "compassion",
        "warm reminiscence",
    ),
    "doctor_acf_playful_introduction": (
        "playful",
        "playfully",
        "probing",
        "restlessly thoughtful",
        "quick-minded",
        "inquisitive",
    ),
    "doctor_comic_disorientation": (
        "comic disorientation",
        "disoriented",
        "disorientation",
        "puzzled",
        "confused",
        "confusion",
        "hesitant and searching",
        "masking confusion",
        "strangely knowing",
    ),
    "doctor_indomitable_determination": (
        "indomitable determination",
        "determined",
        "determination",
        "controlled and purposeful",
        "purposeful",
        "decisive",
        "commanding",
        "urgent",
        "urgently",
        "immediate",
        "moral conviction",
        "resolute",
        "heightened and emphatic",
    ),
}


class ProductionPromptRouteError(RuntimeError):
    pass


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductionPromptRouteError(f"{label} could not be read: {exc}") from exc
    if not isinstance(value, dict):
        raise ProductionPromptRouteError(f"{label} must contain a JSON object.")
    return value


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as input_handle, temporary.open("wb") as output_handle:
            for block in iter(lambda: input_handle.read(1024 * 1024), b""):
                output_handle.write(block)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _atomic_restore(path: Path, value: bytes | None) -> None:
    if value is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".restore.tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _validate_source(path: str | Path, expected_sha256: str, label: str) -> Path:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ProductionPromptRouteError(f"{label} is missing: {source}")
    actual = sha256_file(source)
    if actual != expected_sha256:
        raise ProductionPromptRouteError(
            f"{label} fingerprint is wrong; expected {expected_sha256}, got {actual}."
        )
    return source


def _route_policy(
    *,
    route_key: str,
    prompt_role: str,
    reference_key: str,
    validated_bank_clip_id: str,
    relative_audio: str,
    audio_sha256: str,
    ref_text: str,
    instruction_keywords: list[str],
    approved_at_utc: str,
) -> dict[str, Any]:
    policy = {
        "schema_version": PROMPT_ROUTING_SCHEMA_VERSION,
        "enabled": True,
        "scope": "production_opt_in",
        "general_routing": "instruction_keywords",
        "production_promotion_allowed": True,
        "evidence_round_id": EVIDENCE_ROUND_ID,
        "routes": {
            route_key: {
                "status": "production_opt_in",
                "prompt_role": prompt_role,
                "reference_key": reference_key,
                "validated_bank_clip_id": validated_bank_clip_id,
                "ref_audio": relative_audio,
                "ref_audio_sha256": audio_sha256,
                "ref_text": ref_text,
                "production_promotion_allowed": True,
                "instruction_keywords": instruction_keywords,
                "approval_basis": "operator_approved_after_listening",
                "operator_approved_at_utc": approved_at_utc,
            }
        },
    }
    return policy


def build_primary_responsive_voice_policies(
    *,
    project_root: str | Path,
    approved_at_utc: str,
) -> dict[str, dict[str, Any]]:
    root = Path(project_root).expanduser().resolve()
    policies = {
        "BERNICE": _route_policy(
            route_key="credible_fear",
            prompt_role="legacy_reference",
            reference_key="benny-urgent_fear.wav",
            validated_bank_clip_id="benny_hesitation_fatalistic_dread",
            relative_audio="production_prompt_routes/benny_credible_fear.wav",
            audio_sha256=BENNY_SOURCE_SHA256,
            ref_text=BENNY_ROUTE_TEXT,
            instruction_keywords=[
                "fatalistic dread",
                "dread",
                "fearful",
                "fear",
                "afraid",
                "frightened",
                "terrified",
                "panic",
                "panicked",
                "uneasy",
                "ominous realization",
                "threat awareness",
            ],
            approved_at_utc=approved_at_utc,
        ),
        "THE DOCTOR": _route_policy(
            route_key="ordinary_identity",
            prompt_role="validated_bank",
            reference_key="doctor_acf_playful_introduction",
            validated_bank_clip_id="doctor_acf_playful_introduction",
            relative_audio="production_prompt_routes/doctor_playful_identity.wav",
            audio_sha256=DOCTOR_SOURCE_SHA256,
            ref_text=DOCTOR_ROUTE_TEXT,
            instruction_keywords=[
                "playful",
                "playfully",
                "dryly amused",
                "dry amusement",
                "wry",
                "eccentric",
                "comic",
                "lightly amused",
                "mischievous",
                "quirky",
                "probing",
                "restlessly thoughtful",
            ],
            approved_at_utc=approved_at_utc,
        ),
    }
    for voice_name, policy in policies.items():
        policies[voice_name] = validate_experimental_prompt_routing(
            policy,
            project_root=root,
            verify_audio=True,
        )
    return policies


def _controlled_clone_fingerprint(
    *,
    root: Path,
    voice: dict[str, Any],
) -> str:
    controlled = build_controlled_clone_configuration_fingerprint(
        root_dir=root,
        ref_audio=str(voice.get("ref_audio") or ""),
        ref_text=str(voice.get("ref_text") or ""),
        character_style=str(
            voice.get("character_style")
            or voice.get("default_style")
            or ""
        ),
        temperature=float(voice.get("instruction_clone_temperature", 0.75)),
        top_k=int(voice.get("instruction_clone_top_k", 50)),
        top_p=float(voice.get("instruction_clone_top_p", 0.95)),
        repetition_penalty=float(
            voice.get("instruction_clone_repetition_penalty", 1.5)
        ),
        max_tokens=int(voice.get("instruction_clone_max_tokens", 2000)),
        seed=int(voice.get("seed", -1)),
    )
    policy = voice.get("experimental_prompt_routing")
    if policy is None:
        return controlled
    validated = validate_experimental_prompt_routing(
        policy,
        project_root=root,
        verify_audio=True,
    )
    return fingerprint_value(
        {
            "controlled_clone": controlled,
            "experimental_prompt_routing": prompt_routing_fingerprint(validated),
        }
    )


def _upgrade_voice(
    *,
    root: Path,
    voice_name: str,
    source: dict[str, Any],
    policy: dict[str, Any] | None,
) -> dict[str, Any]:
    if source.get("type") != "clone":
        raise ProductionPromptRouteError(
            f"{voice_name} must already be a supplied-recording clone."
        )
    if not str(source.get("ref_audio") or "").strip():
        raise ProductionPromptRouteError(
            f"{voice_name} has no reference audio."
        )
    if not str(source.get("ref_text") or "").strip():
        raise ProductionPromptRouteError(
            f"{voice_name} has no exact reference transcript."
        )
    upgraded = copy.deepcopy(source)
    upgraded.update(
        {
            "clone_backend": INSTRUCTION_CONTROLLED_ENGINE_ID,
            "instruction_clone_temperature": float(
                upgraded.get("instruction_clone_temperature", 0.75)
            ),
            "instruction_clone_top_k": int(
                upgraded.get("instruction_clone_top_k", 50)
            ),
            "instruction_clone_top_p": float(
                upgraded.get("instruction_clone_top_p", 0.95)
            ),
            "instruction_clone_repetition_penalty": float(
                upgraded.get("instruction_clone_repetition_penalty", 1.5)
            ),
            "instruction_clone_max_tokens": int(
                upgraded.get("instruction_clone_max_tokens", 2000)
            ),
            "seed": str(PRODUCTION_GENERATION_SEED),
        }
    )
    if policy is None:
        upgraded.pop("experimental_prompt_routing", None)
    else:
        upgraded["experimental_prompt_routing"] = policy
    upgraded["controlled_clone_configuration_fingerprint"] = (
        _controlled_clone_fingerprint(root=root, voice=upgraded)
    )
    return upgraded


def build_primary_responsive_voice_config(
    *,
    project_root: str | Path,
    voice_config: dict[str, Any],
    approved_at_utc: str,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    policies = build_primary_responsive_voice_policies(
        project_root=root,
        approved_at_utc=approved_at_utc,
    )
    config = copy.deepcopy(voice_config)
    for voice_name in PRIMARY_VOICES:
        voice = config.get(voice_name)
        if not isinstance(voice, dict):
            raise ProductionPromptRouteError(
                f"The required primary voice {voice_name!r} is missing."
            )
        config[voice_name] = _upgrade_voice(
            root=root,
            voice_name=voice_name,
            source=voice,
            policy=policies.get(voice_name),
        )
    return config


def _resolve_pack_asset(
    *,
    root: Path,
    relative_path: Any,
    label: str,
) -> tuple[Path, str]:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ProductionPromptRouteError(f"{label} is missing its audio path.")
    relative = Path(relative_path.strip())
    if relative.is_absolute() or ".." in relative.parts:
        raise ProductionPromptRouteError(
            f"{label} must use a safe project-relative audio path."
        )
    if not relative.parts or relative.parts[0] not in _ALLOWED_PACK_ASSET_ROOTS:
        raise ProductionPromptRouteError(
            f"{label} must remain inside clone_voices or production_prompt_routes."
        )
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ProductionPromptRouteError(
            f"{label} escaped the project root."
        ) from exc
    if not resolved.is_file():
        raise ProductionPromptRouteError(f"{label} is missing: {resolved}")
    return resolved, relative.as_posix()


def _validate_portable_recurring_voice(
    *,
    root: Path,
    voice_name: str,
    voice: Mapping[str, Any],
) -> None:
    if voice.get("alias_of") or voice.get("alias"):
        raise ProductionPromptRouteError(
            f"Recurring Voice {voice_name!r} must be an authoritative assignment, not an alias."
        )
    voice_type = str(voice.get("type") or "custom").strip().casefold()
    if voice_type in {"custom", "builtin", "built_in", "standard", "saved_voice"}:
        if not str(voice.get("voice") or "").strip():
            raise ProductionPromptRouteError(
                f"Recurring Voice {voice_name!r} has no built-in Voice selection."
            )
        return
    if voice_type in {"design", "designed", "designed_voice", "voice_design"}:
        if not str(voice.get("description") or "").strip():
            raise ProductionPromptRouteError(
                f"Recurring Voice {voice_name!r} has no designed Voice description."
            )
        return
    if voice_type == "community_qvoice":
        pack, _ = _resolve_pack_asset(
            root=root,
            relative_path=voice.get("community_pack_path"),
            label=f"{voice_name} community Voice pack",
        )
        expected = str(voice.get("community_pack_sha256") or "")
        if not expected or sha256_file(pack) != expected:
            raise ProductionPromptRouteError(
                f"Recurring Voice {voice_name!r} community pack failed verification."
            )
        if not str(voice.get("community_pack_approval_fingerprint") or "").strip():
            raise ProductionPromptRouteError(
                f"Recurring Voice {voice_name!r} community pack has no listening approval."
            )
        if not str(
            voice.get("description") or voice.get("character_style") or ""
        ).strip():
            raise ProductionPromptRouteError(
                f"Recurring Voice {voice_name!r} community pack has no persistent description."
            )
        return
    if voice_type != "clone":
        raise ProductionPromptRouteError(
            f"Recurring Voice {voice_name!r} uses unsupported method {voice_type!r}."
        )
    backend = str(voice.get("clone_backend") or STANDARD_CLONE_ENGINE_ID).strip()
    if backend == ROUTED_CLONE_BACKEND:
        policy = validate_recurring_voice_routing(
            voice.get("responsive_backend_routing"),
            project_root=root,
            verify_audio=True,
        )
        recorded = str(
            voice.get("responsive_backend_configuration_fingerprint") or ""
        )
        if recorded != recurring_routing_fingerprint(policy):
            raise ProductionPromptRouteError(
                f"Recurring Voice {voice_name!r} responsive routing approval is stale."
            )
        return
    _resolve_pack_asset(
        root=root,
        relative_path=voice.get("ref_audio"),
        label=f"{voice_name} identity audio",
    )
    if not str(voice.get("ref_text") or "").strip():
        raise ProductionPromptRouteError(
            f"Recurring Voice {voice_name!r} has no exact reference transcript."
        )
    if backend == STANDARD_CLONE_ENGINE_ID:
        return
    if backend == INSTRUCTION_CONTROLLED_ENGINE_ID:
        recorded = str(
            voice.get("controlled_clone_configuration_fingerprint") or ""
        )
        actual = _controlled_clone_fingerprint(root=root, voice=dict(voice))
        if not recorded or recorded != actual:
            raise ProductionPromptRouteError(
                f"Recurring Voice {voice_name!r} controlled-clone approval is stale."
            )
        return
    raise ProductionPromptRouteError(
        f"Recurring Voice {voice_name!r} uses unsupported clone backend {backend!r}."
    )


def _responsive_pack_assets(
    *,
    root: Path,
    voice_config: dict[str, Any],
    voice_names: tuple[str, ...] = RECURRING_VOICES,
) -> list[dict[str, Any]]:
    assets: dict[str, dict[str, Any]] = {}

    def add_asset(
        *,
        voice_name: str,
        relative_path: Any,
        kind: str,
        label: str,
        expected_sha256: str | None = None,
        route: str | None = None,
    ) -> None:
        resolved, normalized = _resolve_pack_asset(
            root=root,
            relative_path=relative_path,
            label=label,
        )
        actual = sha256_file(resolved)
        if expected_sha256 is not None and actual != expected_sha256:
            raise ProductionPromptRouteError(f"{label} changed.")
        existing = assets.get(normalized)
        if existing is not None and existing["sha256"] != actual:
            raise ProductionPromptRouteError(
                f"Recurring Voice asset {normalized!r} has conflicting fingerprints."
            )
        record = {
            "relative_path": normalized,
            "sha256": actual,
            "kind": kind,
            "voice": voice_name,
        }
        if route is not None:
            record["route"] = route
        assets[normalized] = record

    for voice_name in voice_names:
        voice = voice_config.get(voice_name)
        if not isinstance(voice, dict):
            raise ProductionPromptRouteError(
                f"The required recurring Voice {voice_name!r} is missing."
            )
        _validate_portable_recurring_voice(
            root=root,
            voice_name=voice_name,
            voice=voice,
        )
        voice_type = str(voice.get("type") or "custom").strip().casefold()
        if voice_type == "clone":
            clone_backend = str(
                voice.get("clone_backend") or STANDARD_CLONE_ENGINE_ID
            ).strip()
            if clone_backend != ROUTED_CLONE_BACKEND:
                add_asset(
                    voice_name=voice_name,
                    relative_path=voice.get("ref_audio"),
                    kind="identity",
                    label=f"{voice_name} identity audio",
                )
            policy = voice.get("experimental_prompt_routing")
            routes = policy.get("routes") if isinstance(policy, dict) else None
            if isinstance(routes, dict):
                for route_key, route_value in routes.items():
                    if not isinstance(route_value, dict):
                        continue
                    add_asset(
                        voice_name=voice_name,
                        relative_path=route_value.get("ref_audio"),
                        kind="performance_prompt",
                        label=f"{voice_name} route {route_key}",
                        expected_sha256=str(
                            route_value.get("ref_audio_sha256") or ""
                        ),
                        route=str(route_key),
                    )
            responsive = voice.get("responsive_backend_routing")
            responsive_routes = (
                responsive.get("routes")
                if isinstance(responsive, dict)
                else None
            )
            if isinstance(responsive_routes, dict):
                for route_key, route_value in responsive_routes.items():
                    if not isinstance(route_value, dict):
                        continue
                    add_asset(
                        voice_name=voice_name,
                        relative_path=route_value.get("identity_audio"),
                        kind="responsive_identity",
                        label=f"{voice_name} responsive route {route_key} identity",
                        expected_sha256=str(
                            route_value.get("identity_audio_sha256") or ""
                        ),
                        route=str(route_key),
                    )
                    if route_value.get("performance_audio"):
                        add_asset(
                            voice_name=voice_name,
                            relative_path=route_value.get("performance_audio"),
                            kind="responsive_performance_prompt",
                            label=(
                                f"{voice_name} responsive route {route_key} performance"
                            ),
                            expected_sha256=str(
                                route_value.get("performance_audio_sha256") or ""
                            ),
                            route=str(route_key),
                        )
        elif voice_type == "community_qvoice":
            add_asset(
                voice_name=voice_name,
                relative_path=voice.get("community_pack_path"),
                kind="community_voice_pack",
                label=f"{voice_name} community Voice pack",
                expected_sha256=str(voice.get("community_pack_sha256") or ""),
            )
    return [assets[key] for key in sorted(assets)]


def inspect_primary_responsive_voice_pack(
    project_root: str | Path,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    try:
        config = _read_json_object(
            root / "voice_config.json",
            "Voice configuration",
        )
        portable_config: dict[str, Any] = {}
        for voice_name in RECURRING_VOICES:
            voice = config.get(voice_name)
            if not isinstance(voice, dict):
                raise ProductionPromptRouteError(
                    f"The required recurring Voice {voice_name!r} is missing."
                )
            _validate_portable_recurring_voice(
                root=root,
                voice_name=voice_name,
                voice=voice,
            )
            portable_config[voice_name] = copy.deepcopy(voice)
        for alias, target in RECURRING_VOICE_ALIASES.items():
            portable_config[alias] = {"alias_of": target}
        validate_voice_aliases(portable_config)
        assets = _responsive_pack_assets(root=root, voice_config=config)
        pack_fingerprint = fingerprint_value(
            {
                "pack_id": PACK_ID,
                "voices": portable_config,
                "assets": assets,
                "production_generation_seed": PRODUCTION_GENERATION_SEED,
            }
        )
        return {
            "ready": True,
            "pack_id": PACK_ID,
            "pack_fingerprint": pack_fingerprint,
            "voices": list(RECURRING_VOICES),
            "aliases": copy.deepcopy(RECURRING_VOICE_ALIASES),
            "assets": assets,
            "production_generation_seed": PRODUCTION_GENERATION_SEED,
            "error": None,
        }
    except Exception as exc:
        return {
            "ready": False,
            "pack_id": PACK_ID,
            "pack_fingerprint": None,
            "voices": list(RECURRING_VOICES),
            "aliases": copy.deepcopy(RECURRING_VOICE_ALIASES),
            "assets": [],
            "production_generation_seed": PRODUCTION_GENERATION_SEED,
            "error": str(exc),
        }


def materialize_primary_responsive_voice_pack(
    *,
    source_project_root: str | Path,
    destination_project_root: str | Path,
) -> dict[str, Any]:
    source_root = Path(source_project_root).expanduser().resolve()
    destination_root = Path(destination_project_root).expanduser().resolve()
    inspection = inspect_primary_responsive_voice_pack(source_root)
    if inspection.get("ready") is not True:
        raise ProductionPromptRouteError(
            "The primary responsive voice pack is unavailable: "
            + str(inspection.get("error") or "unknown validation failure")
        )
    destination_root.mkdir(parents=True, exist_ok=True)
    source_config = _read_json_object(
        source_root / "voice_config.json",
        "Source voice configuration",
    )
    destination_config_path = destination_root / "voice_config.json"
    destination_config = (
        _read_json_object(destination_config_path, "Destination voice configuration")
        if destination_config_path.is_file()
        else {}
    )
    for asset in inspection["assets"]:
        relative = str(asset["relative_path"])
        source_asset, _ = _resolve_pack_asset(
            root=source_root,
            relative_path=relative,
            label=f"Responsive voice pack asset {relative}",
        )
        destination_asset = destination_root / relative
        _atomic_copy(source_asset, destination_asset)
        if sha256_file(destination_asset) != asset["sha256"]:
            raise ProductionPromptRouteError(
                f"Copied responsive voice asset failed verification: {relative}."
            )
    for voice_name in RECURRING_VOICES:
        destination_config[voice_name] = copy.deepcopy(
            source_config[voice_name]
        )
    for alias, target in RECURRING_VOICE_ALIASES.items():
        destination_config[alias] = {"alias_of": target}
    validate_voice_aliases(destination_config)
    atomic_json_write(destination_config, destination_config_path)
    for voice_name in RECURRING_VOICES:
        voice = destination_config.get(voice_name)
        if not isinstance(voice, dict):
            raise ProductionPromptRouteError(
                f"Copied recurring Voice {voice_name!r} is missing."
            )
        _validate_portable_recurring_voice(
            root=destination_root,
            voice_name=voice_name,
            voice=voice,
        )
    receipt = {
        "schema_version": 1,
        "pack_id": PACK_ID,
        "pack_fingerprint": inspection["pack_fingerprint"],
        "voices": list(RECURRING_VOICES),
        "aliases": copy.deepcopy(RECURRING_VOICE_ALIASES),
        "assets": copy.deepcopy(inspection["assets"]),
        "production_generation_seed": PRODUCTION_GENERATION_SEED,
        "automatic_instruction_matching": True,
        "final_export_eligible": True,
    }
    atomic_json_write(
        receipt,
        destination_root / PACK_RECEIPT_FILENAME,
    )
    return receipt


def stage_verified_responsive_voice_assets(
    *,
    source_project_root: str | Path,
    destination_root: str | Path,
    voice_name: str,
) -> dict[str, Any]:
    source_root = Path(source_project_root).expanduser().resolve()
    destination_root = Path(destination_root).expanduser().resolve()
    voice_config = _read_json_object(
        source_root / "voice_config.json",
        "Voice configuration",
    )
    assets = _responsive_pack_assets(
        root=source_root,
        voice_config=voice_config,
        voice_names=(voice_name,),
    )
    destination_root.mkdir(parents=True, exist_ok=True)
    for asset in assets:
        relative = str(asset["relative_path"])
        _copy_verified_asset_confined(
            source_root=source_root,
            destination_root=destination_root,
            relative_path=relative,
            expected_sha256=str(asset["sha256"]),
        )
    return {"voice": voice_name, "assets": assets}


def _open_confined_parent(
    root: Path,
    relative: Path,
    *,
    create: bool,
    label: str,
) -> int:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(root, directory_flags)
    try:
        for part in relative.parts[:-1]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, directory_flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError as exc:
        os.close(descriptor)
        raise ProductionPromptRouteError(
            f"{label} contains a symlink or unsafe directory: {relative.as_posix()}."
        ) from exc


def _copy_verified_asset_confined(
    *,
    source_root: Path,
    destination_root: Path,
    relative_path: str,
    expected_sha256: str,
) -> None:
    relative = Path(relative_path)
    source_parent = _open_confined_parent(
        source_root,
        relative,
        create=False,
        label="Responsive voice source path",
    )
    try:
        destination_parent = _open_confined_parent(
            destination_root,
            relative,
            create=True,
            label="Responsive voice staging sandbox",
        )
    except Exception:
        os.close(source_parent)
        raise
    temporary_name = f".{relative.name}.{secrets.token_hex(8)}.tmp"
    try:
        try:
            source_descriptor = os.open(
                relative.name,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=source_parent,
            )
        except OSError as exc:
            raise ProductionPromptRouteError(
                f"Responsive voice source path contains a symlink or unsafe file: "
                f"{relative.as_posix()}."
            ) from exc
        try:
            destination_descriptor = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=destination_parent,
            )
        except OSError:
            os.close(source_descriptor)
            raise
        digest = hashlib.sha256()
        with os.fdopen(source_descriptor, "rb") as input_handle, os.fdopen(
            destination_descriptor,
            "wb",
        ) as output_handle:
            for block in iter(lambda: input_handle.read(1024 * 1024), b""):
                digest.update(block)
                output_handle.write(block)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        if digest.hexdigest() != expected_sha256:
            raise ProductionPromptRouteError(
                f"Responsive voice source hash changed during staging: "
                f"{relative.as_posix()}."
            )
        os.replace(
            temporary_name,
            relative.name,
            src_dir_fd=destination_parent,
            dst_dir_fd=destination_parent,
        )
    finally:
        try:
            os.unlink(temporary_name, dir_fd=destination_parent)
        except FileNotFoundError:
            pass
        os.close(source_parent)
        os.close(destination_parent)


def _reviewed_reference_text(reference: Mapping[str, Any], field: str) -> str:
    value = reference.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ProductionPromptRouteError(
            f"Reviewed expressive reference {field} must contain text."
        )
    return value.strip()


def _derived_route_keywords(reference: Mapping[str, Any]) -> list[str]:
    clip_id = _reviewed_reference_text(reference, "clip_id")
    configured = EXPRESSIVE_ROUTE_KEYWORDS.get(clip_id)
    if configured is not None:
        return list(configured)
    phrases: list[str] = []
    for field in ("primary_emotion", "secondary_emotion", "dramatic_function"):
        value = reference.get(field)
        if not isinstance(value, str) or not value.strip():
            continue
        phrase = " ".join(value.casefold().split())
        if phrase not in phrases:
            phrases.append(phrase)
    if not phrases:
        raise ProductionPromptRouteError(
            f"Reviewed expressive reference {clip_id!r} has no routing vocabulary."
        )
    return phrases[:32]


def _validate_reviewed_reference_audio(source: Path, clip_id: str) -> None:
    try:
        audio, sample_rate = sf.read(source, dtype="float32", always_2d=True)
    except Exception as exc:
        raise ProductionPromptRouteError(
            f"Reviewed expressive reference {clip_id!r} is not readable: {exc}"
        ) from exc
    waveform = np.mean(audio, axis=1, dtype=np.float32)
    if waveform.size == 0 or int(sample_rate) <= 0:
        raise ProductionPromptRouteError(
            f"Reviewed expressive reference {clip_id!r} is empty."
        )
    rms = float(np.sqrt(np.mean(np.square(waveform, dtype=np.float64))))
    if rms < 10.0 ** (-36.0 / 20.0):
        raise ProductionPromptRouteError(
            f"Reviewed expressive reference {clip_id!r} is too quiet for "
            "production cloning."
        )
    duration = waveform.size / int(sample_rate)
    if not 1.0 <= duration <= 30.0:
        raise ProductionPromptRouteError(
            f"Reviewed expressive reference {clip_id!r} has an unsafe duration "
            f"({duration:.2f}s)."
        )


def _prepare_reviewed_expressive_references(
    *,
    validated_bank_path: Path,
    target_voices: Mapping[str, str],
) -> list[dict[str, Any]]:
    bank = _read_json_object(validated_bank_path, "Validated expressive bank")
    schema_version = bank.get("schema_version")
    if (
        type(schema_version) is not int
        or schema_version not in SUPPORTED_EXPRESSIVE_BANK_SCHEMAS
    ):
        raise ProductionPromptRouteError(
            "Validated expressive bank schema_version is missing or unsupported."
        )
    raw_references = bank.get("references")
    if not isinstance(raw_references, list):
        raise ProductionPromptRouteError(
            "Validated expressive bank references must be a list."
        )
    prepared: list[dict[str, Any]] = []
    seen: set[str] = set()
    covered_targets: set[str] = set()
    for raw_reference in raw_references:
        if not isinstance(raw_reference, dict):
            raise ProductionPromptRouteError(
                "Every validated expressive bank reference must be an object."
            )
        target = _reviewed_reference_text(raw_reference, "target").casefold()
        if target not in target_voices:
            continue
        clip_id = _reviewed_reference_text(raw_reference, "clip_id")
        if clip_id in seen:
            raise ProductionPromptRouteError(
                f"Validated expressive bank repeats clip {clip_id!r}."
            )
        seen.add(clip_id)
        status = _reviewed_reference_text(
            raw_reference,
            "reference_status",
        ).casefold()
        if status not in APPROVED_EXPRESSIVE_REFERENCE_STATUSES:
            raise ProductionPromptRouteError(
                f"Reviewed expressive reference {clip_id!r} is not approved."
            )
        recorded_sha = _reviewed_reference_text(raw_reference, "audio_sha256")
        raw_audio_path = Path(
            _reviewed_reference_text(raw_reference, "audio_path")
        ).expanduser()
        source = (
            raw_audio_path.resolve()
            if raw_audio_path.is_absolute()
            else (validated_bank_path.parent / raw_audio_path).resolve()
        )
        source = _validate_source(
            source,
            recorded_sha,
            f"Reviewed expressive reference {clip_id}",
        )
        manifest_ref_text = _reviewed_reference_text(
            raw_reference,
            "selected_transcript",
        )
        repair = EXPRESSIVE_REFERENCE_REPAIRS.get((target, clip_id))
        repair_of: dict[str, str] | None = None
        if repair is not None:
            manifest_matches = (
                recorded_sha == repair["manifest_audio_sha256"]
                and manifest_ref_text == repair["manifest_ref_text"]
            )
            already_repaired = (
                recorded_sha == repair["audio_sha256"]
                and manifest_ref_text == repair["ref_text"]
            )
            if manifest_matches:
                repair_of = {
                    "audio_sha256": recorded_sha,
                    "ref_text": manifest_ref_text,
                }
                source = _validate_source(
                    repair["source"],
                    repair["audio_sha256"],
                    f"Curated expressive repair {clip_id}",
                )
                recorded_sha = repair["audio_sha256"]
                manifest_ref_text = repair["ref_text"]
            elif already_repaired:
                recorded_sha = repair["audio_sha256"]
                manifest_ref_text = repair["ref_text"]
        if schema_version >= 3:
            _validate_reviewed_reference_audio(source, clip_id)
        relative_audio = (
            Path("production_prompt_routes")
            / "expressive"
            / target
            / f"{clip_id}{source.suffix.casefold() or '.wav'}"
        ).as_posix()
        prepared.append(
            {
                "target": target,
                "voice_name": target_voices[target],
                "clip_id": clip_id,
                "source": source,
                "relative_audio": relative_audio,
                "audio_sha256": recorded_sha,
                "ref_text": manifest_ref_text,
                "instruction_keywords": _derived_route_keywords(raw_reference),
                "repair_of": repair_of,
            }
        )
        covered_targets.add(target)
    missing = sorted(set(target_voices) - covered_targets)
    if missing:
        raise ProductionPromptRouteError(
            "Validated expressive bank has no approved reference for: "
            + ", ".join(missing)
            + "."
        )
    return prepared


def _validate_existing_reference_route(
    *,
    voice_name: str,
    route_key: str,
    route: Mapping[str, Any],
    reference: Mapping[str, Any],
) -> bool:
    direct_evidence = {
        "status": "production_opt_in",
        "prompt_role": "validated_bank",
        "reference_key": reference["clip_id"],
        "validated_bank_clip_id": reference["clip_id"],
        "ref_audio": reference["relative_audio"],
        "ref_audio_sha256": reference["audio_sha256"],
        "ref_text": reference["ref_text"],
        "production_promotion_allowed": True,
        "instruction_keywords": list(reference["instruction_keywords"]),
        "approval_basis": "operator_approved_after_listening",
    }
    if route_key == reference["clip_id"] and all(
        route.get(field) == value
        for field, value in direct_evidence.items()
    ):
        return False

    substitution = EXPRESSIVE_ROUTE_SUBSTITUTIONS.get(
        (voice_name, reference["clip_id"])
    )
    if substitution is None:
        raise ProductionPromptRouteError(
            f"Expressive clip {reference['clip_id']!r} already exists with "
            "different evidence and has no validated substitution."
        )
    manifest_matches = (
        reference["audio_sha256"] == substitution["manifest_audio_sha256"]
        and reference["ref_text"] == substitution["manifest_ref_text"]
    )
    expected_route = substitution["route_evidence"]
    route_matches = route_key == substitution["route_key"] and all(
        route.get(field) == value
        for field, value in expected_route.items()
    )
    if not manifest_matches or not route_matches:
        raise ProductionPromptRouteError(
            f"Expressive clip {reference['clip_id']!r} has different evidence "
            "from its validated substitution."
        )
    return True


def _build_promoted_policies(
    *,
    root: Path,
    config: Mapping[str, Any],
    prepared: list[dict[str, Any]],
    approved_at_utc: str,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    policies: dict[str, dict[str, Any]] = {}
    substitutions: list[dict[str, str]] = []
    for reference in prepared:
        voice_name = reference["voice_name"]
        if voice_name not in policies:
            voice = config.get(voice_name)
            if not isinstance(voice, dict):
                raise ProductionPromptRouteError(
                    f"The opted-in expressive voice {voice_name!r} is missing."
                )
            existing = voice.get("experimental_prompt_routing")
            if existing is None:
                routes: dict[str, Any] = {}
            else:
                routes = copy.deepcopy(
                    validate_experimental_prompt_routing(
                        existing,
                        project_root=root,
                        verify_audio=True,
                    )["routes"]
                )
            policies[voice_name] = {
                "schema_version": PROMPT_ROUTING_SCHEMA_VERSION,
                "enabled": True,
                "scope": "production_opt_in",
                "general_routing": "instruction_keywords",
                "production_promotion_allowed": True,
                "evidence_round_id": EXPRESSIVE_PROMOTION_EVIDENCE_ROUND_ID,
                "routes": routes,
            }
        policy = policies[voice_name]
        matching = [
            (route_key, route)
            for route_key, route in policy["routes"].items()
            if isinstance(route, dict)
            and route.get("validated_bank_clip_id") == reference["clip_id"]
        ]
        if len(matching) > 1:
            raise ProductionPromptRouteError(
                f"Expressive clip {reference['clip_id']!r} has multiple existing routes."
            )
        if matching:
            route_key, route = matching[0]
            repair_of = reference.get("repair_of")
            if (
                isinstance(repair_of, dict)
                and route_key == reference["clip_id"]
                and route.get("ref_audio") == reference["relative_audio"]
                and route.get("ref_audio_sha256") == repair_of["audio_sha256"]
                and route.get("ref_text") == repair_of["ref_text"]
            ):
                policy["routes"][route_key] = {
                    "status": "production_opt_in",
                    "prompt_role": "validated_bank",
                    "reference_key": reference["clip_id"],
                    "validated_bank_clip_id": reference["clip_id"],
                    "ref_audio": reference["relative_audio"],
                    "ref_audio_sha256": reference["audio_sha256"],
                    "ref_text": reference["ref_text"],
                    "production_promotion_allowed": True,
                    "instruction_keywords": list(reference["instruction_keywords"]),
                    "approval_basis": "operator_approved_after_listening",
                    "operator_approved_at_utc": approved_at_utc,
                }
                continue
            if _validate_existing_reference_route(
                voice_name=voice_name,
                route_key=route_key,
                route=route,
                reference=reference,
            ):
                substitutions.append(
                    {
                        "voice": voice_name,
                        "clip_id": reference["clip_id"],
                        "route_key": route_key,
                    }
                )
            continue
        route_key = reference["clip_id"]
        if route_key in policy["routes"]:
            raise ProductionPromptRouteError(
                f"Expressive route {route_key!r} already exists with different evidence."
            )
        policy["routes"][route_key] = {
            "status": "production_opt_in",
            "prompt_role": "validated_bank",
            "reference_key": reference["clip_id"],
            "validated_bank_clip_id": reference["clip_id"],
            "ref_audio": reference["relative_audio"],
            "ref_audio_sha256": reference["audio_sha256"],
            "ref_text": reference["ref_text"],
            "production_promotion_allowed": True,
            "instruction_keywords": list(reference["instruction_keywords"]),
            "approval_basis": "operator_approved_after_listening",
            "operator_approved_at_utc": approved_at_utc,
        }
    for voice_name, policy in policies.items():
        policies[voice_name] = validate_experimental_prompt_routing(
            policy,
            project_root=root,
            verify_audio=False,
        )
    return policies, substitutions


def promote_validated_expressive_routes(
    *,
    project_root: str | Path,
    validated_bank_path: str | Path,
    confirm_production_opt_in: bool,
    approved_at_utc: str | None = None,
    target_voices: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if confirm_production_opt_in is not True:
        raise ProductionPromptRouteError(
            "Expressive bank promotion requires explicit production confirmation."
        )
    root = Path(project_root).expanduser().resolve()
    bank_path = Path(validated_bank_path).expanduser().resolve()
    voice_config_path = root / "voice_config.json"
    config = _read_json_object(voice_config_path, "Voice configuration")
    selected_targets = dict(
        target_voices
        if target_voices is not None
        else DEFAULT_EXPRESSIVE_TARGET_VOICES
    )
    if not selected_targets or any(
        not isinstance(target, str)
        or not target.strip()
        or not isinstance(voice, str)
        or not voice.strip()
        for target, voice in selected_targets.items()
    ):
        raise ProductionPromptRouteError(
            "Expressive target voices must explicitly map bank targets to voice names."
        )
    selected_targets = {
        target.strip().casefold(): voice.strip()
        for target, voice in selected_targets.items()
    }
    prepared = _prepare_reviewed_expressive_references(
        validated_bank_path=bank_path,
        target_voices=selected_targets,
    )
    approved_at = approved_at_utc or utc_timestamp()
    policies, substitutions = _build_promoted_policies(
        root=root,
        config=config,
        prepared=prepared,
        approved_at_utc=approved_at,
    )
    destinations = {
        root / reference["relative_audio"]: reference
        for reference in prepared
        if not any(
            route.get("validated_bank_clip_id") == reference["clip_id"]
            for route in policies[reference["voice_name"]]["routes"].values()
            if isinstance(route, dict)
            and route.get("ref_audio") != reference["relative_audio"]
        )
    }
    before_config = voice_config_path.read_bytes()
    before_assets = {
        destination: destination.read_bytes() if destination.is_file() else None
        for destination in destinations
    }
    updated_config = copy.deepcopy(config)
    try:
        for destination, reference in destinations.items():
            _atomic_copy(reference["source"], destination)
            if sha256_file(destination) != reference["audio_sha256"]:
                raise ProductionPromptRouteError(
                    f"Promoted expressive route failed verification: {destination}."
                )
        for voice_name, policy in policies.items():
            source_voice = updated_config.get(voice_name)
            if not isinstance(source_voice, dict):
                raise ProductionPromptRouteError(
                    f"The opted-in expressive voice {voice_name!r} is missing."
                )
            updated_config[voice_name] = _upgrade_voice(
                root=root,
                voice_name=voice_name,
                source=source_voice,
                policy=policy,
            )
        expanded_aliases: list[str] = []
        for alias, target in PRIMARY_VOICE_ALIASES.items():
            alias_voice = updated_config.get(alias)
            if (
                target not in policies
                or not isinstance(alias_voice, dict)
                or alias_voice.get("alias_of")
                or alias_voice.get("alias")
            ):
                continue
            if alias_voice.get("type") != "clone":
                continue
            updated_config[alias] = _upgrade_voice(
                root=root,
                voice_name=alias,
                source=alias_voice,
                policy=policies[target],
            )
            expanded_aliases.append(alias)
        validate_voice_aliases(updated_config)
        atomic_json_write(updated_config, voice_config_path)
        operation_id = "audio_dependency_" + fingerprint_value(
            {
                "operation": "validated_expressive_route_promotion",
                "approved_at_utc": approved_at,
                "bank_path": str(bank_path),
                "voices": sorted(policies),
                "routes": sorted(
                    reference["clip_id"] for reference in prepared
                ),
            }
        )[:24]
        invalidated_speakers = set(policies)
        invalidated_speakers.update(
            alias
            for alias, target in PRIMARY_VOICE_ALIASES.items()
            if target in policies
        )
        invalidation = apply_project_audio_invalidation(
            project_root=root,
            operation_id=operation_id,
            operation="validated_expressive_route_promotion",
            at_utc=approved_at,
            speakers=invalidated_speakers,
            reason=(
                "primary voice performance prompts changed to operator-promoted "
                "human-validated expressive references"
            ),
            dependency_before={
                voice_config_path: before_config,
                **before_assets,
            },
        )
    except Exception:
        _atomic_restore(voice_config_path, before_config)
        for destination, before in before_assets.items():
            _atomic_restore(destination, before)
        raise
    return {
        "status": "promoted",
        "evidence_round_id": EXPRESSIVE_PROMOTION_EVIDENCE_ROUND_ID,
        "promoted_voice_count": len(policies),
        "voices": sorted(policies),
        "expanded_aliases": sorted(expanded_aliases),
        "validated_reference_count": len(prepared),
        "promoted_reference_count": len(prepared) - len(substitutions),
        "validated_substitution_count": len(substitutions),
        "validated_substitutions": substitutions,
        "automatic_instruction_matching": True,
        "final_export_eligible": True,
        "audio_invalidation": invalidation,
    }


def install_primary_responsive_voices(
    *,
    project_root: str | Path,
    benny_prompt_source: str | Path,
    doctor_prompt_source: str | Path,
    confirm_production_opt_in: bool,
    approved_at_utc: str | None = None,
) -> dict[str, Any]:
    if confirm_production_opt_in is not True:
        raise ProductionPromptRouteError(
            "Production-responsive voice installation requires explicit confirmation."
        )
    root = Path(project_root).expanduser().resolve()
    voice_config_path = root / "voice_config.json"
    if not voice_config_path.is_file():
        raise ProductionPromptRouteError(
            f"Voice configuration is missing: {voice_config_path}"
        )
    benny_source = _validate_source(
        benny_prompt_source,
        BENNY_SOURCE_SHA256,
        "Benny fear prompt",
    )
    doctor_source = _validate_source(
        doctor_prompt_source,
        DOCTOR_SOURCE_SHA256,
        "Doctor playful prompt",
    )
    approved_at = approved_at_utc or utc_timestamp()
    before_config = voice_config_path.read_bytes()
    benny_destination = root / "production_prompt_routes" / "benny_credible_fear.wav"
    doctor_destination = root / "production_prompt_routes" / "doctor_playful_identity.wav"
    before_benny = benny_destination.read_bytes() if benny_destination.exists() else None
    before_doctor = doctor_destination.read_bytes() if doctor_destination.exists() else None

    _atomic_copy(benny_source, benny_destination)
    _atomic_copy(doctor_source, doctor_destination)
    try:
        config = build_primary_responsive_voice_config(
            project_root=root,
            voice_config=_read_json_object(
                voice_config_path,
                "Voice configuration",
            ),
            approved_at_utc=approved_at,
        )
        atomic_json_write(config, voice_config_path)
        operation_id = "audio_dependency_" + fingerprint_value(
            {
                "operation": "primary_responsive_voice_install",
                "pack_id": PACK_ID,
                "approved_at_utc": approved_at,
                "voices": list(PRIMARY_VOICES),
            }
        )[:24]
        invalidation = apply_project_audio_invalidation(
            project_root=root,
            operation_id=operation_id,
            operation="primary_responsive_voice_install",
            at_utc=approved_at,
            speakers=set(PRIMARY_VOICES),
            reason=(
                "primary voices changed to deterministic instruction-controlled "
                "delivery with production-approved route prompts"
            ),
            dependency_before={
                voice_config_path: before_config,
                benny_destination: before_benny,
                doctor_destination: before_doctor,
            },
        )
    except Exception:
        voice_config_path.write_bytes(before_config)
        if before_benny is None:
            try:
                benny_destination.unlink()
            except FileNotFoundError:
                pass
        else:
            benny_destination.write_bytes(before_benny)
        if before_doctor is None:
            try:
                doctor_destination.unlink()
            except FileNotFoundError:
                pass
        else:
            doctor_destination.write_bytes(before_doctor)
        raise

    return {
        "status": "installed",
        "pack_id": PACK_ID,
        "voices": list(PRIMARY_VOICES),
        "production_routes": {
            "BERNICE": "credible_fear",
            "THE DOCTOR": "ordinary_identity",
        },
        "automatic_instruction_matching": True,
        "final_export_eligible": True,
        "deterministic_seed_required": True,
        "production_generation_seed": PRODUCTION_GENERATION_SEED,
        "audio_invalidation": invalidation,
    }
