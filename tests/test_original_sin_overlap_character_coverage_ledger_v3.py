from __future__ import annotations

from pathlib import Path
import runpy
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks/build_original_sin_overlap_character_coverage_ledger_v3.py"


class CoverageLedgerV3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = SCRIPT.read_text(encoding="utf-8")
        cls.namespace = runpy.run_path(str(SCRIPT), run_name="ledger_v3_test")

    def test_only_three_characters_remain_pending(self) -> None:
        self.assertIn('"pending_generation_review": 3', self.text)
        self.assertIn('"total": 19', self.text)

    def test_homeless_is_covered_by_restricted_generated_transfer(self) -> None:
        self.assertIn('homeless_row["coverage_status"] = "covered_restricted"', self.text)
        self.assertIn("do not install the contaminated source audio", self.text)

    def test_shythe_is_identity_approved_not_complete(self) -> None:
        self.assertIn('shythe_row["identity_status"] = "approved_salvaged_identity"', self.text)
        self.assertIn('shythe_row["coverage_status"] = "pending_generated_mode_review"', self.text)


if __name__ == "__main__":
    unittest.main()
