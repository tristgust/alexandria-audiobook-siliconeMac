from __future__ import annotations

from pathlib import Path
import runpy
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks/analyze_original_sin_overlap_character_repairs_round_v4.py"


class RepairRoundV4AnalyzerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.namespace = runpy.run_path(str(SCRIPT), run_name="repair_v4_analyzer_test")

    def test_three_winners_and_two_repair_modes_are_exact(self) -> None:
        self.assertEqual(len(self.namespace["WINNERS"]), 3)
        self.assertEqual(set(self.namespace["REPAIR_REQUIRED"]), {"doctor_urgent_discovery_repair", "dantalion_sharp_irritation"})

    def test_dantalion_dry_mode_is_completion_only(self) -> None:
        self.assertEqual(self.namespace["PENDING_COMPLETION"], {"dantalion_dry_sardonic": "0dff7471f2e22ead"})


if __name__ == "__main__":
    unittest.main()
