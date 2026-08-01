from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks/build_original_sin_dantalion_mode_completion_round_v1.py"


class DantalionCompletionRoundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = SCRIPT.read_text(encoding="utf-8")

    def test_preserves_prior_pass_and_requests_all_scores(self) -> None:
        self.assertIn('CANDIDATE_ID = "0dff7471f2e22ead"', self.text)
        for field in ("identity", "delivery", "naturalness", "intelligibility", "effects"):
            self.assertIn(f'"{field}"', self.text)
        self.assertIn("decision:'pass'", self.text)


if __name__ == "__main__":
    unittest.main()
