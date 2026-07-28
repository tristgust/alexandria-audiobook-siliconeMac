"""Identity, support, and generation-control contracts for Round 1."""

from __future__ import annotations

import hashlib
from typing import Any


GENERATION_FAILURES: dict[tuple[str, str, str], dict[str, Any]] = {
    ("chatterbox_multilingual_v3", "narrator", "proud"): {
        "code": "repeated_no_eos_memory_pressure_surge",
        "reason": (
            "Repeated exact-control generation failed to emit EOS and caused "
            "unsafe unified-memory and swap growth."
        ),
        "retry_allowed": False,
        "controls_changed": False,
    }
}


class ManifestContractError(RuntimeError):
    """Raised when a model or coverage contract is not recognized."""


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(*parts: str, length: int = 20) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:length]


def generation_failure_for(
    model_key: str, identity_key: str, style_key: str
) -> dict[str, Any] | None:
    failure = GENERATION_FAILURES.get((model_key, identity_key, style_key))
    return dict(failure) if failure is not None else None


def known_identity_lanes(
    reference_manifest: dict[str, Any], ryan: dict[str, Any]
) -> dict[str, Any]:
    lanes: dict[str, Any] = {}
    for item in reference_manifest["identities"]:
        lanes[item["identity_key"]] = {
            "identity_key": item["identity_key"],
            "review_name": item["label"],
            "kind": "supplied_recording_clone",
            "source_file": item["source_file"],
            "source_sha256": item["source_sha256"],
            "conditioning_file": item["conditioning_file"],
            "conditioning_sha256": item["conditioning_sha256"],
            "conditioning_transcript": item["conditioning_transcript"],
            "conditioning_transcript_sha256": item[
                "conditioning_transcript_sha256"
            ],
            "reference_manifest": f"references/{item['identity_key']}/reference.json",
        }
    lanes["ryan_neutral"] = {
        "identity_key": "ryan_neutral",
        "review_name": "Ryan — neutral anchor",
        "kind": "fixed_neutral_clone_reference",
        "source_file": "ryan/" + ryan["neutral"]["audio_file"],
        "source_sha256": ryan["neutral"]["audio_sha256"],
        "conditioning_file": "ryan/" + ryan["neutral"]["audio_file"],
        "conditioning_sha256": ryan["neutral"]["audio_sha256"],
        "conditioning_transcript": ryan["neutral"]["text"],
        "conditioning_transcript_sha256": ryan["neutral"]["text_sha256"],
        "reference_manifest": "references/ryan/manifest.json",
    }
    lanes["ryan_acted"] = {
        "identity_key": "ryan_acted",
        "review_name": "Ryan — acted anchor",
        "kind": "style_matched_acted_clone_reference",
        "reference_manifest": "references/ryan/manifest.json",
        "style_specific": True,
    }
    return lanes


def acted_reference_for_style(
    ryan: dict[str, Any], style_key: str
) -> dict[str, Any]:
    item = next(row for row in ryan["acted"] if row["style"] == style_key)
    return {
        "source_file": "ryan/" + item["audio_file"],
        "source_sha256": item["audio_sha256"],
        "conditioning_file": "ryan/" + item["audio_file"],
        "conditioning_sha256": item["audio_sha256"],
        "conditioning_transcript": item["text"],
        "conditioning_transcript_sha256": item["text_sha256"],
    }


def support_for(
    model: dict[str, Any], identity_key: str, style_key: str
) -> tuple[bool, str | None]:
    model_key = model["key"]
    if model_key == "higgs_audio_v25":
        return False, (
            "No distinct public Higgs Audio V2.5 checkpoint was identified; "
            "do not substitute Higgs TTS 2 invisibly."
        )
    if model_key == "qwen3_tts":
        if identity_key in {"native_qwen_aiden", "ryan_acted"} or style_key == "neutral":
            return True, None
        return False, (
            "Official Qwen3-TTS Base voice cloning does not accept style "
            "instructions for this identity lane."
        )
    return True, None


def control_for(
    model: dict[str, Any],
    identity_key: str,
    style: dict[str, Any],
    reference: dict[str, Any],
) -> dict[str, Any]:
    model_key = model["key"]
    instruction = style["instruction"]
    base = {
        "requested_instruction": instruction,
        "requested_instruction_sha256": sha256_text(instruction),
        "target_text_sha256": sha256_text(style["target_text"]),
    }
    if model_key == "indextts2":
        return {
            **base,
            "mechanism": "separate_emotion_reference_audio",
            "emotion_reference_file": None if style["key"] == "neutral" else reference["acted_emotion_reference_file"],
            "emotion_reference_sha256": None if style["key"] == "neutral" else reference["acted_emotion_reference_sha256"],
            "emo_alpha": 0.0 if style["key"] == "neutral" else style["index_alpha"],
            "num_beams": 1,
            "greedy": True,
            "diffusion_steps": 8,
            "semantic_instruction_directly_consumed": False,
        }
    if model_key == "voxcpm2":
        return {**base, "mechanism": "reference_plus_instruct", "instruct": instruction, "cfg_value": 2.0, "inference_timesteps": 10, "warmup_patches": 1, "semantic_instruction_directly_consumed": True}
    if model_key == "qwen3_tts":
        if identity_key == "native_qwen_aiden":
            return {**base, "mechanism": "built_in_custom_voice_instruct", "speaker": "Aiden", "instruct": instruction, "semantic_instruction_directly_consumed": True}
        return {**base, "mechanism": "style_matched_reference_prosody" if identity_key == "ryan_acted" and style["key"] != "neutral" else "base_icl_voice_clone", "instruct": None, "semantic_instruction_directly_consumed": False}
    if model_key == "fish_s2_pro":
        return {**base, "mechanism": "reference_transcript_instruct_and_inline_tag", "instruct": instruction, "inline_tag": style.get("fish_tag"), "temperature": 0.7, "top_p": 0.7, "top_k": 30, "semantic_instruction_directly_consumed": True}
    if model_key == "moss_tts_local_v15":
        return {**base, "mechanism": "reference_transcript_and_instruction_hint", "instruction": instruction, "language": "English", "audio_temperature": 1.7, "audio_top_p": 0.8, "audio_top_k": 25, "n_vq_for_inference": 12, "max_tokens": 768, "semantic_instruction_directly_consumed": True}
    if model_key == "chatterbox_multilingual_v3":
        return {**base, "mechanism": "numeric_exaggeration_cfg_proxy", "language_id": "en", "exaggeration": style["chatterbox"]["exaggeration"], "cfg_weight": style["chatterbox"]["cfg_weight"], "semantic_instruction_directly_consumed": False}
    raise ManifestContractError(f"Unknown Round 1 model contract: {model_key}")
