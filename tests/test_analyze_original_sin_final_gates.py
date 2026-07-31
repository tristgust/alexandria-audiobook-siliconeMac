from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from benchmarks.analyze_original_sin_final_gates import (
    FinalGateError,
    analyze_chris,
    analyze_powerless,
)


PROJECT = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665"
)
ROOT = PROJECT / "external_workflows/big_finish_overlap_reference_v1"
REPO = Path(__file__).resolve().parents[1]


class FinalGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.powerless_answer = json.loads(
            (ROOT / "powerless_final_source_round_v7/private/answer-key.json").read_text()
        )
        cls.powerless_review = json.loads(
            (REPO / "benchmarks/original_sin_powerless_final_source_review_v7.json").read_text()
        )
        cls.chris_answer = json.loads(
            (ROOT / "chris_urgent_clean_identity_round_v4/private/answer-key.json").read_text()
        )
        cls.chris_review = json.loads(
            (REPO / "benchmarks/original_sin_chris_urgent_clean_identity_review_v4.json").read_text()
        )

    def test_powerless_is_eligible(self) -> None:
        result = analyze_powerless(self.powerless_answer, self.powerless_review)
        self.assertEqual(result["selected_candidate_id"], "eb1ade8e0036b484")
        self.assertEqual(result["chunk_id"], 1322)

    def test_powerless_note_blocks_promotion(self) -> None:
        review = copy.deepcopy(self.powerless_review)
        review["results"]["eb1ade8e0036b484"]["notes"] = "cut short"
        result = analyze_powerless(self.powerless_answer, review)
        self.assertIsNone(result["selected_candidate_id"])

    def test_chris_selects_qwen_normal(self) -> None:
        result = analyze_chris(self.chris_answer, self.chris_review)
        self.assertEqual(result["selected_candidate_id"], "808d3494896ed395")
        self.assertEqual(result["selected_route_key"], "qwen_clean_identity")

    def test_delivery_three_is_not_urgent_authority(self) -> None:
        result = analyze_chris(self.chris_answer, self.chris_review)
        fish = next(row for row in result["candidates"] if row["candidate_id"] == "575df49c937afacb")
        self.assertFalse(fish["promotion_eligible"])

    def test_missing_candidate_fails_closed(self) -> None:
        review = copy.deepcopy(self.chris_review)
        review["results"].pop("808d3494896ed395")
        with self.assertRaises(FinalGateError):
            analyze_chris(self.chris_answer, review)


if __name__ == "__main__":
    unittest.main()
