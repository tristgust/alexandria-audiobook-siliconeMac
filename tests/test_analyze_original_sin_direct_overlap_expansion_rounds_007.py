from __future__ import annotations

import json
import unittest
from pathlib import Path

from benchmarks.analyze_original_sin_direct_overlap_expansion_rounds_007 import analyze


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665/external_workflows/big_finish_overlap_reference_v1")


class ExpansionRound007AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = analyze(
            json.loads((WORKFLOW / "direct_overlap_boundary_repair_v7/private/answer-key.json").read_text()),
            json.loads((ROOT / "benchmarks/original_sin_direct_overlap_boundary_repair_v7_review.json").read_text()),
            json.loads((WORKFLOW / "direct_overlap_expansion_batch_007/private/answer-key.json").read_text()),
            json.loads((ROOT / "benchmarks/original_sin_direct_overlap_expansion_batch_007_review.json").read_text()),
        )

    def decisions(self, key: str) -> dict[int, dict]:
        return {row["chunk_id"]: row for row in self.report[key]["chunk_decisions"]}

    def test_boundary_round_closes_only_beltempest(self) -> None:
        rows = self.decisions("boundary_repair_round")
        self.assertEqual(rows[2584]["selected_candidate_id"], "5c8a9656d565159c")
        self.assertIsNone(rows[5055]["selected_candidate_id"])
        self.assertIsNone(rows[973]["selected_candidate_id"])

    def test_batch_seven_has_eight_clean_lines(self) -> None:
        rows = self.decisions("batch_007_round")
        approved = {cid for cid, row in rows.items() if row["outcome"] == "exact-line substitution eligible"}
        self.assertEqual(approved, {2080, 3431, 2144, 1618, 2398, 3451, 223, 5198})

    def test_written_notes_override_pass(self) -> None:
        rows = self.decisions("batch_007_round")
        for chunk_id in (2373, 3, 5462, 1731, 3116, 2231):
            self.assertIsNone(rows[chunk_id]["selected_candidate_id"])

    def test_wrong_speaker_matches_are_excluded(self) -> None:
        rows = self.decisions("batch_007_round")
        self.assertIn("wrong-speaker", rows[2175]["outcome"])
        self.assertIn("wrong-speaker", rows[426]["outcome"])

    def test_reference_bank_dispositions_are_explicit(self) -> None:
        rows = self.decisions("batch_007_round")
        self.assertIn("Doctor", rows[2398]["reference_bank_disposition"])
        self.assertIn("pending", rows[1731]["reference_bank_disposition"])

    def test_no_project_mutation(self) -> None:
        self.assertFalse(self.report["production_changes"])
        self.assertFalse(self.report["project_voice_config_changed"])
        self.assertFalse(self.report["project_chunks_changed"])


if __name__ == "__main__":
    unittest.main()
