from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = json.loads((ROOT / "benchmarks/original_sin_direct_overlap_boundary_repair_plan_v9.json").read_text())


class BoundaryRepairV9PlanTests(unittest.TestCase):
    def test_final_eleven_bounded_groups(self) -> None:
        self.assertTrue(PLAN["final_bounded_round"])
        self.assertEqual(len(PLAN["groups"]), 11)
        self.assertEqual(len({row["chunk_id"] for row in PLAN["groups"]}), 11)

    def test_operations_are_new_and_explicit(self) -> None:
        actions = {row["action"] for row in PLAN["groups"]}
        self.assertEqual(actions, {"terminal_trim", "start_trim", "start_trim_clarity", "clarity", "tail_recovery", "clarity_tail_recovery"})

    def test_tail_recovery_is_bounded(self) -> None:
        self.assertEqual(PLAN["tail_recovery_milliseconds"], [80, 140, 200])
        self.assertLessEqual(max(PLAN["tail_recovery_milliseconds"]), 200)

    def test_clarity_profiles_are_mild(self) -> None:
        self.assertLessEqual(max(profile["presence_gain_db"] for profile in PLAN["clarity_profiles"].values()), 2.5)

    def test_no_production_changes(self) -> None:
        self.assertFalse(PLAN["production_changes"])


if __name__ == "__main__":
    unittest.main()
