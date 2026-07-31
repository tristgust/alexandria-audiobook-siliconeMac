from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "benchmarks/original_sin_direct_overlap_boundary_repair_plan_v4.json"


class BoundaryRepairV4PlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = json.loads(PLAN.read_text(encoding="utf-8"))

    def test_six_bounded_repairs(self) -> None:
        self.assertEqual({row["chunk_id"] for row in self.plan["groups"]}, {2720, 4432, 658, 1575, 3989, 3036})

    def test_actions_are_explicit(self) -> None:
        actions = {row["chunk_id"]: row["action"] for row in self.plan["groups"]}
        self.assertEqual(actions[2720], "tighter_trailing_trim")
        self.assertEqual(actions[4432], "bounded_tail_recovery")
        self.assertEqual({actions[cid] for cid in (658, 1575, 3989, 3036)}, {"start_trim"})

    def test_tail_recovery_never_extends_source_boundary(self) -> None:
        self.assertEqual(self.plan["tail_source_takeover_milliseconds"], [80, 140])

    def test_no_production_changes(self) -> None:
        self.assertFalse(self.plan["production_changes"])


if __name__ == "__main__":
    unittest.main()
