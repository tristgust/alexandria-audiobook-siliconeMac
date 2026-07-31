from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from model_registry import model_spec


PROVENANCE_SCHEMA_VERSION = 1


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _path_label(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    name = Path(text).name
    return name or text


def approved_import_provenance(
    *,
    promotion_id: str,
    candidate_id: str,
    source_round_id: str | None,
    direct_placement_tier: str,
) -> dict[str, Any]:
    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "source": "approved_adaptation_import",
        "recorded": True,
        "runtime": "approved-human-performance",
        "model_id": "adaptation-source-performance",
        "model_revision": None,
        "base_model_id": None,
        "voice_type": "approved_adaptation_audio",
        "voice_method": "direct_performance_import",
        "detail": (
            f"{promotion_id}:{candidate_id}:"
            f"{source_round_id or 'unknown-round'}:{direct_placement_tier}"
        ),
    }


def resolve_audio_generation_provenance(
    voice_data: Mapping[str, Any] | None,
    *,
    mode: str,
    use_mlx: bool,
    source: str,
    external_url: str | None = None,
) -> dict[str, Any]:
    """Describe the model route used, or currently configured, for one chunk."""
    voice = dict(voice_data or {})
    voice_type = _text(voice.get("type")) or "custom"
    voice_method = voice_type
    model_id: str | None = None
    model_revision: str | None = None
    base_model_id: str | None = None
    runtime: str
    detail: str | None = None

    if mode != "local":
        runtime = "external-gradio"
        model_id = (
            _text(voice.get("model_id"))
            or _text(voice.get("model"))
            or "External TTS service"
        )
        detail = _text(external_url)
    elif use_mlx:
        runtime = "mlx-audio"
        if voice_type == "clone":
            clone_backend = _text(voice.get("clone_backend")) or "qwen3_base"
            voice_method = clone_backend
            detail = _text(
                voice.get("approved_adaptation_profile_fingerprint")
            ) or _text(voice.get("reference_bank_fingerprint"))
            spec_key = (
                "mlx_controlled_clone"
                if clone_backend == "voxcpm2_controlled"
                else "mlx_clone"
            )
            spec = model_spec(spec_key)
            model_id = spec.repo_id
            model_revision = spec.revision
        elif voice_type == "design":
            spec = model_spec("mlx_voice_design")
            model_id = spec.repo_id
            model_revision = spec.revision
        elif voice_type in {"lora", "builtin_lora"}:
            voice_method = "merged_lora_clone"
            base = model_spec("mlx_clone")
            base_model_id = base.repo_id
            model_id = (
                _text(voice.get("mlx_model_path"))
                or _text(voice.get("adapter_path"))
                or base.repo_id
            )
            detail = _text(voice.get("adapter_id")) or _path_label(
                voice.get("adapter_path")
            )
        elif voice_type == "community_qvoice":
            family = _text(voice.get("community_pack_family")) or "qvoice_graft"
            voice_method = family
            base = model_spec("mlx_custom_voice")
            base_model_id = base.repo_id
            model_id = (
                _text(voice.get("community_pack_name"))
                or _text(voice.get("community_pack_id"))
                or _path_label(voice.get("community_pack_path"))
                or "Community Qwen Voice pack"
            )
            detail = _text(voice.get("community_pack_sha256"))
        else:
            spec = model_spec("mlx_custom_voice")
            model_id = spec.repo_id
            model_revision = spec.revision
    else:
        runtime = "qwen-tts-pytorch"
        if voice_type == "clone":
            voice_method = _text(voice.get("clone_backend")) or "qwen3_base"
            spec = model_spec("pytorch_qwen_base")
            detail = _text(
                voice.get("approved_adaptation_profile_fingerprint")
            ) or _text(voice.get("reference_bank_fingerprint"))
        elif voice_type == "design":
            spec = model_spec("pytorch_qwen_voice_design")
        elif voice_type in {"lora", "builtin_lora"}:
            voice_method = "peft_lora_clone"
            spec = model_spec("pytorch_qwen_base")
            base_model_id = spec.repo_id
            model_id = _text(voice.get("adapter_path")) or spec.repo_id
            detail = _text(voice.get("adapter_id")) or _path_label(
                voice.get("adapter_path")
            )
            return {
                "schema_version": PROVENANCE_SCHEMA_VERSION,
                "source": source,
                "recorded": source == "generation",
                "runtime": runtime,
                "model_id": model_id,
                "model_revision": None,
                "base_model_id": base_model_id,
                "voice_type": voice_type,
                "voice_method": voice_method,
                "detail": detail,
            }
        else:
            spec = model_spec("pytorch_qwen_custom_voice")
        model_id = spec.repo_id
        model_revision = spec.revision

    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "source": source,
        "recorded": source == "generation",
        "runtime": runtime,
        "model_id": model_id,
        "model_revision": model_revision,
        "base_model_id": base_model_id,
        "voice_type": voice_type,
        "voice_method": voice_method,
        "detail": detail,
    }
