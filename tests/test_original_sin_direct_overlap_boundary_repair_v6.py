from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = json.loads((ROOT / "benchmarks/original_sin_direct_overlap_boundary_repair_plan_v6.json").read_text())


class BoundaryRepairV6PlanTests(unittest.TestCase):
    def test_seven_bounded_repairs(self) -> None:
        self.assertEqual(len(PLAN["groups"]), 7)
        self.assertEqual(len({row["chunk_id"] for row in PLAN["groups"]}), 7)

    def test_actions_are_explicit(self) -> None:
        actions = {row["action"] for row in PLAN["groups"]}
        self.assertEqual(actions, {"start_trim", "micro_tail_extension", "start_micro_tail_extension", "start_end_cleanup", "start_tail_recovery"})

    def test_extensions_remain_bounded(self) -> None:
        self.assertLessEqual(max(PLAN["micro_tail_extension_milliseconds"]), 160)
        self.assertLessEqual(max(PLAN["tail_recovery_milliseconds"]), 140)

    def test_source_blocked_lines_are_not_retried(self) -> None:
        selected = {row["chunk_id"] for row in PLAN["groups"]}
        self.assertTrue(selected.isdisjoint(PLAN["source_blocked_chunks_not_retried"]))

    def test_no_production_changes(self) -> None:
        self.assertFalse(PLAN["production_changes"])


if __name__ == "__main__":
    unittest.main()
