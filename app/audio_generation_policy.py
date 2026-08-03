from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from model_registry import INSTRUCTION_CONTROLLED_ENGINE_ID


SEED_CONTRACT_VERSION = 1
MAX_GENERATION_SEED = (2**31) - 1


class AudioGenerationPolicyError(RuntimeError):
    pass


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _optional_seed(value: Any, label: str) -> int | None:
    if value in (None, "", -1, "-1"):
        return None
    if isinstance(value, bool):
        raise AudioGenerationPolicyError(
            f"{label} must be a non-negative integer or unset."
        )
    try:
        seed = int(value)
    except (TypeError, ValueError) as exc:
        raise AudioGenerationPolicyError(
            f"{label} must be a non-negative integer or unset."
        ) from exc
    if not 0 <= seed <= MAX_GENERATION_SEED:
        raise AudioGenerationPolicyError(
            f"{label} must be between 0 and {MAX_GENERATION_SEED}."
        )
    return seed


def voice_supports_deterministic_seed(voice_data: dict[str, Any]) -> bool:
    """Return true only for production paths that consume ``voice.seed``.

    MLX ordinary custom and ordinary clone generation do not currently expose a
    request seed, so those paths deliberately remain excluded. Instruction-
    controlled clones, designed voices, and experimental LoRA inference do
    consume the saved seed.
    """

    voice_type = str(voice_data.get("type") or "custom")
    if voice_type == "clone":
        return (
            voice_data.get("clone_backend")
            == INSTRUCTION_CONTROLLED_ENGINE_ID
        )
    return voice_type in {"design", "lora", "builtin_lora"}


