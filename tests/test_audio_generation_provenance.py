from __future__ import annotations

import unittest

from audio_generation_provenance import (
    approved_import_provenance,
    resolve_audio_generation_provenance,
)


class AudioGenerationProvenanceTests(unittest.TestCase):
    def test_fish_clone_records_cloud_model(self) -> None:
        result = resolve_audio_generation_provenance(
            {"type": "clone", "clone_backend": "fish_s21_cloud"},
            mode="local",
            use_mlx=True,
            source="generation",
            fish_model="s2.1-pro-free",
        )
        self.assertTrue(result["recorded"])
        self.assertEqual(result["runtime"], "fish-audio-cloud")
        self.assertEqual(result["model_id"], "s2.1-pro-free")
        self.assertEqual(result["voice_method"], "fish_s21_cloud")

    def test_mlx_instruction_clone_records_exact_base_model(self) -> None:
        result = resolve_audio_generation_provenance(
            {
                "type": "clone",
                "clone_backend": "qwen3_instruction_controlled",
            },
            mode="local",
            use_mlx=True,
            source="generation",
        )

        self.assertTrue(result["recorded"])
        self.assertEqual(result["runtime"], "mlx-audio")
        self.assertEqual(
            result["model_id"],
            "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
        )
        self.assertEqual(
            result["voice_method"],
            "qwen3_instruction_controlled",
        )

    def test_legacy_inference_is_explicitly_not_recorded(self) -> None:
        result = resolve_audio_generation_provenance(
            {"type": "design"},
            mode="local",
            use_mlx=True,
            source="current_voice_config",
        )

        self.assertFalse(result["recorded"])
        self.assertEqual(result["source"], "current_voice_config")
        self.assertEqual(
            result["model_id"],
            "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit",
        )

    def test_lora_records_adapter_and_base_model(self) -> None:
        result = resolve_audio_generation_provenance(
            {
                "type": "lora",
                "adapter_id": "narrator-pilot",
                "mlx_model_path": "/tmp/narrator-pilot/mlx_model",
            },
            mode="local",
            use_mlx=True,
            source="generation",
        )

        self.assertEqual(result["voice_method"], "merged_lora_clone")
        self.assertEqual(result["detail"], "narrator-pilot")
        self.assertEqual(
            result["base_model_id"],
            "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
        )

    def test_approved_import_is_recorded_as_human_performance(self) -> None:
        result = approved_import_provenance(
            promotion_id="promotion-v2",
            candidate_id="candidate-7",
            source_round_id="review-round",
            direct_placement_tier="strict_clean",
        )

        self.assertTrue(result["recorded"])
        self.assertEqual(result["source"], "approved_adaptation_import")
        self.assertEqual(result["runtime"], "approved-human-performance")
        self.assertEqual(result["voice_method"], "direct_performance_import")


if __name__ == "__main__":
    unittest.main()
