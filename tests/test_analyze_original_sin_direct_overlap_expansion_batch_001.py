from __future__ import annotations

import json
import unittest
from pathlib import Path

from benchmarks.analyze_original_sin_direct_overlap_expansion_batch_001 import (
    OUTCOME_BOUNDARY,
    OUTCOME_CONTAMINATION,
    OUTCOME_DIRECT,
    Batch001ReviewError,
    analyze,
)


ROOT = Path(__file__).resolve().parents[1]
PROJECT = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665"
)
ANSWER = PROJECT / (
    "external_workflows/big_finish_overlap_reference_v1/"
    "direct_overlap_expansion_batch_001/private/answer-key.json"
)
REVIEW = ROOT / "benchmarks/original_sin_direct_overlap_expansion_batch_001_review.json"


class Batch001AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.answer = json.loads(ANSWER.read_text(encoding="utf-8"))
        cls.review = json.loads(REVIEW.read_text(encoding="utf-8"))
        cls.report = analyze(cls.answer, cls.review)

    def test_only_two_chunks_are_clean(self) -> None:
        clean = {
            row["chunk_id"]: row["selected_candidate_id"]
            for row in self.report["chunk_decisions"]
            if row["outcome"] == OUTCOME_DIRECT
        }
        self.assertEqual(
            clean,
            {
                1586: "318cee8e2969a50c",
                2070: "d65638d215480423",
            },
        )

    def test_written_cut_note_blocks_perfect_numeric_scores(self) -> None:
        row = next(
            item
            for item in self.report["candidates"]
            if item["candidate_id"] == "3516fce420217627"
        )
        self.assertFalse(row["promotion_eligible"])
        self.assertIn("boundary_incomplete", row["note_flags"])

    def test_timing_repairs_are_identified(self) -> None:
        boundary = {
            row["chunk_id"]
            for row in self.report["chunk_decisions"]
            if row["outcome"] == OUTCOME_BOUNDARY
        }
        self.assertEqual(
            boundary,
            {5351, 696, 1261, 2741, 2745, 90, 2090, 4764, 3285},
        )

    def test_contamination_repairs_are_separate(self) -> None:
        contaminated = {
            row["chunk_id"]
            for row in self.report["chunk_decisions"]
            if row["outcome"] == OUTCOME_CONTAMINATION
        }
        self.assertEqual(contaminated, {2718, 12, 1318})

    def test_missing_candidate_fails_closed(self) -> None:
        review = json.loads(json.dumps(self.review))
        review["results"].pop(next(iter(review["results"])))
        with self.assertRaises(Batch001ReviewError):
            analyze(self.answer, review)

    def test_analysis_is_no_mutation(self) -> None:
        self.assertFalse(self.report["production_changes"])
        self.assertFalse(self.report["project_voice_config_changed"])
        self.assertFalse(self.report["project_chunks_changed"])


if __name__ == "__main__":
    unittest.main()
