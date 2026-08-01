from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping

from qvoice_format import QVoicePack, QwenVoicePackError, parse_qvoice


class CommunityPackFamily(str, Enum):
    QVOICE_GRAFT = "qvoice_graft"
    PEFT_SPEAKER_BUNDLE = "peft_speaker_bundle"
    FULL_CUSTOM_VOICE_CHECKPOINT = "full_custom_voice_checkpoint"


class CommunityPackState(str, Enum):
    READY_FOR_REVIEW = "ready_for_review"
    MLX_CONVERSION_AVAILABLE = "mlx_conversion_available"
    MLX_CONVERSION_REQUIRED = "mlx_conversion_required"


@dataclass(frozen=True, slots=True)
class CommunityPackInspection:
    path: Path
    family: CommunityPackFamily
    state: CommunityPackState
    name: str
    speakers: tuple[str, ...]
    model_size: str
    license_name: str | None
    production_supported: bool
    message: str
    runtime: str | None = None
    conversion_supported: bool = False
    qvoice: QVoicePack | None = None


def _json_mapping(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QwenVoicePackError(
            "community_pack_invalid_json",
            f"The pack configuration is invalid: {path.name}.",
        ) from exc
    if not isinstance(value, dict):
        raise QwenVoicePackError(
            "community_pack_invalid_json",
            f"The pack configuration must be an object: {path.name}.",
        )
    return value


def _speakers(config: Mapping[str, object]) -> tuple[str, ...]:
    talker = config.get("talker_config")
    if not isinstance(talker, dict):
        return ()
    speaker_ids = talker.get("spk_id")
    if not isinstance(speaker_ids, dict):
        return ()
    return tuple(sorted(str(name) for name in speaker_ids if str(name).strip()))


def _license_name(directory: Path) -> str | None:
    metadata = directory / "voice_pack.json"
    if not metadata.is_file():
        return None
    try:
        value = _json_mapping(metadata).get("license")
    except QwenVoicePackError:
        # License metadata is informational and never a compatibility gate.
        return None
    return str(value).strip() if isinstance(value, str) and value.strip() else None


def inspect_community_pack(path: str | Path) -> CommunityPackInspection:
    source = Path(path).expanduser().resolve()
    if source.is_file() and source.suffix.lower() == ".qvoice":
        pack = parse_qvoice(source, expected_encoder_dimension=2048)
        runtime_ready = pack.xvector_only and not pack.has_icl
        return CommunityPackInspection(
            path=source,
            family=CommunityPackFamily.QVOICE_GRAFT,
            state=(
                CommunityPackState.READY_FOR_REVIEW
                if runtime_ready
                else CommunityPackState.MLX_CONVERSION_REQUIRED
            ),
            name=pack.voice_name or source.stem,
            speakers=(pack.voice_name or source.stem,),
            model_size="1b7",
            license_name=None,
            production_supported=False,
            message=(
                "Compatible QVCE x-vector graft. Preview and listening review required."
                if runtime_ready
                else "This QVCE ICL prompt is parseable but the current MLX runtime "
                "cannot preserve its prompt and Alexandria's line instructions together."
            ),
            runtime="mlx_qvoice_graft" if runtime_ready else None,
            qvoice=pack,
        )
    if not source.is_dir():
        raise QwenVoicePackError(
            "community_pack_missing",
            f"The community voice pack does not exist: {source}",
        )
    adapter = source / "adapter_config.json"
    adapter_weights = source / "adapter_model.safetensors"
    embedding = source / "speaker_embedding.safetensors"
    tts_config = source / "tts_config.json"
    model = source / "model.safetensors"
    config = source / "config.json"
    if (
        adapter.is_file()
        and adapter_weights.is_file()
        and embedding.is_file()
        and tts_config.is_file()
    ):
        tts = _json_mapping(tts_config)
        adapter_config = _json_mapping(adapter)
        if str(adapter_config.get("peft_type") or "LORA").upper() != "LORA":
            raise QwenVoicePackError(
                "community_pack_peft_type_unsupported",
                "Only LoRA PEFT speaker bundles are supported.",
            )
        if adapter_config.get("use_dora"):
            raise QwenVoicePackError(
                "community_pack_dora_unsupported",
                "DoRA speaker bundles are not supported by the MLX overlay runtime.",
            )
        speakers = _speakers(tts)
        if len(speakers) != 1:
            raise QwenVoicePackError(
                "community_pack_speaker_count_invalid",
                "A PEFT speaker bundle must define exactly one speaker.",
            )
        return CommunityPackInspection(
            path=source,
            family=CommunityPackFamily.PEFT_SPEAKER_BUNDLE,
            state=CommunityPackState.READY_FOR_REVIEW,
            name=source.name,
            speakers=speakers,
            model_size=str(tts.get("tts_model_size") or "unknown"),
            license_name=_license_name(source),
            production_supported=False,
            message="PEFT bundle can run as a low-disk MLX overlay on Alexandria's "
            "cached CustomVoice model. The source directory is linked, not copied.",
            runtime="mlx_peft_overlay",
        )
    if model.is_file() and config.is_file():
        checkpoint = _json_mapping(config)
        if checkpoint.get("tts_model_type") != "custom_voice":
            raise QwenVoicePackError(
                "community_pack_wrong_model_type",
                "The full checkpoint is not a Qwen CustomVoice model.",
            )
        speakers = _speakers(checkpoint)
        if not speakers:
            raise QwenVoicePackError(
                "community_pack_speakers_missing",
                "The full CustomVoice checkpoint does not define a speaker.",
            )
        return CommunityPackInspection(
            path=source,
            family=CommunityPackFamily.FULL_CUSTOM_VOICE_CHECKPOINT,
            state=CommunityPackState.MLX_CONVERSION_AVAILABLE,
            name=source.name,
            speakers=speakers,
            model_size=str(checkpoint.get("tts_model_size") or "unknown"),
            license_name=_license_name(source),
            production_supported=False,
            message="Full CustomVoice checkpoint can be converted with Alexandria's "
            "guarded low-disk MLX path. Conversion is blocked unless the drive "
            "will retain a 16 GiB safety reserve.",
            runtime="mlx_checkpoint",
            conversion_supported=True,
        )
    raise QwenVoicePackError(
        "community_pack_unrecognized",
        "The selected files do not match a supported Qwen voice-pack family.",
    )


__all__ = [
    "CommunityPackFamily",
    "CommunityPackInspection",
    "CommunityPackState",
    "QVoicePack",
    "QwenVoicePackError",
    "inspect_community_pack",
    "parse_qvoice",
]
