from __future__ import annotations

import json
import unittest
from pathlib import Path

from benchmarks.analyze_original_sin_direct_overlap_expansion_rounds_006 import analyze


ROOT = Path(__file__).resolve().parents[1]
PROJECT = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665/external_workflows/big_finish_overlap_reference_v1"
)


class ExpansionRound006AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = analyze(
            json.loads((PROJECT / "direct_overlap_boundary_repair_v6/private/answer-key.json").read_text()),
            json.loads((ROOT / "benchmarks/original_sin_direct_overlap_boundary_repair_v6_review.json").read_text()),
            json.loads((PROJECT / "direct_overlap_expansion_batch_006/private/answer-key.json").read_text()),
            json.loads((ROOT / "benchmarks/original_sin_direct_overlap_expansion_batch_006_review.json").read_text()),
        )

    def decisions(self, key: str) -> dict[int, dict]:
        return {
            row["chunk_id"]: row
            for row in self.report[key]["chunk_decisions"]
        }

    def test_boundary_round_closes_four_lines(self) -> None:
        decisions = self.decisions("boundary_repair_round")
        approved = {
            cid for cid, row in decisions.items()
            if row["outcome"] == "exact-line substitution eligible"
        }
        self.assertEqual(approved, {2979, 5336, 5353, 3090})
        self.assertEqual(decisions[2979]["selected_candidate_id"], "5f00c22860ea0571")
        self.assertEqual(decisions[5353]["selected_candidate_id"], "619b52e585c1ab95")
        self.assertEqual(decisions[3090]["selected_candidate_id"], "0cb6beadacaafc3c")

    def test_boundary_source_blocks_are_explicit(self) -> None:
        decisions = self.decisions("boundary_repair_round")
        self.assertIn("source blocked", decisions[2746]["outcome"])
        self.assertIn("quality blocked", decisions[5120]["outcome"])
        self.assertIn("source blocked", decisions[4675]["outcome"])

    def test_batch_six_has_six_clean_lines(self) -> None:
        decisions = self.decisions("batch_006_round")
        approved = {
            cid for cid, row in decisions.items()
            if row["outcome"] == "exact-line substitution eligible"
        }
        self.assertEqual(approved, {1401, 750, 3979, 561, 3189, 5431})

    def test_incomplete_isolation_score_fails_closed(self) -> None:
        decisions = self.decisions("batch_006_round")
        self.assertEqual(decisions[2584]["outcome"], "requires explicit isolation re-review")
        rows = [
            row for row in self.report["batch_006_round"]["candidates"]
            if row["chunk_id"] == 2584 and row["candidate_id"] == "2bb2ab05384e8584"
        ]
        self.assertEqual(rows[0]["missing_scores"], ["isolation"])
        self.assertFalse(rows[0]["promotion_eligible"])

    def test_identity_and_quality_blocks_are_preserved(self) -> None:
        decisions = self.decisions("batch_006_round")
        self.assertEqual(decisions[4580]["outcome"], "excluded speaker-identity mismatch")
        self.assertIn("quality blocked", decisions[3471]["outcome"])
        self.assertIn("trailing trim", decisions[5055]["outcome"])
        self.assertIn("trailing trim", decisions[973]["outcome"])

    def test_reference_bank_dispositions_are_explicit(self) -> None:
        decisions = self.decisions("batch_006_round")
        self.assertIn("Doctor", decisions[561]["reference_bank_disposition"])
        self.assertIn("direct placement only", decisions[3189]["reference_bank_disposition"])

    def test_no_project_mutation(self) -> None:
        self.assertFalse(self.report["production_changes"])
        self.assertFalse(self.report["project_voice_config_changed"])
        self.assertFalse(self.report["project_chunks_changed"])


if __name__ == "__main__":
    unittest.main()
