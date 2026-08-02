from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from audio_invalidation import (
    apply_project_audio_invalidation,
    undo_project_audio_invalidation,
)
from experimental_prompt_routing import sha256_file
from generation_state import atomic_json_write, fingerprint_value
from model_registry import INSTRUCTION_CONTROLLED_ENGINE_ID
from recurring_voice_routing import (
    FISH_ROUTE_BACKEND_ID,
    INDEXTTS2_ROUTE_BACKEND_ID,
    VOXCPM2_ROUTE_BACKEND_ID,
    ROUTED_CLONE_BACKEND,
    routing_fingerprint,
    validate_recurring_voice_routing,
)
from voice_aliases import validate_voice_aliases


PACK_ID = "alexandria_original_sin_noncore_quasi_emotive_v2"
EVIDENCE_ROUND_ID = "alexandria_original_sin_noncore_multimodel_round_v2_closed"
PRODUCTION_SEED = 130363
RECEIPT_FILENAME = "noncore_quasi_emotive_voice_pack.json"
ASSET_ROOT = Path("production_prompt_routes/noncore_quasi_emotive_v2")

VOICE_BY_MODE = {
    "beltempest_interrogative_impatience": "BELTEMPEST",
    "beltempest_military_volatility": "BELTEMPEST",
    "beltempest_weary_resignation": "BELTEMPEST",
    "beltempest_urgent_command": "BELTEMPEST",
    "tobias_cultivated_menace": "TOBIAS VAUGHN",
    "tobias_polished_probe": "TOBIAS VAUGHN",
    "zebulon_nervous_analysis": "ZEBULON PRYCE",
    "zebulon_intense_questioning": "ZEBULON PRYCE",
    "hater_wounded_fury": "HATER OF HUMANS",
    "karvellis_amplified_command": "KARVELLIS",
    "lubineki_rough_jovial": "LUBINEKI",
    "powerless_panicked_urgency": "POWERLESS FRIENDLESS",
    "rashid_tired_authority": "RASHID",
    "under_sergeant_military_menace": "UNDER-SERGEANT",
}

ROUTE_KEYWORDS = {
    "beltempest_interrogative_impatience": [
        "interrogative impatience",
        "rising impatience",
        "military questioning",
        "clipped questioning",
    ],
    "beltempest_military_volatility": [
        "military volatility",
        "simmering volatility",
        "rigid authority",
        "under arrest",
    ],
    "beltempest_weary_resignation": [
        "weary resignation",
        "audibly weary",
        "subdued resignation",
        "softened pacing",
    ],
    "beltempest_urgent_command": [
        "urgent command",
        "urgent military",
        "clipped momentum",
        "interrupted urgency",
    ],
    "tobias_cultivated_menace": [
        "cultivated menace",
        "concealed menace",
        "chilling patience",
        "cold threat",
    ],
    "tobias_polished_probe": [
        "polished probing",
        "probing calm",
        "polished control",
        "subtle threat",
    ],
    "zebulon_nervous_analysis": [
        "nervous analysis",
        "mounting strain",
        "defensive precision",
        "unstable focus",
    ],
    "zebulon_intense_questioning": [
        "intense questioning",
        "analytical questioning",
        "intellectual control",
        "beginning to fracture",
    ],
    "hater_wounded_fury": [
        "wounded fury",
        "wounded pride",
        "grave challenge",
        "alien formality",
    ],
    "karvellis_amplified_command": [
        "amplified command",
        "hard command",
        "clipped urgency",
        "zero warmth",
    ],
    "lubineki_rough_jovial": [
        "rough jovial",
        "jovial concern",
        "blunt humor",
        "blunt humour",
    ],
    "powerless_panicked_urgency": [
        "panicked urgency",
        "exposed panic",
        "strained projection",
        "alien vulnerability",
    ],
    "rashid_tired_authority": [
        "tired authority",
        "bureaucratic impatience",
        "dry bluntness",
        "tired bureaucratic",
    ],
    "under_sergeant_military_menace": [
        "military menace",
        "controlled menace",
        "hard authority",
        "jungle warfare",
    ],
}

