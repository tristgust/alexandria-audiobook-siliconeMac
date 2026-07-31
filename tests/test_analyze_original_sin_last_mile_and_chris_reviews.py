from __future__ import annotations

import json
import unittest
from pathlib import Path

from benchmarks.analyze_original_sin_last_mile_and_chris_reviews import analyze_chris, analyze_direct


PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
ROOT = PROJECT / "external_workflows/big_finish_overlap_reference_v1"
REPO = Path(__file__).resolve().parents[1]


class LastMileAndChrisReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.direct_answer = json.loads((ROOT / "direct_substitution_last_mile_round_v6/private/answer-key.json").read_text())
        cls.direct_review = json.loads((REPO / "benchmarks/original_sin_direct_substitution_last_mile_review_v6.json").read_text())
        cls.chris_answer = json.loads((ROOT / "chris_urgent_performance_round_v3/private/answer-key.json").read_text())
        cls.chris_review = json.loads((REPO / "benchmarks/original_sin_chris_urgent_performance_review_v3.json").read_text())

    def test_zebulon_selects_mossformer_when_scores_tie(self):
        report = analyze_direct(self.direct_answer, self.direct_review)
        decision = next(row for row in report["chunk_decisions"] if row["chunk_id"] == 2954)
        self.assertEqual(decision["selected_candidate_id"], "c8e6b5cff71d56da")

    def test_powerless_remains_blocked(self):
        report = analyze_direct(self.direct_answer, self.direct_review)
        decision = next(row for row in report["chunk_decisions"] if row["chunk_id"] == 1317)
        self.assertIsNone(decision["selected_candidate_id"])

    def test_chris_echo_and_low_delivery_block_both(self):
        report = analyze_chris(self.chris_answer, self.chris_review)
        self.assertEqual(report["outcome"], "urgent authority remains unproven")
        self.assertTrue(all(not row["promotion_eligible"] for row in report["candidates"]))

    def test_missing_candidate_fails(self):
        broken = json.loads(json.dumps(self.direct_review))
        broken["results"].pop(next(iter(broken["results"])))
        with self.assertRaises(Exception):
            analyze_direct(self.direct_answer, broken)


if __name__ == "__main__":
    unittest.main()
