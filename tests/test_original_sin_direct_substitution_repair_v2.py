from __future__ import annotations

import json
import unittest
from pathlib import Path


class DirectRepairPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = json.loads((Path(__file__).parents[1] / "benchmarks" / "original_sin_direct_substitution_repair_plan_v2.json").read_text())

    def test_plan_is_bounded_to_five_rejected_chunks(self):
        self.assertEqual({group["chunk_id"] for group in self.plan["groups"]}, {405, 5207, 3106, 3908, 493})

    def test_candidate_count_matches(self):
        self.assertEqual(sum(len(group["treatments"]) for group in self.plan["groups"]), self.plan["candidate_count"])

    def test_no_production_changes(self):
        self.assertFalse(self.plan["production_changes"])


if __name__ == "__main__":
    unittest.main()
