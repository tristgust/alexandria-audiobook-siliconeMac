from __future__ import annotations

import json
from pathlib import Path
import runpy
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "analyze_original_sin_overlap_character_coverage_round_v3.py"


class CoverageRoundV3AnalyzerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.namespace = runpy.run_path(str(SCRIPT), run_name="coverage_v3_analyzer_test")

    def test_winner_and_repair_sets_are_exact(self) -> None:
        self.assertEqual(len(self.namespace["WINNERS"]), 12)
        self.assertEqual(
            set(self.namespace["REPAIR_REQUIRED"]),
            {
                "doctor_urgent_discovery",
                "doctor_weary_moral_gravity",
                "roz_dry_banter",
                "computer_formal_timestamp",
            },
        )

    def test_restricted_winners_capture_three_score_passes(self) -> None:
        self.assertEqual(
            self.namespace["RESTRICTED_WINNERS"],
            {
                "roz_survivor_reflection",
                "hater_grave_statecraft",
                "securitybot_identity_repair",
            },
        )

    def test_computer_nominal_passes_are_not_winners(self) -> None:
        winners = set(self.namespace["WINNERS"].values())
        self.assertNotIn("da6c367d964ea6c9", winners)
        self.assertNotIn("56da202533b9f6d6", winners)


if __name__ == "__main__":
    unittest.main()
