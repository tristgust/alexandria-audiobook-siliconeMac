from __future__ import annotations

from typing import Any, Mapping


SOUND_EFFECT_SCHEMA_VERSION = 1
SOUND_EFFECT_BACKEND_ID: str | None = None
SOUND_EFFECT_BACKEND_MESSAGE = (
    "No approved sound-effect generation backend is installed. Alexandria "
    "will not send this non-speech role through text-to-speech."
)


class SoundEffectConfigurationError(ValueError):
    pass


def normalize_sound_effect_configuration(
    value: Mapping[str, Any] | None,
) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    definition = " ".join(
        str(
            source.get("sound_effect_definition")
            or source.get("definition")
            or source.get("description")
            or ""
        ).split()
    ).strip()
    if not definition:
        raise SoundEffectConfigurationError(
            "Describe the non-speech sound this character should produce."
        )
    if len(definition) > 4000:
        raise SoundEffectConfigurationError(
            "The sound-effect definition is too long."
        )
    return {
        "type": "sound_effect",
        "voice": None,
        "sound_effect_schema_version": SOUND_EFFECT_SCHEMA_VERSION,
        "sound_effect_definition": definition,
        "sound_effect_backend": SOUND_EFFECT_BACKEND_ID,
        "description": definition,
        "character_style": "",
    }


def sound_effect_backend_status() -> dict[str, Any]:
    return {
        "available": SOUND_EFFECT_BACKEND_ID is not None,
        "backend_id": SOUND_EFFECT_BACKEND_ID,
        "schema_version": SOUND_EFFECT_SCHEMA_VERSION,
        "message": SOUND_EFFECT_BACKEND_MESSAGE,
    }


def sound_effect_generation_error() -> str:
    return SOUND_EFFECT_BACKEND_MESSAGE
