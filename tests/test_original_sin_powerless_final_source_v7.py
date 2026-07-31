from __future__ import annotations

import json
import unittest
from pathlib import Path


PLAN = Path(__file__).resolve().parents[1] / "benchmarks/original_sin_powerless_final_source_plan_v7.json"


class PowerlessFinalPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = json.loads(PLAN.read_text())

    def test_uses_different_exact_line(self):
        self.assertEqual(self.plan["chunk_id"], 1322)
        self.assertEqual(self.plan["expected_transcript"], "You tortured me!")

    def test_round_is_bounded(self):
        self.assertEqual(len(self.plan["treatments"]), 3)

    def test_asr_alias_does_not_change_spoken_target(self):
        self.assertEqual(self.plan["alignment_word_aliases"], {"tortured": ["torture"]})

    def test_no_production_changes(self):
        self.assertFalse(self.plan["production_changes"])


if __name__ == "__main__":
    unittest.main()
