from __future__ import annotations

from pathlib import Path
import runpy
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "build_original_sin_overlap_character_coverage_ledger_v2.py"


class CoverageLedgerV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = SCRIPT.read_text(encoding="utf-8")
        cls.namespace = runpy.run_path(str(SCRIPT), run_name="coverage_ledger_v2_test")

    def test_mode_mapping_contains_all_twelve_selected_modes(self) -> None:
        self.assertEqual(len(self.namespace["MODE_TO_CHARACTER"]), 12)

    def test_summary_preserves_nineteen_character_roster(self) -> None:
        self.assertIn('"total": 19', self.text)
        self.assertIn('"covered_pending_speaker_remap": 2', self.text)
        self.assertIn('"pending_identity_completion_review": 1', self.text)

    def test_salvage_approval_does_not_mark_doc_complete(self) -> None:
        self.assertIn('doctor["coverage_status"] = "pending_generated_mode_review"', self.text)


if __name__ == "__main__":
    unittest.main()
