from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "benchmarks/original_sin_direct_substitution_alternate_source_plan_v5.json"


class AlternateSourcePlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = json.loads(PLAN.read_text())

    def test_round_replaces_all_three_contaminated_lines(self) -> None:
        self.assertEqual(
            {group["chunk_id"] for group in self.plan["groups"]},
            {1317, 4366, 3829},
        )

    def test_candidate_count_matches(self) -> None:
        self.assertEqual(
            sum(len(group["treatments"]) for group in self.plan["groups"]),
            9,
        )
        self.assertEqual(self.plan["candidate_count"], 9)

    def test_no_production_change(self) -> None:
        self.assertFalse(self.plan["production_changes"])


if __name__ == "__main__":
    unittest.main()
