from __future__ import annotations

import copy
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from audio_invalidation import apply_project_audio_invalidation
from controlled_clone_preview import build_controlled_clone_configuration_fingerprint
from experimental_prompt_routing import (
    PROMPT_ROUTING_SCHEMA_VERSION,
    prompt_routing_fingerprint,
    sha256_file,
    validate_experimental_prompt_routing,
)
from generation_state import atomic_json_write, fingerprint_value
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
PRIMARY_VOICE_ALIASES = {
    "DOCTOR": "THE DOCTOR",
    "SEVENTH DOCTOR": "THE DOCTOR",
    "THE SEVENTH DOCTOR": "THE DOCTOR",
    "BENNY": "BERNICE",
    "BERNICE SUMMERFIELD": "BERNICE",
    "NARRATOR (BENNY)": "BERNICE",
}
PACK_RECEIPT_FILENAME = "primary_responsive_voice_pack.json"
_ALLOWED_PACK_ASSET_ROOTS = frozenset(
    {"clone_voices", "production_prompt_routes"}
)
PRODUCTION_GENERATION_SEED = 130363


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
            "clone_backend": "qwen3_instruction_controlled",
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


def _responsive_pack_assets(
    *,
    root: Path,
    voice_config: dict[str, Any],
) -> list[dict[str, Any]]:
    assets: dict[str, dict[str, Any]] = {}
    for voice_name in PRIMARY_VOICES:
        voice = voice_config.get(voice_name)
        if not isinstance(voice, dict):
            raise ProductionPromptRouteError(
                f"The required primary voice {voice_name!r} is missing."
            )
        identity, identity_relative = _resolve_pack_asset(
            root=root,
            relative_path=voice.get("ref_audio"),
            label=f"{voice_name} identity audio",
        )
        assets[identity_relative] = {
            "relative_path": identity_relative,
            "sha256": sha256_file(identity),
            "kind": "identity",
            "voice": voice_name,
        }
        policy = voice.get("experimental_prompt_routing")
        if not isinstance(policy, dict):
            continue
        routes = policy.get("routes")
        if not isinstance(routes, dict):
            continue
        for route_key, route in routes.items():
            if not isinstance(route, dict):
                continue
            prompt, prompt_relative = _resolve_pack_asset(
                root=root,
                relative_path=route.get("ref_audio"),
                label=f"{voice_name} route {route_key}",
            )
            actual_hash = sha256_file(prompt)
            expected_hash = str(route.get("ref_audio_sha256") or "")
            if actual_hash != expected_hash:
                raise ProductionPromptRouteError(
                    f"{voice_name} route {route_key} audio changed."
                )
            assets[prompt_relative] = {
                "relative_path": prompt_relative,
                "sha256": actual_hash,
                "kind": "performance_prompt",
                "voice": voice_name,
                "route": str(route_key),
            }
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
        for voice_name in PRIMARY_VOICES:
            voice = config.get(voice_name)
            if not isinstance(voice, dict):
                raise ProductionPromptRouteError(
                    f"The required primary voice {voice_name!r} is missing."
                )
            if voice.get("type") != "clone":
                raise ProductionPromptRouteError(
                    f"{voice_name} is not a supplied-recording clone."
                )
            if voice.get("clone_backend") != "qwen3_instruction_controlled":
                raise ProductionPromptRouteError(
                    f"{voice_name} is not instruction-controlled."
                )
            try:
                configured_seed = int(voice.get("seed", -1))
            except (TypeError, ValueError) as exc:
                raise ProductionPromptRouteError(
                    f"{voice_name} has an invalid deterministic seed."
                ) from exc
            if configured_seed != PRODUCTION_GENERATION_SEED:
                raise ProductionPromptRouteError(
                    f"{voice_name} is not using the approved production seed."
                )
            recorded = str(
                voice.get("controlled_clone_configuration_fingerprint") or ""
            )
            actual = _controlled_clone_fingerprint(root=root, voice=voice)
            if not recorded or recorded != actual:
                raise ProductionPromptRouteError(
                    f"{voice_name} configuration approval is stale."
                )
            portable_config[voice_name] = copy.deepcopy(voice)
        for alias, target in PRIMARY_VOICE_ALIASES.items():
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
            "voices": list(PRIMARY_VOICES),
            "aliases": copy.deepcopy(PRIMARY_VOICE_ALIASES),
            "assets": assets,
            "production_generation_seed": PRODUCTION_GENERATION_SEED,
            "error": None,
        }
    except Exception as exc:
        return {
            "ready": False,
            "pack_id": PACK_ID,
            "pack_fingerprint": None,
            "voices": list(PRIMARY_VOICES),
            "aliases": copy.deepcopy(PRIMARY_VOICE_ALIASES),
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
    for voice_name in PRIMARY_VOICES:
        destination_config[voice_name] = copy.deepcopy(
            source_config[voice_name]
        )
    for alias, target in PRIMARY_VOICE_ALIASES.items():
        destination_config[alias] = {"alias_of": target}
    validate_voice_aliases(destination_config)
    atomic_json_write(destination_config, destination_config_path)
    for voice_name in PRIMARY_VOICES:
        voice = destination_config[voice_name]
        recorded = str(
            voice.get("controlled_clone_configuration_fingerprint") or ""
        )
        actual = _controlled_clone_fingerprint(
            root=destination_root,
            voice=voice,
        )
        if recorded != actual:
            raise ProductionPromptRouteError(
                f"Copied {voice_name} approval fingerprint is invalid."
            )
    receipt = {
        "schema_version": 1,
        "pack_id": PACK_ID,
        "pack_fingerprint": inspection["pack_fingerprint"],
        "voices": list(PRIMARY_VOICES),
        "aliases": copy.deepcopy(PRIMARY_VOICE_ALIASES),
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
