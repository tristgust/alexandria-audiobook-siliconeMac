from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from benchmarks.analyze_original_sin_boundary_and_expression_reviews import (
    OUTCOME_ANCHOR,
    OUTCOME_DIRECT_REPAIR,
    OUTCOME_EXPRESSION,
    OUTCOME_EXPRESSION_REPAIR,
    ReviewAnalysisError,
    analyze_direct,
    analyze_expression,
    analyze_reference,
)


ROOT = Path(__file__).resolve().parents[1]
PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
ROUND_ROOT = PROJECT / "external_workflows/big_finish_overlap_reference_v1"


class BoundaryAndExpressionReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.reference_key = json.loads((ROUND_ROOT / "reference_boundary_repair_round_v5/private/answer-key.json").read_text())
        cls.reference_review = json.loads((ROOT / "benchmarks/original_sin_overlap_reference_boundary_repair_review_v5.json").read_text())
        cls.direct_key = json.loads((ROUND_ROOT / "direct_substitution_boundary_repair_round_v4/private/answer-key.json").read_text())
        cls.direct_review = json.loads((ROOT / "benchmarks/original_sin_direct_substitution_boundary_repair_review_v4.json").read_text())
        cls.expression_key = json.loads((ROUND_ROOT / "unseen_expression_round_v1/private/answer-key.json").read_text())
        cls.expression_review = json.loads((ROOT / "benchmarks/original_sin_unseen_expression_review_v1.json").read_text())

    def test_reference_selects_beltempest_and_computer(self) -> None:
        report = analyze_reference(self.reference_key, self.reference_review)
        selected = {row["character"]: row for row in report["character_decisions"]}
        self.assertEqual(selected["Beltempest"]["outcome"], OUTCOME_ANCHOR)
        self.assertEqual(selected["Beltempest"]["selected_candidate_id"], "52c386b56c630e95")
        self.assertEqual(selected["Computer"]["selected_candidate_id"], "048a5ca161610aad")
        self.assertIsNone(selected["The Doctor"]["selected_candidate_id"])
        self.assertIsNone(selected["Shythe Shahid"]["selected_candidate_id"])

    def test_all_direct_candidates_remain_rejected(self) -> None:
        report = analyze_direct(self.direct_key, self.direct_review)
        self.assertTrue(all(row["outcome"] == OUTCOME_DIRECT_REPAIR for row in report["chunk_decisions"]))
        self.assertTrue(all(row["selected_candidate_id"] is None for row in report["chunk_decisions"]))

    def test_expression_selects_four_modes(self) -> None:
        report = analyze_expression(self.expression_key, self.expression_review)
        decisions = {row["group"]: row for row in report["group_decisions"]}
        expected = {
            "chris_protective_concern": "30e3a71f0971b671",
            "under_sergeant_cold_authority": "29f951673070dd39",
            "vaughn_controlled_anger": "9ae61993697e70eb",
            "vaughn_existential_fear": "d4b6c554606669d7",
        }
        for group, candidate_id in expected.items():
            self.assertEqual(decisions[group]["outcome"], OUTCOME_EXPRESSION)
            self.assertEqual(decisions[group]["selected_candidate_id"], candidate_id)
        for group in {
            "bernice_urgent_concern",
            "bernice_dry_irony",
            "chris_urgent_authority",
            "roz_command_authority",
        }:
            self.assertEqual(decisions[group]["outcome"], OUTCOME_EXPRESSION_REPAIR)
            self.assertIsNone(decisions[group]["selected_candidate_id"])

    def test_identity_uncertainty_note_blocks_fish_under_sergeant(self) -> None:
        report = analyze_expression(self.expression_key, self.expression_review)
        row = next(item for item in report["candidates"] if item["candidate_id"] == "88de51aa8b3d1b8d")
        self.assertFalse(row["promotion_eligible"])
        self.assertIn("identity_uncertain", row["note_flags"])

    def test_missing_expression_candidate_fails_closed(self) -> None:
        review = copy.deepcopy(self.expression_review)
        review["results"].pop(next(iter(review["results"])))
        with self.assertRaises(ReviewAnalysisError):
            analyze_expression(self.expression_key, review)

    def test_analysis_is_pure(self) -> None:
        voice_before = (PROJECT / "voice_config.json").read_bytes()
        chunks_before = (PROJECT / "chunks.json").read_bytes()
        analyze_reference(self.reference_key, self.reference_review)
        analyze_direct(self.direct_key, self.direct_review)
        analyze_expression(self.expression_key, self.expression_review)
        self.assertEqual(voice_before, (PROJECT / "voice_config.json").read_bytes())
        self.assertEqual(chunks_before, (PROJECT / "chunks.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
