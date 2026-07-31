from __future__ import annotations
import json, unittest
from pathlib import Path

PLAN = Path(__file__).resolve().parents[1] / "benchmarks/original_sin_overlap_reference_boundary_repair_plan_v5.json"

class BoundaryRepairPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.plan = json.loads(PLAN.read_text())
    def test_beltempest_is_required(self):
        row = next(g for g in self.plan["groups"] if g["character"] == "Beltempest")
        self.assertEqual(row["expected_transcript"], "I stand corrected. What would you prefer?")
    def test_round_is_bounded(self):
        self.assertEqual(len(self.plan["groups"]), 4)
        self.assertEqual(sum(len(g["treatments"]) for g in self.plan["groups"]), 12)
    def test_no_production_change(self): self.assertFalse(self.plan["production_changes"])

if __name__ == "__main__": unittest.main()
