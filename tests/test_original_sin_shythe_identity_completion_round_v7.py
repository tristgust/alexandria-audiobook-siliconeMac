from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks/build_original_sin_shythe_identity_completion_round_v7.py"


class ShytheCompletionRoundV7Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = SCRIPT.read_text(encoding="utf-8")

    def test_round_preserves_prior_pass_and_requests_four_scores(self) -> None:
        self.assertIn('CANDIDATE_ID = "5ad130953556d32b"', self.text)
        for field in ("cleanliness", "naturalness", "intelligibility", "contamination"):
            self.assertIn(f'"{field}"', self.text)
        self.assertIn("identity:'5'", self.text)
        self.assertIn("decision:'pass'", self.text)

    def test_round_is_non_installing(self) -> None:
        self.assertIn('"production_changes": False', self.text)


if __name__ == "__main__":
    unittest.main()
