from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks/analyze_original_sin_noncore_quasi_emotive_round_v1.py"


class NoncoreDiagnosticAnalyzerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = SCRIPT.read_text(encoding="utf-8")

    def test_written_notes_override_pass_buttons(self) -> None:
        for marker in (
            "cuts off",
            "different person",
            "wrong accent",
            "modulation effect",
            "blocking_reasons",
        ):
            self.assertIn(marker, self.text)

    def test_v1_is_diagnostic_not_production_acceptance(self) -> None:
        self.assertIn(
            "diagnostic_closed_cross_model_acceptance_required",
            self.text,
        )
        self.assertIn('"production_winners_selected": False', self.text)
        self.assertIn('"production_routing_changed": False', self.text)

    def test_cross_model_round_requires_all_supported_engines(self) -> None:
        for backend in (
            "qwen3_instruction_controlled",
            "voxcpm2_controllable_clone",
            "fish_s2_pro_free_zero_shot",
            "indextts2_matched_control",
        ):
            self.assertIn(backend, self.text)
        self.assertIn('"reference_audio_visible": True', self.text)
        self.assertIn('"complete_line_score_required": True', self.text)
        self.assertIn('"identity_effects_score_required": True', self.text)


if __name__ == "__main__":
    unittest.main()
