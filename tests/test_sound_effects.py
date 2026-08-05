from __future__ import annotations

import unittest
from unittest.mock import patch

from sound_effects import (
    SOUND_EFFECT_BACKEND_ID,
    SOUND_EFFECT_BACKEND_MESSAGE,
    SoundEffectConfigurationError,
    build_sound_effect_request,
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
        self.assertEqual(value["sound_effect_backend"], SOUND_EFFECT_BACKEND_ID)
        self.assertEqual(value["sound_effect_schema_version"], 2)
        self.assertEqual(value["sound_effect_duration_seconds"], 3.5)
        self.assertEqual(value["sound_effect_steps"], 8)
        self.assertEqual(value["sound_effect_cfg_scale"], 1.0)
        self.assertEqual(
            value["sound_effect_definition"],
            "domestic cat, natural close-mic meows and purrs",
        )

    def test_requires_definition(self) -> None:
        with self.assertRaises(SoundEffectConfigurationError):
            normalize_sound_effect_configuration({})

    def test_backend_status_reports_missing_model_truthfully(self) -> None:
        missing = {"cached": False, "state": "missing"}
        cached = {"cached": True, "state": "cached"}
        with (
            patch(
                "sound_effects.model_cache_status",
                side_effect=[missing, cached],
            ),
            patch("sound_effects._dependencies_ready", return_value=(True, [])),
        ):
            status = sound_effect_backend_status()
        self.assertFalse(status["available"])
        self.assertEqual(status["backend_id"], SOUND_EFFECT_BACKEND_ID)
        self.assertEqual(status["state"], "model_missing")
        self.assertIn("not cached", status["message"])

    def test_backend_status_reports_ready_only_with_both_snapshots_and_runtime(self) -> None:
        cached = {"cached": True, "state": "cached"}
        with (
            patch(
                "sound_effects.model_cache_status",
                side_effect=[cached, cached],
            ),
            patch("sound_effects._dependencies_ready", return_value=(True, [])),
        ):
            status = sound_effect_backend_status()
        self.assertTrue(status["available"])
        self.assertEqual(status["state"], "ready")
        self.assertEqual(status["sample_rate"], 44100)
        self.assertEqual(status["channels"], 2)

    def test_request_combines_persistent_identity_and_line_direction(self) -> None:
        request = build_sound_effect_request(
            voice_data={
                "sound_effect_definition": "Natural domestic cat meows and purrs",
            },
            chunk={
                "text": "Wolsey answers from beneath the table.",
                "instruct": "A questioning meow followed by a quiet purr.",
            },
            seed=130363,
        )
        self.assertIn("Natural domestic cat meows", request["prompt"])
        self.assertIn("A questioning meow", request["prompt"])
        self.assertIn("no human speech", request["prompt"])
        self.assertEqual(request["settings"]["seed"], 130363)
        self.assertEqual(request["settings"]["sampler"], "pingpong")
        self.assertEqual(len(request["request_fingerprint"]), 64)


if __name__ == "__main__":
    unittest.main()
