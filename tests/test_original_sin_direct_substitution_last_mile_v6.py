from __future__ import annotations

import json
import unittest
from pathlib import Path


PLAN = Path(__file__).resolve().parents[1] / "benchmarks/original_sin_direct_substitution_last_mile_plan_v6.json"


class LastMilePlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = json.loads(PLAN.read_text(encoding="utf-8"))

    def test_round_is_bounded_to_two_chunks(self):
        self.assertEqual({row["chunk_id"] for row in self.plan["groups"]}, {1317, 2954})
        self.assertEqual(sum(len(row["treatments"]) for row in self.plan["groups"]), 7)

    def test_powerless_uses_extended_tail_and_blends(self):
        row = next(row for row in self.plan["groups"] if row["chunk_id"] == 1317)
        self.assertGreaterEqual(row["minimum_segment_end_margin_seconds"], 0.17)
        self.assertIn("mossformer2_blend50", row["treatments"])
        self.assertIn("mel_roformer_blend70", row["treatments"])

    def test_zebulon_uses_cleaner_source(self):
        row = next(row for row in self.plan["groups"] if row["character"] == "Zebulon Pryce")
        self.assertEqual(row["expected_transcript"], "I don't need a doctor,")

    def test_no_production_changes(self):
        self.assertFalse(self.plan["production_changes"])


if __name__ == "__main__":
    unittest.main()
