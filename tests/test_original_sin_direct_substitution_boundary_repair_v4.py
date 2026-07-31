from __future__ import annotations
import json, unittest
from pathlib import Path

PLAN = Path(__file__).resolve().parents[1] / "benchmarks/original_sin_direct_substitution_boundary_repair_plan_v4.json"

class DirectBoundaryPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.plan = json.loads(PLAN.read_text())
    def test_round_contains_only_boundary_blocked_chunks(self):
        self.assertEqual({g["chunk_id"] for g in self.plan["groups"]}, {5207,3908,3098})
    def test_tail_extensions_are_explicit(self):
        self.assertTrue(all(g["minimum_segment_end_margin_seconds"] > 0 for g in self.plan["groups"]))
    def test_no_production_change(self): self.assertFalse(self.plan["production_changes"])

if __name__ == "__main__": unittest.main()
