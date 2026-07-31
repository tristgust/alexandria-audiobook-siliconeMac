from __future__ import annotations

import json
import unittest
from pathlib import Path

from benchmarks.analyze_original_sin_direct_overlap_expansion_rounds_003 import analyze


ROOT = Path(__file__).resolve().parents[1]
PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
WORKFLOW = PROJECT / "external_workflows/big_finish_overlap_reference_v1"


class ExpansionRound003AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = analyze(
            json.loads((WORKFLOW / "direct_overlap_boundary_repair_v3/private/answer-key.json").read_text()),
            json.loads((ROOT / "benchmarks/original_sin_direct_overlap_boundary_repair_v3_review.json").read_text()),
            json.loads((WORKFLOW / "direct_overlap_expansion_batch_003/private/answer-key.json").read_text()),
            json.loads((ROOT / "benchmarks/original_sin_direct_overlap_expansion_batch_003_review.json").read_text()),
        )

    def test_boundary_round_closes_four_lines(self) -> None:
        decisions = self.report["boundary_repair_round"]["chunk_decisions"]
        clean = {row["chunk_id"] for row in decisions if row["outcome"] == "exact-line substitution eligible"}
        self.assertEqual(clean, {5351, 218, 5371, 2919})

    def test_failed_tail_splices_are_source_blocked(self) -> None:
        decisions = {row["chunk_id"]: row for row in self.report["boundary_repair_round"]["chunk_decisions"]}
        self.assertIn("source blocked", decisions[2745]["outcome"])
        self.assertIn("source blocked", decisions[1320]["outcome"])

    def test_batch_three_has_eight_clean_lines(self) -> None:
        decisions = self.report["batch_003_round"]["chunk_decisions"]
        clean = {row["chunk_id"] for row in decisions if row["outcome"] == "exact-line substitution eligible"}
        self.assertEqual(clean, {5014, 2089, 1985, 4735, 615, 4880, 4698, 3293})

    def test_wrong_speaker_and_reference_only_are_preserved(self) -> None:
        decisions = {row["chunk_id"]: row for row in self.report["batch_003_round"]["chunk_decisions"]}
        self.assertEqual(decisions[1098]["outcome"], "excluded wrong-speaker textual match")
        self.assertEqual(decisions[3209]["outcome"], "reference-bank evidence only")

    def test_written_pass_notes_block_direct_promotion(self) -> None:
        decisions = {row["chunk_id"]: row for row in self.report["batch_003_round"]["chunk_decisions"]}
        self.assertIn("trim", decisions[658]["outcome"])
        self.assertIn("trim", decisions[1575]["outcome"])
        self.assertIn("trim", decisions[3036]["outcome"])

    def test_no_project_mutation(self) -> None:
        self.assertFalse(self.report["production_changes"])
        self.assertFalse(self.report["project_voice_config_changed"])
        self.assertFalse(self.report["project_chunks_changed"])


if __name__ == "__main__":
    unittest.main()
