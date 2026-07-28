"""Pinned MLX model selection and exact cached snapshot loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any, NoReturn

from multimodel_round1_mlx_support import GenerationInputError, exact_snapshot, load_model


class SupportedModel(str):
    """Stable model keys used by the manifest and evaluator."""

    VOXCPM2 = "voxcpm2"
    QWEN3_TTS = "qwen3_tts"
    FISH_S2_PRO = "fish_s2_pro"
    MOSS_TTS_LOCAL_V15 = "moss_tts_local_v15"


def assert_never(value: str) -> NoReturn:
    """Raise a typed error if a match receives an unsupported model key."""

    raise GenerationInputError("model", f"unsupported model key: {value}")


def load_requested_models(
    model_key: str,
    samples: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Load only the exact snapshots required by the selected model."""

    loaded: dict[str, Any] = {}
    snapshots: dict[str, str] = {}
    match model_key:
        case SupportedModel.VOXCPM2:
            loaded["main"], snapshot = load_model(
                "mlx-community/VoxCPM2-4bit",
                "dc9e5c187858da5f4a13dc4c247e297339216381",
            )
            snapshots["main"] = str(snapshot)
        case SupportedModel.QWEN3_TTS:
            loaded["base"], base_snapshot = load_model(
                "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
                "e7dd0585652209fa0d7783659aad4e8a324de11c",
            )
            snapshots["base"] = str(base_snapshot)
            if any(
                item["identity_key"] == "native_qwen_aiden" for item in samples
            ):
                loaded["custom"], custom_snapshot = load_model(
                    "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit",
                    "41d3337e8b7f2843a75841595fc14e4b9a7a4b96",
                )
                snapshots["custom"] = str(custom_snapshot)
        case SupportedModel.FISH_S2_PRO:
            loaded["main"], snapshot = load_model(
                "mlx-community/fish-audio-s2-pro",
                "eccd57bf5c1ebc13cb2f993df867f4e49931a36a",
            )
            snapshots["main"] = str(snapshot)
        case SupportedModel.MOSS_TTS_LOCAL_V15:
            loaded["main"], snapshot = load_model(
                "OpenMOSS-Team/MOSS-TTS-Local-Transformer-v1.5",
                "be7766a6735b98bd793f7c79fb720b4d0f5d13b8",
            )
            loaded["tokenizer_snapshot"] = exact_snapshot(
                "OpenMOSS-Team/MOSS-Audio-Tokenizer-v2",
                "f6e20e543b33d2c252a7ef71bdf8aa71e5ff9169",
            )
            loaded["moss_reference_code_cache"] = {}
            snapshots["main"] = str(snapshot)
            snapshots["tokenizer"] = str(loaded["tokenizer_snapshot"])
        case unreachable:
            assert_never(unreachable)
    return loaded, snapshots
