import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FinalDirectRepairPlanTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((ROOT / "benchmarks/original_sin_direct_substitution_final_repair_plan.json").read_text())

    def test_round_is_bounded(self):
        self.assertEqual(len(self.plan["groups"]), 4)
        self.assertEqual(sum(len(g["treatments"]) for g in self.plan["groups"]), 10)

    def test_alternate_exact_lines_are_used(self):
        chunks = {g["character"]: g["chunk_id"] for g in self.plan["groups"]}
        self.assertEqual(chunks["Zebulon Pryce"], 3098)
        self.assertEqual(chunks["Securitybot"], 618)

    def test_no_production_changes(self):
        self.assertFalse(self.plan["production_changes"])


if __name__ == "__main__":
    unittest.main()
