from __future__ import annotations

import json
import unittest
from pathlib import Path

from benchmarks.analyze_original_sin_direct_overlap_expansion_followup_reviews import (
    OUTCOME_DIRECT,
    OUTCOME_REFERENCE,
    OUTCOME_START,
    OUTCOME_START_TAIL,
    OUTCOME_TAIL,
    OUTCOME_TRAILING,
    OUTCOME_WRONG_SPEAKER,
    analyze_round,
    TIMING_ROUND_ID,
    BATCH2_ROUND_ID,
    TIMING_CHUNKS,
    BATCH2_CHUNKS,
)


ROOT = Path(__file__).resolve().parents[1]
PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
WORKFLOW = PROJECT / "external_workflows/big_finish_overlap_reference_v1"


class FollowupReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.timing = analyze_round(
            answer_key=json.loads((WORKFLOW / "direct_overlap_expansion_batch_001_timing_repair_v2/private/answer-key.json").read_text()),
            review=json.loads((ROOT / "benchmarks/original_sin_direct_overlap_expansion_batch_001_timing_repair_v2_review.json").read_text()),
            round_id=TIMING_ROUND_ID,
            expected_chunks=TIMING_CHUNKS,
        )
        cls.batch2 = analyze_round(
            answer_key=json.loads((WORKFLOW / "direct_overlap_expansion_batch_002/private/answer-key.json").read_text()),
            review=json.loads((ROOT / "benchmarks/original_sin_direct_overlap_expansion_batch_002_review.json").read_text()),
            round_id=BATCH2_ROUND_ID,
            expected_chunks=BATCH2_CHUNKS,
        )

    def test_timing_round_has_seven_clean_chunks(self) -> None:
        clean = [row for row in self.timing["chunk_decisions"] if row["outcome"] == OUTCOME_DIRECT]
        self.assertEqual({row["chunk_id"] for row in clean}, {696, 1261, 2741, 90, 2090, 4764, 3285})

    def test_timing_repairs_remain_bounded(self) -> None:
        outcomes = {row["chunk_id"]: row["outcome"] for row in self.timing["chunk_decisions"]}
        self.assertEqual(outcomes[5351], OUTCOME_START)
        self.assertEqual(outcomes[2745], OUTCOME_START_TAIL)

    def test_batch2_has_seven_direct_clean_chunks(self) -> None:
        clean = [row for row in self.batch2["chunk_decisions"] if row["outcome"] == OUTCOME_DIRECT]
        self.assertEqual({row["chunk_id"] for row in clean}, {1590, 5375, 5037, 3080, 11, 5020, 2002})

    def test_computer_long_line_is_reference_only(self) -> None:
        row = next(row for row in self.batch2["chunk_decisions"] if row["chunk_id"] == 1247)
        self.assertEqual(row["outcome"], OUTCOME_REFERENCE)

    def test_batch2_repairs_and_wrong_speaker(self) -> None:
        outcomes = {row["chunk_id"]: row["outcome"] for row in self.batch2["chunk_decisions"]}
        self.assertEqual(outcomes[218], OUTCOME_START)
        self.assertEqual(outcomes[5371], OUTCOME_START)
        self.assertEqual(outcomes[2919], OUTCOME_START)
        self.assertEqual(outcomes[1320], OUTCOME_TAIL)
        self.assertEqual(outcomes[2720], OUTCOME_TRAILING)
        self.assertEqual(outcomes[3273], OUTCOME_WRONG_SPEAKER)

    def test_written_notes_override_pass(self) -> None:
        row = next(row for row in self.batch2["candidates"] if row["candidate_id"] == "a9e13fe61944199a")
        self.assertFalse(row["direct_promotion_eligible"])


if __name__ == "__main__":
    unittest.main()
