import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FinalReferenceRepairPlanTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((ROOT / "benchmarks/original_sin_overlap_reference_final_repair_plan.json").read_text())

    def test_round_is_bounded(self):
        self.assertEqual(len(self.plan["groups"]), 6)
        self.assertEqual(sum(len(g["treatments"]) for g in self.plan["groups"]), 16)

    def test_under_sergeant_uses_non_intercom_line(self):
        group = next(g for g in self.plan["groups"] if g["character"] == "Under-Sergeant")
        self.assertEqual(group["segment_start"], 999)
        self.assertIn("non-intercom", group["review_context"])

    def test_no_production_changes(self):
        self.assertFalse(self.plan["production_changes"])


if __name__ == "__main__":
    unittest.main()
