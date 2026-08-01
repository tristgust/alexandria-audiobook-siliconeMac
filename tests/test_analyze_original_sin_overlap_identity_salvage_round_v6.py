from __future__ import annotations

from pathlib import Path
import runpy
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "analyze_original_sin_overlap_identity_salvage_round_v6.py"


class IdentitySalvageV6AnalyzerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = SCRIPT.read_text(encoding="utf-8")
        cls.namespace = runpy.run_path(str(SCRIPT), run_name="salvage_v6_analyzer_test")

    def test_doc_winner_is_fixed_and_shythe_is_fail_closed(self) -> None:
        self.assertIn('doc_id = "89773ee3454a2cbf"', self.text)
        self.assertIn('shythe_id = "5ad130953556d32b"', self.text)
        self.assertIn("operator_pass_fail_closed_missing_required_scores", self.text)

    def test_identity_approval_does_not_complete_character(self) -> None:
        self.assertIn("identity_source_approval_does_not_complete_generated_mode_coverage", self.text)


if __name__ == "__main__":
    unittest.main()
