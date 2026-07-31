from __future__ import annotations

import json
import unittest
from pathlib import Path


class RepairV3PlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = json.loads((Path(__file__).parents[1] / "benchmarks" / "original_sin_overlap_reference_repair_plan_v3.json").read_text())

    def test_plan_has_11_groups_and_declared_candidate_count(self):
        self.assertEqual(len(self.plan["groups"]), 11)
        self.assertEqual(sum(len(group["treatments"]) for group in self.plan["groups"]), self.plan["candidate_count"])

    def test_vaughn_is_explicitly_robotic(self):
        vaughn = next(group for group in self.plan["groups"] if group["book_speaker"] == "TOBIAS VAUGHN")
        self.assertIn("machine-bodied", vaughn["review_context"])

    def test_plan_declares_no_production_changes(self):
        self.assertFalse(self.plan["production_changes"])


if __name__ == "__main__":
    unittest.main()
