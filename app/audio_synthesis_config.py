from __future__ import annotations

from typing import Any, Mapping


LEGACY_LOCAL_TTS_DEFAULTS: dict[str, Any] = {
    "mode": "local",
    "url": "http://127.0.0.1:7860",
    "device": "auto",
    "language": "Auto",
    "parallel_workers": 2,
    "batch_seed": None,
    "compile_codec": False,
    "sub_batch_enabled": True,
    "sub_batch_min_size": 4,
    "sub_batch_ratio": 5.0,
    "sub_batch_max_items": 0,
    "batch_group_by_type": True,
}

FISH_OUTPUT_BINDING_FIELDS = frozenset(
    {
        "fish_model",
        "fish_candidate_count",
        "fish_difficult_candidate_count",
        "fish_text_wer_limit",
    }
)


def synthesis_binding_config(
    tts_config: Mapping[str, Any] | None,
    *,
    voice_data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return settings that bind one chunk to its synthesized audio.

    Local Qwen audio historically bound the complete local TTS defaults. The
    Fish-enabled application config stores provider controls beside those
    defaults, so normalize missing legacy defaults and include Fish selection
    controls only for Voices that actually use Fish.
    """
    value = dict(tts_config or {})
    value.pop("pause_between_speakers_ms", None)
    value.pop("pause_same_speaker_ms", None)

    if str(value.get("mode") or "") == "local":
        for key, default in LEGACY_LOCAL_TTS_DEFAULTS.items():
            value.setdefault(key, default)

    voice = dict(voice_data or {})
    uses_fish = (
        str(voice.get("type") or "custom") == "clone"
        and str(voice.get("clone_backend") or "qwen3_base")
        == "fish_s21_cloud"
    )
    for key in tuple(value):
        if not key.startswith("fish_"):
            continue
        if not uses_fish or key not in FISH_OUTPUT_BINDING_FIELDS:
            value.pop(key, None)
    return value
