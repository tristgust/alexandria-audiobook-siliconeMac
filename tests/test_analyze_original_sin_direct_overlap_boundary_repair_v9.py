from __future__ import annotations

import json
import unittest
from pathlib import Path

from benchmarks.analyze_original_sin_direct_overlap_boundary_repair_v9 import analyze


ROOT = Path(__file__).resolve().parents[1]
PROJECT = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665"
)
ANSWER = PROJECT / (
    "external_workflows/big_finish_overlap_reference_v1/"
    "direct_overlap_boundary_repair_v9/private/answer-key.json"
)
REVIEW = ROOT / "benchmarks/original_sin_direct_overlap_boundary_repair_v9_review.json"


class TerminalRepairV9AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = analyze(
            json.loads(ANSWER.read_text(encoding="utf-8")),
            json.loads(REVIEW.read_text(encoding="utf-8")),
        )
        cls.decisions = {
            row["chunk_id"]: row for row in cls.result["chunk_decisions"]
        }

    def test_three_strict_clean_lines(self) -> None:
        self.assertEqual(self.result["strict_clean_approved_count"], 3)
        self.assertEqual(
            {
                chunk_id
                for chunk_id, row in self.decisions.items()
                if row["direct_placement_tier"] == "strict_clean"
            },
            {365, 4071, 4443},
        )

    def test_three_restricted_direct_only_lines(self) -> None:
        self.assertEqual(self.result["restricted_direct_approved_count"], 3)
        self.assertEqual(
            {
                chunk_id
                for chunk_id, row in self.decisions.items()
                if row["direct_placement_tier"] == "restricted_user_accepted_artifacts"
            },
            {1801, 3025, 4907},
        )

    def test_five_terminal_rejections(self) -> None:
        self.assertEqual(self.result["terminal_rejected_count"], 5)
        self.assertEqual(
            {
                chunk_id
                for chunk_id, row in self.decisions.items()
                if row["direct_placement_tier"] == "rejected_terminal"
            },
            {5055, 3116, 3016, 4715, 4888},
        )

    def test_reference_bank_decisions_are_separate(self) -> None:
        self.assertEqual(
            self.decisions[4443]["reference_bank_disposition"]["status"],
            "approved",
        )
        for chunk_id in (1801, 3025, 4907):
            self.assertEqual(
                self.decisions[chunk_id]["reference_bank_disposition"]["status"],
                "excluded",
            )
        self.assertEqual(
            self.decisions[4888]["reference_bank_disposition"]["status"],
            "rejected_terminal",
        )

    def test_blocking_notes_override_pass_buttons(self) -> None:
        by_chunk: dict[int, list[dict]] = {}
        for row in self.result["candidates"]:
            by_chunk.setdefault(row["chunk_id"], []).append(row)
        self.assertTrue(any(row["human_decision"] == "pass" for row in by_chunk[5055]))
        self.assertEqual(self.decisions[5055]["direct_placement_tier"], "rejected_terminal")
        self.assertTrue(any(row["human_decision"] == "pass" for row in by_chunk[4888]))
        self.assertEqual(self.decisions[4888]["direct_placement_tier"], "rejected_terminal")

    def test_no_project_mutation(self) -> None:
        self.assertFalse(self.result["production_changes"])
        self.assertFalse(self.result["project_voice_config_changed"])
        self.assertFalse(self.result["project_chunks_changed"])


if __name__ == "__main__":
    unittest.main()
