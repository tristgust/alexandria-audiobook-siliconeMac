from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = json.loads((ROOT / "benchmarks/original_sin_direct_overlap_boundary_repair_plan_v8.json").read_text())


class BoundaryRepairV8PlanTests(unittest.TestCase):
    def test_eight_final_bounded_groups(self) -> None:
        self.assertEqual([g["chunk_id"] for g in PLAN["groups"]], [5055, 973, 2373, 3, 5462, 1731, 3116, 2231])

    def test_actions_are_explicit(self) -> None:
        self.assertEqual({g["action"] for g in PLAN["groups"]}, {"clarity_tail_trim", "in_boundary_tail_recovery", "start_trim", "clarity_enhance", "trailing_trim", "start_trim_variants", "start_tail_recovery"})

    def test_tail_recovery_is_bounded(self) -> None:
        self.assertEqual(PLAN["tail_recovery_milliseconds"], [80, 140])
        self.assertEqual(PLAN["clarity_tail_postroll_seconds"], [0.04, 0.06, 0.08])

    def test_clarity_profiles_are_mild(self) -> None:
        self.assertLessEqual(max(v["presence_gain_db"] for v in PLAN["clarity_profiles"].values()), 3.5)

    def test_no_production_changes(self) -> None:
        self.assertFalse(PLAN["production_changes"])


if __name__ == "__main__":
    unittest.main()
