from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = json.loads(
    (ROOT / "benchmarks/original_sin_direct_overlap_boundary_repair_plan_v7.json").read_text()
)


class BoundaryRepairV7PlanTests(unittest.TestCase):
    def test_three_bounded_groups(self) -> None:
        self.assertEqual([row["chunk_id"] for row in PLAN["groups"]], [2584, 5055, 973])

    def test_actions_are_explicit(self) -> None:
        actions = {row["chunk_id"]: row["action"] for row in PLAN["groups"]}
        self.assertEqual(actions[2584], "isolation_recheck")
        self.assertEqual(actions[5055], "trailing_trim")
        self.assertEqual(actions[973], "trailing_trim")

    def test_only_safe_postrolls_are_used(self) -> None:
        self.assertEqual(PLAN["trailing_postroll_seconds"], [0.0, 0.02])

    def test_no_production_changes(self) -> None:
        self.assertFalse(PLAN["production_changes"])


if __name__ == "__main__":
    unittest.main()
