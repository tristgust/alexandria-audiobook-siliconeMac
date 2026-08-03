from __future__ import annotations

import unittest

from audio_synthesis_config import (
    LEGACY_LOCAL_TTS_DEFAULTS,
    synthesis_binding_config,
)


class AudioSynthesisConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fish_enabled = {
            "mode": "local",
            "url": "http://127.0.0.1:7860",
            "language": "Auto",
            "parallel_workers": 2,
            "pause_between_speakers_ms": 500,
            "pause_same_speaker_ms": 250,
            "fish_cloud_enabled": True,
            "fish_model": "s2.1-pro-free",
            "fish_candidate_count": 2,
            "fish_difficult_candidate_count": 6,
            "fish_text_wer_limit": 0.08,
            "fish_timeout_seconds": 240,
            "fish_api_key_configured": True,
        }

    def test_local_qwen_binding_preserves_legacy_defaults_and_ignores_fish(self) -> None:
        result = synthesis_binding_config(
            self.fish_enabled,
            voice_data={
                "type": "clone",
                "clone_backend": "qwen3_instruction_controlled",
            },
        )
        self.assertEqual(result, LEGACY_LOCAL_TTS_DEFAULTS)

    def test_fish_binding_keeps_only_output_affecting_provider_controls(self) -> None:
        result = synthesis_binding_config(
            self.fish_enabled,
            voice_data={"type": "clone", "clone_backend": "fish_s21_cloud"},
        )
        self.assertEqual(result["fish_model"], "s2.1-pro-free")
        self.assertEqual(result["fish_candidate_count"], 2)
        self.assertEqual(result["fish_difficult_candidate_count"], 6)
        self.assertEqual(result["fish_text_wer_limit"], 0.08)
        self.assertNotIn("fish_cloud_enabled", result)
        self.assertNotIn("fish_timeout_seconds", result)
        self.assertNotIn("fish_api_key_configured", result)

    def test_legacy_fixture_without_mode_is_not_reinterpreted(self) -> None:
        fixture = {"language": "English", "parallel_workers": 2}
        self.assertEqual(synthesis_binding_config(fixture), fixture)


if __name__ == "__main__":
    unittest.main()
