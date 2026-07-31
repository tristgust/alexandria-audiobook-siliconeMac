from __future__ import annotations

import json
import unittest
from pathlib import Path

from benchmarks.analyze_original_sin_direct_overlap_expansion_rounds_005 import analyze


ROOT = Path(__file__).resolve().parents[1]
PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
WORKFLOW = PROJECT / "external_workflows/big_finish_overlap_reference_v1"


class ExpansionRound005AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        def load(path: Path):
            return json.loads(path.read_text(encoding="utf-8"))
        cls.report = analyze(
            load(WORKFLOW / "direct_overlap_boundary_repair_v5/private/answer-key.json"),
            load(ROOT / "benchmarks/original_sin_direct_overlap_boundary_repair_v5_review.json"),
            load(WORKFLOW / "direct_overlap_expansion_batch_005/private/answer-key.json"),
            load(ROOT / "benchmarks/original_sin_direct_overlap_expansion_batch_005_review.json"),
        )

    def test_boundary_round_closes_two_lines(self) -> None:
        rows = self.report["boundary_repair_round"]["chunk_decisions"]
        clean = {row["chunk_id"] for row in rows if row["outcome"] == "exact-line substitution eligible"}
        self.assertEqual(clean, {2047, 506})

    def test_boundary_source_blocks_are_explicit(self) -> None:
        rows = {row["chunk_id"]: row for row in self.report["boundary_repair_round"]["chunk_decisions"]}
        self.assertIn("effects", rows[2555]["outcome"])
        self.assertIn("source blocked", rows[4758]["outcome"])
        self.assertIn("combined", rows[2979]["outcome"])

    def test_batch_five_has_four_clean_lines(self) -> None:
        rows = self.report["batch_005_round"]["chunk_decisions"]
        clean = {row["chunk_id"] for row in rows if row["outcome"] == "exact-line substitution eligible"}
        self.assertEqual(clean, {2159, 2154, 4756, 3635})

    def test_batch_repair_outcomes_are_explicit(self) -> None:
        rows = {row["chunk_id"]: row for row in self.report["batch_005_round"]["chunk_decisions"]}
        self.assertIn("tail repair", rows[2746]["outcome"])
        self.assertIn("start trim", rows[5336]["outcome"])
        self.assertIn("start and end", rows[5120]["outcome"])
        self.assertIn("muffling", rows[4852]["outcome"])

    def test_wrong_speaker_matches_are_excluded(self) -> None:
        rows = {row["chunk_id"]: row for row in self.report["batch_005_round"]["chunk_decisions"]}
        self.assertEqual(rows[2169]["outcome"], "excluded wrong-speaker textual match")
        self.assertEqual(rows[2993]["outcome"], "excluded wrong-speaker textual match")

    def test_written_notes_override_nominal_pass(self) -> None:
        candidates = self.report["batch_005_round"]["candidates"]
        row = next(item for item in candidates if item["candidate_id"] == "60b0ee7f75bce40d")
        self.assertFalse(row["promotion_eligible"])
        self.assertIn("boundary_incomplete", row["note_flags"])

    def test_no_project_mutation(self) -> None:
        self.assertFalse(self.report["production_changes"])
        self.assertFalse(self.report["project_voice_config_changed"])
        self.assertFalse(self.report["project_chunks_changed"])


if __name__ == "__main__":
    unittest.main()
