from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = json.loads((ROOT / "benchmarks/original_sin_direct_overlap_boundary_repair_plan_v5.json").read_text())


class BoundaryRepairV5PlanTests(unittest.TestCase):
    def test_five_bounded_repairs(self):
        self.assertEqual([row["chunk_id"] for row in PLAN["groups"]], [2047, 506, 2979, 2555, 4758])

    def test_actions_are_explicit(self):
        actions = {row["chunk_id"]: row["action"] for row in PLAN["groups"]}
        self.assertEqual(actions[2047], "start_trim")
        self.assertEqual(actions[506], "start_trim")
        self.assertEqual(actions[2979], "micro_tail_extension")
        self.assertEqual(actions[2555], "micro_tail_extension")
        self.assertEqual(actions[4758], "micro_tail_extension")

    def test_micro_extensions_are_small(self):
        self.assertEqual(PLAN["tail_extension_milliseconds"], [40, 80, 120])
        self.assertLessEqual(max(PLAN["tail_extension_milliseconds"]), 120)

    def test_source_blocked_chunk_is_not_retried(self):
        self.assertNotIn(4780, [row["chunk_id"] for row in PLAN["groups"]])

    def test_no_production_changes(self):
        self.assertFalse(PLAN["production_changes"])


if __name__ == "__main__":
    unittest.main()
