from __future__ import annotations

from pathlib import Path
import runpy
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks/build_original_sin_noncore_multimodel_round_v2.py"


class NoncoreMultimodelRoundContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = SCRIPT.read_text(encoding="utf-8")
        for value in (ROOT / "app", ROOT / "benchmarks"):
            if str(value) not in sys.path:
                sys.path.insert(0, str(value))
        namespace = runpy.run_path(str(SCRIPT), run_name="multimodel_contract")
        cls.round_id = namespace["ROUND_ID"]
        cls.matrix = namespace["MODEL_MATRIX"]
        cls.effects = namespace["EFFECT_CHAINS"]
        cls.target_overrides = namespace["TARGET_CHUNK_OVERRIDES"]
        cls.research_thresholds = namespace["RESEARCH_ADMISSION_MAX_WER"]

    def test_round_uses_four_real_model_families(self) -> None:
        self.assertEqual(self.round_id, "alexandria_original_sin_noncore_multimodel_round_v2")
        backends = {backend for values in self.matrix.values() for backend in values}
        self.assertEqual(backends, {"qwen3_instruction_controlled", "voxcpm2_controllable_clone", "fish_s2_pro_free_zero_shot", "indextts2_matched_control"})
        self.assertEqual(sum(map(len, self.matrix.values())), 47)

    def test_review_shows_reference_audio_and_requires_completeness(self) -> None:
        for marker in ("Approved adaptation identity reference", "Approved adaptation delivery reference", "Entire line present", "Cut off / incomplete", "Effects / processing", "Model identities are hidden"):
            self.assertIn(marker, self.text)

    def test_effects_are_bounded_to_characters_that_need_them(self) -> None:
        self.assertEqual(set(self.effects), {"powerless_panicked_urgency", "under_sergeant_military_menace", "bot_synthetic_neutral", "computer_interrupted_system"})
        self.assertIn("computer_modulation_v1", self.text)
        self.assertIn("powerless_alien_modulation_v1", self.text)

    def test_hater_uses_a_cleaner_unseen_identity_line(self) -> None:
        self.assertEqual(self.target_overrides, {"hater_wounded_fury": 3803})

    def test_synthetic_alias_admission_is_narrow(self) -> None:
        self.assertEqual(
            self.research_thresholds,
            {
                "bot_synthetic_neutral": 0.40,
                "computer_interrupted_system": 0.35,
            },
        )
        self.assertIn('"500": "five"', self.text)
        self.assertIn('"town": "undertown"', self.text)

    def test_round_is_non_installing_and_objectively_screened(self) -> None:
        for marker in ('"production_routing_changed": False', '"project_audio_changed": False', '"voice_config_changed": False', "evaluate_transcriptions", "final_transcription_gate_failed", "backend_unavailable"):
            self.assertIn(marker, self.text)
        self.assertIn('"maximum_word_error_rate": research_max_wer', self.text)
        self.assertIn("MAX_ACCEPTABLE_WER = 0.25", self.text)

    def test_resume_reuses_verified_candidates_without_regeneration(self) -> None:
        self.assertIn('parser.add_argument("--resume"', self.text)
        self.assertIn("previous_by_slot", self.text)
        self.assertIn("resumed_from_previous_build", self.text)
        self.assertIn("Previous candidate changed", self.text)

    def test_fish_uses_character_correct_inline_reference(self) -> None:
        self.assertIn("generate_zero_shot", self.text)
        self.assertIn('reference_audio=identity["audio_path"]', self.text)
        self.assertNotIn("631bff1fd20b48e1a4a08db8e936b038", self.text)
        self.assertNotIn("0a23ec9242bf4a42b88ab69f92aa9816", self.text)


if __name__ == "__main__":
    unittest.main()