INDEX_STRENGTH = {
    "beltempest_interrogative_impatience": 0.80,
    "beltempest_military_volatility": 0.90,
    "beltempest_weary_resignation": 0.75,
    "tobias_cultivated_menace": 0.90,
    "zebulon_nervous_analysis": 0.90,
    "powerless_panicked_urgency": 1.00,
}

FISH_TAGS = {
    "karvellis_amplified_command": (
        "Speak as a hard amplified command: clipped, urgent, penetrating, and cold."
    ),
    "lubineki_rough_jovial": (
        "Speak with rough jovial confidence, blunt humour, and alert concern."
    ),
}

PRODUCTION_BACKEND = {
    "fish_s2_pro_free_zero_shot": FISH_ROUTE_BACKEND_ID,
}


class NoncoreVoicePackError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NoncoreVoicePackError(f"{label} could not be read: {exc}") from exc
    if not isinstance(value, dict):
        raise NoncoreVoicePackError(f"{label} must contain an object.")
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
        temporary.unlink(missing_ok=True)


def _mode_map(answer: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    modes = answer.get("modes")
    if not isinstance(modes, list):
        raise NoncoreVoicePackError("Multimodel answer key has no mode list.")
    result: dict[str, dict[str, Any]] = {}
    for raw in modes:
        if not isinstance(raw, dict):
            continue
        mode_id = str(raw.get("mode_id") or "").strip()
        if mode_id:
            result[mode_id] = copy.deepcopy(raw)
    return result


def _reference_source(
    *,
    answer_root: Path,
    mode: Mapping[str, Any],
    kind: str,
) -> dict[str, Any]:
    for raw in mode.get("public_references") or []:
        if isinstance(raw, Mapping) and raw.get("kind") == kind:
            relative = str(raw.get("audio") or "").removeprefix("../")
            source = (answer_root / relative).resolve()
            expected = str(raw.get("audio_sha256") or "")
            if not source.is_file() or sha256_file(source) != expected:
                raise NoncoreVoicePackError(
                    f"Approved {kind} reference is missing or changed for {mode['mode_id']}."
                )
            transcript = str(raw.get("transcript") or "").strip()
            if not transcript:
                raise NoncoreVoicePackError(
                    f"Approved {kind} reference has no transcript for {mode['mode_id']}."
                )
            return {
                "source": source,
                "sha256": expected,
                "transcript": transcript,
            }
    raise NoncoreVoicePackError(
        f"Approved {kind} reference is missing for {mode['mode_id']}."
    )


def _safe_name(value: str) -> str:
    return "_".join(
        part for part in "".join(
            character.casefold() if character.isalnum() else " "
            for character in value
        ).split()
        if part
    )


def _route_control(
    *,
    mode_id: str,
    backend: str,
    mode: Mapping[str, Any],
) -> dict[str, Any]:
    if backend == INDEXTTS2_ROUTE_BACKEND_ID:
        return {
            "emotion_strength": INDEX_STRENGTH[mode_id],
            "diffusion_steps": 8,
            "num_beams": 1,
            "greedy": True,
            "max_mel_tokens": 600,
        }
    if backend == VOXCPM2_ROUTE_BACKEND_ID:
        instruction = " ".join(
            value.strip()
            for value in (
                str(mode.get("target_instruct") or ""),
                str(mode.get("review_instruction") or ""),
            )
            if value.strip()
        )
        return {
            "instruction": instruction,
            "cfg_value": 2.0,
            "inference_timesteps": 10,
            "warmup_patches": 0,
            "max_tokens": 1800,
        }
    if backend == FISH_ROUTE_BACKEND_ID:
        return {
            "reference_mode": "inline_zero_shot",
            "api_model_header": "s2.1-pro-free",
            "prompt_mode": "full_alexandria_tag",
            "tag": FISH_TAGS[mode_id],
            "temperature": 0.7,
            "top_p": 0.7,
            "repetition_penalty": 1.2,
        }
    if backend == INSTRUCTION_CONTROLLED_ENGINE_ID:
        return {}
    raise NoncoreVoicePackError(f"Unsupported selected backend: {backend}.")


def build_noncore_routing_policies(
    *,
    project_root: str | Path,
    answer_key_path: str | Path,
    decision_path: str | Path,
    install_assets: bool,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    root = Path(project_root).expanduser().resolve()
    answer_path = Path(answer_key_path).expanduser().resolve()
    answer = _read_json(answer_path, "Multimodel answer key")
    decision = _read_json(Path(decision_path).expanduser().resolve(), "Multimodel decision")
    if answer.get("round_id") != "alexandria_original_sin_noncore_multimodel_round_v2":
        raise NoncoreVoicePackError("Multimodel answer key belongs to another round.")
    if decision.get("round_id") != answer.get("round_id"):
        raise NoncoreVoicePackError("Multimodel decision belongs to another round.")
    selected = decision.get("selected")
    if not isinstance(selected, Mapping) or set(selected) != set(VOICE_BY_MODE):
        raise NoncoreVoicePackError("Multimodel decision does not contain the 14 approved modes.")
    voice_config = _read_json(root / "voice_config.json", "Voice configuration")
    modes = _mode_map(answer)
    answer_root = answer_path.parents[1]
    routes_by_voice: dict[str, dict[str, Any]] = {}
    installed_assets: list[dict[str, Any]] = []

    for mode_id, selected_row in selected.items():
        voice_name = VOICE_BY_MODE[mode_id]
        mode = modes.get(mode_id)
        voice = voice_config.get(voice_name)
        if not isinstance(mode, Mapping) or not isinstance(voice, Mapping):
            raise NoncoreVoicePackError(f"Voice or mode is missing: {mode_id}.")
        identity_relative = str(voice.get("ref_audio") or "").strip()
        identity_text = str(voice.get("ref_text") or "").strip()
        identity_path = (root / identity_relative).resolve()
        if not identity_relative or not identity_text or not identity_path.is_file():
            raise NoncoreVoicePackError(f"Voice identity is incomplete: {voice_name}.")
        identity_sha256 = sha256_file(identity_path)
        identity_reference = _reference_source(
            answer_root=answer_root,
            mode=mode,
            kind="identity",
        )
        if identity_sha256 != identity_reference["sha256"]:
            raise NoncoreVoicePackError(f"Voice identity drifted for {voice_name}.")
        delivery_reference = _reference_source(
            answer_root=answer_root,
            mode=mode,
            kind="delivery",
        )
        delivery_relative = (
            ASSET_ROOT
            / _safe_name(voice_name)
            / f"{mode_id}{delivery_reference['source'].suffix.casefold()}"
        )
        delivery_destination = root / delivery_relative
        if install_assets:
            _atomic_copy(delivery_reference["source"], delivery_destination)
        if not delivery_destination.is_file() or sha256_file(delivery_destination) != delivery_reference["sha256"]:
            raise NoncoreVoicePackError(f"Installed delivery reference failed verification: {mode_id}.")
        installed_assets.append(
            {
                "relative_path": delivery_relative.as_posix(),
                "sha256": delivery_reference["sha256"],
                "mode_id": mode_id,
            }
        )
        reviewed_backend = str(selected_row.get("backend") or "")
        backend = PRODUCTION_BACKEND.get(reviewed_backend, reviewed_backend)
        use_performance = backend in {
            INDEXTTS2_ROUTE_BACKEND_ID,
            INSTRUCTION_CONTROLLED_ENGINE_ID,
        }
        effect = selected_row.get("effect_processing")
        effect_chain = effect.get("chain") if isinstance(effect, Mapping) else None
        route = {
            "backend": backend,
            "instruction_keywords": list(ROUTE_KEYWORDS[mode_id]),
            "identity_audio": identity_relative,
            "identity_audio_sha256": identity_sha256,
            "identity_text": identity_text,
            "performance_audio": (
                delivery_relative.as_posix() if use_performance else None
            ),
            "performance_audio_sha256": (
                delivery_reference["sha256"] if use_performance else None
            ),
            "performance_text": (
                delivery_reference["transcript"] if use_performance else None
            ),
            "control": _route_control(
                mode_id=mode_id,
                backend=backend,
                mode=mode,
            ),
            "effect_chain": effect_chain,
            "approval_tier": selected_row.get("approval_tier", "strict"),
            "production_promotion_allowed": True,
        }
        routes_by_voice.setdefault(voice_name, {})[mode_id] = route

    policies: dict[str, dict[str, Any]] = {}
    for voice_name, routes in routes_by_voice.items():
        voice = voice_config[voice_name]
        identity_relative = str(voice["ref_audio"])
        identity_path = (root / identity_relative).resolve()
        neutral = {
            "backend": INSTRUCTION_CONTROLLED_ENGINE_ID,
            "instruction_keywords": [],
            "identity_audio": identity_relative,
            "identity_audio_sha256": sha256_file(identity_path),
            "identity_text": str(voice["ref_text"]),
            "performance_audio": None,
            "performance_audio_sha256": None,
            "performance_text": None,
            "control": {},
            "effect_chain": None,
            "approval_tier": "strict",
            "production_promotion_allowed": True,
        }
        policy = {
            "schema_version": 1,
            "enabled": True,
            "default_route": "neutral",
            "fallback_backend": INSTRUCTION_CONTROLLED_ENGINE_ID,
            "evidence_round_id": EVIDENCE_ROUND_ID,
            "production_promotion_allowed": True,
            "routes": {"neutral": neutral, **routes},
        }
        policies[voice_name] = validate_recurring_voice_routing(
            policy,
            project_root=root,
            verify_audio=True,
        )
    return policies, installed_assets


def install_noncore_quasi_emotive_voices(
    *,
    project_root: str | Path,
    answer_key_path: str | Path,
    decision_path: str | Path,
    confirm_production_opt_in: bool,
    approved_at_utc: str | None = None,
) -> dict[str, Any]:
    if confirm_production_opt_in is not True:
        raise NoncoreVoicePackError(
            "Non-core quasi-emotive Voice installation requires explicit confirmation."
        )
    root = Path(project_root).expanduser().resolve()
    voice_config_path = root / "voice_config.json"
    config = _read_json(voice_config_path, "Voice configuration")
    before_config = voice_config_path.read_bytes()
    decision_path = Path(decision_path).expanduser().resolve()
    answer_key_path = Path(answer_key_path).expanduser().resolve()
    decision = _read_json(decision_path, "Multimodel decision")
    answer = _read_json(answer_key_path, "Multimodel answer key")
    selected = decision.get("selected") or {}
    modes = _mode_map(answer)
    destinations: dict[Path, bytes | None] = {}
    for mode_id in selected:
        mode = modes[mode_id]
        delivery = _reference_source(
            answer_root=answer_key_path.parents[1],
            mode=mode,
            kind="delivery",
        )
        destination = (
            root
            / ASSET_ROOT
            / _safe_name(VOICE_BY_MODE[mode_id])
            / f"{mode_id}{delivery['source'].suffix.casefold()}"
        )
        destinations[destination] = destination.read_bytes() if destination.is_file() else None
    approved_at = approved_at_utc or utc_now()
    try:
        policies, assets = build_noncore_routing_policies(
            project_root=root,
            answer_key_path=answer_key_path,
            decision_path=decision_path,
            install_assets=True,
        )
        updated = copy.deepcopy(config)
        for voice_name, policy in policies.items():
            voice = copy.deepcopy(dict(updated[voice_name]))
            voice["clone_backend"] = ROUTED_CLONE_BACKEND
            voice["seed"] = str(PRODUCTION_SEED)
            voice["responsive_backend_routing"] = copy.deepcopy(policy)
            voice["responsive_backend_configuration_fingerprint"] = (
                routing_fingerprint(policy)
            )
            updated[voice_name] = voice
        validate_voice_aliases(updated)
        atomic_json_write(updated, voice_config_path)
        operation_id = "audio_dependency_" + fingerprint_value(
            {
                "operation": "noncore_quasi_emotive_voice_install",
                "pack_id": PACK_ID,
                "approved_at_utc": approved_at,
                "voices": sorted(policies),
                "decision_fingerprint": fingerprint_value(decision),
            }
        )[:24]
        invalidation = apply_project_audio_invalidation(
            project_root=root,
            operation_id=operation_id,
            operation="noncore_quasi_emotive_voice_install",
            at_utc=approved_at,
            speakers=set(policies),
            reason="Non-core characters changed to reviewed multimodel quasi-emotive routing",
            dependency_before={
                voice_config_path: before_config,
                **destinations,
            },
        )
    except Exception:
        voice_config_path.write_bytes(before_config)
        for destination, content in destinations.items():
            if content is None:
                destination.unlink(missing_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
        raise
    receipt = {
        "schema_version": 1,
        "status": "installed",
        "pack_id": PACK_ID,
        "evidence_round_id": EVIDENCE_ROUND_ID,
        "approved_at_utc": approved_at,
        "operation_id": operation_id,
        "voices": sorted(policies),
        "selected_mode_count": len(selected),
        "strict_mode_count": sum(
            row.get("approval_tier") == "strict" for row in selected.values()
        ),
        "restricted_mode_count": sum(
            row.get("approval_tier") == "restricted_user_accepted"
            for row in selected.values()
        ),
        "unsupported_modes": sorted((decision.get("unsupported_modes") or {}).keys()),
        "assets": assets,
        "routing_fingerprints": {
            voice: routing_fingerprint(policy) for voice, policy in policies.items()
        },
        "decision_fingerprint": fingerprint_value(decision),
        "answer_key_fingerprint": fingerprint_value(answer),
        "production_seed": PRODUCTION_SEED,
        "audio_invalidation": invalidation,
    }
    receipt["receipt_fingerprint"] = fingerprint_value(receipt)
    atomic_json_write(receipt, root / RECEIPT_FILENAME)
    return receipt


def rollback_noncore_quasi_emotive_voices(
    *,
    project_root: str | Path,
    confirm_rollback: bool,
    rolled_back_at_utc: str | None = None,
) -> dict[str, Any]:
    if confirm_rollback is not True:
        raise NoncoreVoicePackError("Non-core Voice rollback requires confirmation.")
    root = Path(project_root).expanduser().resolve()
    receipt_path = root / RECEIPT_FILENAME
    receipt = _read_json(receipt_path, "Non-core Voice receipt")
    if receipt.get("pack_id") != PACK_ID or receipt.get("status") != "installed":
        raise NoncoreVoicePackError("Non-core Voice receipt is not available for rollback.")
    undone = undo_project_audio_invalidation(
        project_root=root,
        operation_id=str(receipt["operation_id"]),
        undone_at_utc=rolled_back_at_utc or utc_now(),
    )
    updated = {
        **receipt,
        "status": "rolled_back",
        "rolled_back_at_utc": rolled_back_at_utc or utc_now(),
        "rollback": undone,
    }
    updated["receipt_fingerprint"] = fingerprint_value(
        {key: value for key, value in updated.items() if key != "receipt_fingerprint"}
    )
    atomic_json_write(updated, receipt_path)
    return updated


def inspect_noncore_quasi_emotive_voices(
    project_root: str | Path,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    try:
        config = _read_json(root / "voice_config.json", "Voice configuration")
        fingerprints: dict[str, str] = {}
        route_counts: dict[str, int] = {}
        restricted: list[str] = []
        for voice_name in sorted(set(VOICE_BY_MODE.values())):
            voice = config.get(voice_name)
            if not isinstance(voice, Mapping) or voice.get("clone_backend") != ROUTED_CLONE_BACKEND:
                raise NoncoreVoicePackError(f"Voice is not routed: {voice_name}.")
            policy = validate_recurring_voice_routing(
                voice.get("responsive_backend_routing"),
                project_root=root,
                verify_audio=True,
            )
            fingerprint = routing_fingerprint(policy)
            if voice.get("responsive_backend_configuration_fingerprint") != fingerprint:
                raise NoncoreVoicePackError(f"Voice routing fingerprint is stale: {voice_name}.")
            fingerprints[voice_name] = fingerprint
            route_counts[voice_name] = len(policy["routes"]) - 1
            restricted.extend(
                key
                for key, route in policy["routes"].items()
                if route.get("approval_tier") == "restricted_user_accepted"
            )
        return {
            "ready": True,
            "pack_id": PACK_ID,
            "voices": sorted(fingerprints),
            "route_counts": route_counts,
            "restricted_routes": sorted(restricted),
            "routing_fingerprints": fingerprints,
            "error": None,
        }
    except Exception as exc:
        return {
            "ready": False,
            "pack_id": PACK_ID,
            "voices": [],
            "route_counts": {},
            "restricted_routes": [],
            "routing_fingerprints": {},
            "error": str(exc),
        }
