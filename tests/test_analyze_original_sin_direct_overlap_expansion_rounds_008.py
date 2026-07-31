from __future__ import annotations

import json
import unittest
from pathlib import Path

from benchmarks.analyze_original_sin_direct_overlap_expansion_rounds_008 import analyze


ROOT = Path(__file__).resolve().parents[1]
PROJECT = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665/external_workflows/big_finish_overlap_reference_v1"
)


class ExpansionRound008AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = analyze(
            json.loads((PROJECT / "direct_overlap_boundary_repair_v8/private/answer-key.json").read_text()),
            json.loads((ROOT / "benchmarks/original_sin_direct_overlap_boundary_repair_v8_review.json").read_text()),
            json.loads((PROJECT / "direct_overlap_expansion_batch_008/private/answer-key.json").read_text()),
            json.loads((ROOT / "benchmarks/original_sin_direct_overlap_expansion_batch_008_review.json").read_text()),
        )

    def test_six_clean_lines_close(self) -> None:
        decisions = self.report["boundary_repair_round"]["chunk_decisions"] + self.report["batch_008_round"]["chunk_decisions"]
        clean = [row for row in decisions if row["outcome"] == "exact-line substitution eligible"]
        self.assertEqual({row["chunk_id"] for row in clean}, {2373, 5462, 1731, 1210, 1939, 2394})

    def test_written_notes_override_nominal_pass(self) -> None:
        decisions = {row["chunk_id"]: row for row in self.report["batch_008_round"]["chunk_decisions"]}
        self.assertIn("repair", decisions[4715]["outcome"])
        self.assertIn("repair", decisions[4888]["outcome"])
        self.assertIn("enhancement", decisions[4907]["outcome"])

    def test_wrong_speaker_is_excluded(self) -> None:
        decisions = {row["chunk_id"]: row for row in self.report["batch_008_round"]["chunk_decisions"]}
        self.assertEqual(decisions[2840]["outcome"], "excluded wrong-speaker textual match")

    def test_reference_bank_dispositions_are_explicit(self) -> None:
        boundary = {row["chunk_id"]: row for row in self.report["boundary_repair_round"]["chunk_decisions"]}
        batch = {row["chunk_id"]: row for row in self.report["batch_008_round"]["chunk_decisions"]}
        self.assertIn("Doctor", boundary[5462]["reference_bank_disposition"])
        self.assertIn("rolled-R", boundary[1731]["reference_bank_disposition"])
        self.assertIn("Bernice", batch[1939]["reference_bank_disposition"])

    def test_no_project_mutation(self) -> None:
        self.assertFalse(self.report["production_changes"])
        self.assertFalse(self.report["project_voice_config_changed"])
        self.assertFalse(self.report["project_chunks_changed"])


if __name__ == "__main__":
    unittest.main()
