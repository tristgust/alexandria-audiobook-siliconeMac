from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks/analyze_original_sin_shythe_identity_completion_round_v7.py"


class ShytheCompletionAnalyzerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = SCRIPT.read_text(encoding="utf-8")

    def test_contamination_scale_is_low_is_good(self) -> None:
        self.assertIn("1_is_none_or_best_5_is_most_or_worst", self.text)
        self.assertIn('scores["contamination"] > 2', self.text)


if __name__ == "__main__":
    unittest.main()
