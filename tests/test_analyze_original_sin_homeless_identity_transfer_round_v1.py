from __future__ import annotations

from pathlib import Path
import runpy
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks/analyze_original_sin_homeless_identity_transfer_round_v1.py"


class HomelessTransferAnalyzerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.namespace = runpy.run_path(str(SCRIPT), run_name="homeless_transfer_analyzer_test")

    def test_winner_and_alternate_use_adaptation_identity(self) -> None:
        self.assertEqual(self.namespace["WINNER"], "e883b934a1bdb7f3")
        self.assertEqual(self.namespace["ALTERNATE"], "dbb22db6e3fb92d3")


if __name__ == "__main__":
    unittest.main()
