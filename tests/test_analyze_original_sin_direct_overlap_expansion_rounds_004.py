from __future__ import annotations

import json
import unittest
from pathlib import Path

from benchmarks.analyze_original_sin_direct_overlap_expansion_rounds_004 import analyze


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665/external_workflows/big_finish_overlap_reference_v1")


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class ExpansionRound004AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = analyze(
            read(WORKFLOW / "direct_overlap_boundary_repair_v4/private/answer-key.json"),
            read(ROOT / "benchmarks/original_sin_direct_overlap_boundary_repair_v4_review.json"),
            read(WORKFLOW / "direct_overlap_expansion_batch_004/private/answer-key.json"),
            read(ROOT / "benchmarks/original_sin_direct_overlap_expansion_batch_004_review.json"),
        )

    def test_boundary_round_closes_all_six_lines(self):
        decisions = self.report["boundary_repair_round"]["chunk_decisions"]
        self.assertEqual(len(decisions), 6)
        self.assertTrue(all(row["outcome"] == "exact-line substitution eligible" for row in decisions))

    def test_batch_four_has_seven_clean_lines(self):
        decisions = self.report["batch_004_round"]["chunk_decisions"]
        clean = [row["chunk_id"] for row in decisions if row["outcome"] == "exact-line substitution eligible"]
        self.assertEqual(clean, [2716, 2737, 66, 1995, 1259, 4866, 636])

    def test_wrong_speaker_is_excluded(self):
        decisions = {row["chunk_id"]: row for row in self.report["batch_004_round"]["chunk_decisions"]}
        self.assertEqual(decisions[1676]["outcome"], "excluded wrong-speaker textual match")

    def test_written_pass_notes_block_promotion(self):
        candidates = self.report["batch_004_round"]["candidates"]
        by_id = {row["candidate_id"]: row for row in candidates}
        self.assertFalse(by_id["1f7d01a3bc48aeb0"]["promotion_eligible"])
        self.assertFalse(by_id["80da1f5e81f73447"]["promotion_eligible"])
        self.assertFalse(by_id["f82c495fab596719"]["promotion_eligible"])

    def test_repair_and_contamination_outcomes_are_explicit(self):
        decisions = {row["chunk_id"]: row for row in self.report["batch_004_round"]["chunk_decisions"]}
        self.assertEqual(decisions[2047]["outcome"], "requires first-word start trim")
        self.assertEqual(decisions[2979]["outcome"], "requires final-word tail repair")
        self.assertEqual(decisions[4780]["outcome"], "requires final-word and post-line sound repair")
        self.assertEqual(decisions[5018]["outcome"], "requires contamination/source repair")

    def test_no_project_mutation(self):
        self.assertFalse(self.report["production_changes"])
        self.assertFalse(self.report["project_voice_config_changed"])
        self.assertFalse(self.report["project_chunks_changed"])


if __name__ == "__main__":
    unittest.main()
