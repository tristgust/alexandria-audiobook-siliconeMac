from __future__ import annotations

from pathlib import Path
import runpy
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FinalReviewAnalyzerTests(unittest.TestCase):
    def test_final_winners_are_exact_and_operator_passes_are_actionable(self) -> None:
        namespace = runpy.run_path(
            str(ROOT / "benchmarks/analyze_original_sin_overlap_final_character_round_v5.py"),
            run_name="final_v5_analyzer_test",
        )
        self.assertEqual(namespace["DOCTOR_WINNER"], "3b81e79b4db7b9e7")
        self.assertEqual(namespace["SHYTHE_WINNER"], "a4eb313f21abbc67")
        self.assertEqual(namespace["DANTALION_WINNER"], "22f71b41cbee4305")
        self.assertEqual(namespace["DANTALION_ALTERNATE"], "3e27fa9d49bbf575")
        self.assertNotIn("PENDING_COMPLETION", namespace)


    def test_dantalion_first_mode_requires_all_fives(self) -> None:
        text = (
            ROOT / "benchmarks/analyze_original_sin_dantalion_mode_completion_round_v1.py"
        ).read_text(encoding="utf-8")
        self.assertIn('CANDIDATE_ID = "0dff7471f2e22ead"', text)
        self.assertIn("min(scores.values()) != 5", text)


if __name__ == "__main__":
    unittest.main()
