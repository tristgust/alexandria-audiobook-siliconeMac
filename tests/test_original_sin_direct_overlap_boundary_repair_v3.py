from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "benchmarks/original_sin_direct_overlap_boundary_repair_plan_v3.json"


class BoundaryRepairPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = json.loads(PLAN.read_text(encoding="utf-8"))

    def test_seven_bounded_repairs(self) -> None:
        self.assertEqual({row["chunk_id"] for row in self.plan["groups"]}, {5351, 2745, 2720, 218, 5371, 1320, 2919})

    def test_start_tail_and_trailing_actions_are_explicit(self) -> None:
        actions = {row["chunk_id"]: row["action"] for row in self.plan["groups"]}
        self.assertEqual(actions[5351], "start_trim")
        self.assertEqual(actions[2720], "trailing_trim")
        self.assertEqual(actions[2745], "start_tail_splice")
        self.assertEqual(actions[1320], "tail_splice")

    def test_tail_splice_has_two_takeover_lengths(self) -> None:
        self.assertEqual(self.plan["tail_splice_milliseconds"], [180, 300])

    def test_no_production_changes(self) -> None:
        self.assertFalse(self.plan["production_changes"])


if __name__ == "__main__":
    unittest.main()
