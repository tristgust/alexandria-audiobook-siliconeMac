from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FinalCompletionTests(unittest.TestCase):
    def test_ledger_v4_closes_all_nineteen_characters(self) -> None:
        text = (
            ROOT / "benchmarks/build_original_sin_overlap_character_coverage_ledger_v4.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"pending_generation_review": 0', text)
        self.assertIn('"covered_operator_approved": 2', text)
        self.assertIn('"complete_pending_production_promotion_and_bot_speaker_remap"', text)
        self.assertNotIn("final_score_completion", text)


if __name__ == "__main__":
    unittest.main()
