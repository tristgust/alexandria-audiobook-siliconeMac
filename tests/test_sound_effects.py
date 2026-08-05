from __future__ import annotations

import unittest

from sound_effects import (
    SOUND_EFFECT_BACKEND_MESSAGE,
    SoundEffectConfigurationError,
    normalize_sound_effect_configuration,
    sound_effect_backend_status,
)


class SoundEffectTests(unittest.TestCase):
    def test_normalizes_persistent_definition_without_speech_voice(self) -> None:
        value = normalize_sound_effect_configuration(
            {
                "sound_effect_definition": (
                    "  domestic cat, natural close-mic meows   and purrs "
                )
            }
        )
        self.assertEqual(value["type"], "sound_effect")
        self.assertIsNone(value["voice"])
        self.assertIsNone(value["sound_effect_backend"])
        self.assertEqual(
            value["sound_effect_definition"],
            "domestic cat, natural close-mic meows and purrs",
        )

    def test_requires_definition(self) -> None:
        with self.assertRaises(SoundEffectConfigurationError):
            normalize_sound_effect_configuration({})

    def test_backend_status_is_truthfully_unavailable(self) -> None:
        status = sound_effect_backend_status()
        self.assertFalse(status["available"])
        self.assertIsNone(status["backend_id"])
        self.assertEqual(status["message"], SOUND_EFFECT_BACKEND_MESSAGE)


if __name__ == "__main__":
    unittest.main()