def _seed_voice_identity(voice_data: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(voice_data)
    for key in (
        "seed",
        "controlled_clone_approval_token",
        "controlled_clone_configuration_fingerprint",
        "selected_reference_style",
        "selected_reference_id",
        "selected_prompt_route",
        "selected_prompt_role",
        "selected_prompt_evidence_round_id",
        "selected_production_voice_sample_id",
        "production_voice_prompt_fingerprint",
        "production_voice_dependency_fingerprint",
        "production_voice_preprocessing_fingerprint",
        "production_voice_pronunciation_fingerprint",
        "production_voice_prompt_instruction",
    ):
        value.pop(key, None)
    return value


def seed_basis_fingerprint(
    *,
    chunk: dict[str, Any],
    resolved_speaker: str,
    voice_data: dict[str, Any],
    synthesis_config: dict[str, Any] | None = None,
    base_seed: int | None = None,
) -> str:
    payload = {
        "contract_version": SEED_CONTRACT_VERSION,
        "base_seed": base_seed,
        "chunk": {
            "id": chunk.get("id"),
            "speaker": chunk.get("speaker", ""),
            "text": chunk.get("text", ""),
            "instruct": chunk.get("instruct", ""),
        },
        "resolved_speaker": resolved_speaker,
        "voice": _seed_voice_identity(voice_data),
        "synthesis": synthesis_config or {},
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def resolve_generation_seed(
    *,
    chunk: dict[str, Any],
    resolved_speaker: str,
    voice_config: dict[str, Any],
    synthesis_config: dict[str, Any] | None = None,
    explicit_seed: int | None = None,
    deterministic_enabled: bool = True,
    deterministic_base_seed: int | None = None,
    seed_supported: bool | None = None,
) -> dict[str, Any]:
    voice_data = voice_config.get(resolved_speaker, {})
    if not isinstance(voice_data, dict):
        voice_data = {}
    supported = (
        voice_supports_deterministic_seed(voice_data)
        if seed_supported is None
        else bool(seed_supported)
    )
    configured_seed = _optional_seed(
        voice_data.get("seed"),
        "Voice seed",
    )
    requested_seed = _optional_seed(
        explicit_seed,
        "Generation seed",
    )
    base_seed = _optional_seed(
        deterministic_base_seed,
        "Deterministic base seed",
    )
    basis = seed_basis_fingerprint(
        chunk=chunk,
        resolved_speaker=resolved_speaker,
        voice_data=voice_data,
        synthesis_config=synthesis_config,
        base_seed=base_seed,
    )

    if requested_seed is not None and not supported:
        raise AudioGenerationPolicyError(
            "This TTS backend does not support deterministic seeds for the "
            "requested generation path."
        )
    if not supported:
        return {
            "supported": False,
            "seed": None,
            "source": "unsupported_backend",
            "basis_fingerprint": basis,
            "base_seed": base_seed,
        }
    if requested_seed is not None:
        return {
            "supported": True,
            "seed": requested_seed,
            "source": "explicit_request",
            "basis_fingerprint": basis,
            "base_seed": base_seed,
        }
    if configured_seed is not None:
        return {
            "supported": True,
            "seed": configured_seed,
            "source": "voice_config",
            "basis_fingerprint": basis,
            "base_seed": base_seed,
        }
    if not deterministic_enabled:
        return {
            "supported": True,
            "seed": None,
            "source": "random_disabled",
            "basis_fingerprint": basis,
            "base_seed": base_seed,
        }

    persisted_seed = _optional_seed(
        chunk.get("generation_seed"),
        "Persisted generation seed",
    )
    if (
        persisted_seed is not None
        and chunk.get("generation_seed_basis") == basis
    ):
        return {
            "supported": True,
            "seed": persisted_seed,
            "source": "persisted_derived",
            "basis_fingerprint": basis,
            "base_seed": base_seed,
        }

    seed_payload = {
        "contract_version": SEED_CONTRACT_VERSION,
        "basis_fingerprint": basis,
        "base_seed": base_seed,
    }
    derived = int.from_bytes(
        hashlib.sha256(_canonical_json(seed_payload)).digest()[:4],
        "big",
    ) & MAX_GENERATION_SEED
    return {
        "supported": True,
        "seed": derived,
        "source": "derived",
        "basis_fingerprint": basis,
        "base_seed": base_seed,
    }


def apply_generation_seed_to_voice_config(
    voice_config: dict[str, Any],
    *,
    resolved_speaker: str,
    resolution: dict[str, Any],
) -> dict[str, Any]:
    seed = resolution.get("seed")
    if not resolution.get("supported") or seed is None:
        return voice_config
    effective = dict(voice_config)
    voice_data = dict(effective.get(resolved_speaker, {}))
    voice_data["seed"] = int(seed)
    effective[resolved_speaker] = voice_data
    return effective


def generation_seed_chunk_fields(
    resolution: dict[str, Any],
) -> dict[str, Any]:
    if not resolution.get("supported"):
        return {
            "generation_seed": None,
            "generation_seed_source": "unsupported_backend",
            "generation_seed_basis": resolution.get("basis_fingerprint"),
        }
    return {
        "generation_seed": resolution.get("seed"),
        "generation_seed_source": resolution.get("source"),
        "generation_seed_basis": resolution.get("basis_fingerprint"),
    }


def persisted_generation_seed_resolution(
    chunk: dict[str, Any],
) -> dict[str, Any] | None:
    basis = chunk.get("generation_seed_basis")
    if not basis:
        return None
    source = chunk.get("generation_seed_source")
    return {
        "supported": source != "unsupported_backend",
        "seed": chunk.get("generation_seed"),
        "source": source,
        "basis_fingerprint": basis,
    }


def generation_seed_synthesis_binding(
    resolution: dict[str, Any],
) -> dict[str, Any]:
    return {
        "generation_seed_contract_version": SEED_CONTRACT_VERSION,
        "generation_seed": resolution.get("seed"),
        "generation_seed_basis": resolution.get("basis_fingerprint"),
    }


def synthesis_config_with_generation_seed(
    synthesis_config: dict[str, Any] | None,
    chunk: dict[str, Any],
) -> dict[str, Any]:
    synthesis = dict(synthesis_config or {})
    resolution = persisted_generation_seed_resolution(chunk)
    if resolution is not None:
        synthesis.update(generation_seed_synthesis_binding(resolution))
    return synthesis
