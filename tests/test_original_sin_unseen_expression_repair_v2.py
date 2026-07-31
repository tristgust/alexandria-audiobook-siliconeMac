from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "benchmarks/original_sin_unseen_expression_repair_plan_v2.json"


class UnseenExpressionRepairPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = json.loads(PLAN.read_text())

    def test_round_targets_only_failed_modes(self) -> None:
        self.assertEqual(
            {group["group"] for group in self.plan["groups"]},
            {
                "bernice_urgent_concern_repair",
                "bernice_dry_irony_repair",
                "chris_urgent_authority_repair",
                "roz_command_authority_repair",
            },
        )

    def test_candidate_count_matches(self) -> None:
        self.assertEqual(
            sum(len(group["routes"]) for group in self.plan["groups"]),
            17,
        )
        self.assertEqual(self.plan["candidate_count"], 17)

    def test_every_group_uses_longer_current_identity(self) -> None:
        allowed = {"qwen_identity", "current_route", "vox_identity", "fish_identity"}
        for group in self.plan["groups"]:
            self.assertTrue({route["kind"] for route in group["routes"]}.issubset(allowed))
            self.assertIn("qwen_identity", {route["kind"] for route in group["routes"]})
            self.assertIn("vox_identity", {route["kind"] for route in group["routes"]})
            self.assertIn("fish_identity", {route["kind"] for route in group["routes"]})

    def test_no_production_change(self) -> None:
        self.assertFalse(self.plan["production_changes"])


if __name__ == "__main__":
    unittest.main()
