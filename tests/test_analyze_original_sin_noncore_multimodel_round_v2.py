from __future__ import annotations

import runpy
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks/analyze_original_sin_noncore_multimodel_round_v2.py"


class MultimodelV2DecisionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = SCRIPT.read_text(encoding="utf-8")
        cls.namespace = runpy.run_path(str(SCRIPT), run_name="decision_contract")

    def test_fourteen_modes_are_selected_and_two_are_restricted(self) -> None:
        self.assertEqual(len(self.namespace["WINNERS"]), 14)
        self.assertEqual(
            self.namespace["RESTRICTED_WINNERS"],
            {
                "powerless_panicked_urgency",
                "under_sergeant_military_menace",
            },
        )

    def test_missing_completeness_is_not_selected(self) -> None:
        self.assertEqual(
            self.namespace["WINNERS"]["zebulon_intense_questioning"],
            "be97083cd4387e62",
        )
        self.assertNotIn("692f89a1d523fd3b", self.namespace["WINNERS"].values())

    def test_fail_closed_review_fields_are_required(self) -> None:
        for marker in (
            'result["all_scores_present"]',
            'result["complete"]',
            'result["operator_pass"]',
            '"written_notes_override_pass": True',
        ):
            self.assertIn(marker, self.text)


if __name__ == "__main__":
    unittest.main()
