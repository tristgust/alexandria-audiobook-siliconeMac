from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path

from benchmarks.analyze_original_sin_overlap_v3_reviews import (
    OUTCOME_EXACT,
    OUTCOME_NEUTRAL,
    OUTCOME_PERFORMANCE,
    OUTCOME_REPAIR,
    V3ReviewError,
    analyze_direct,
    analyze_reference,
)


ROOT = Path(__file__).resolve().parents[1]
PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
EVIDENCE = PROJECT / "external_workflows" / "big_finish_overlap_reference_v1"


class V3ReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference_answer = json.loads((EVIDENCE / "reference_repair_round_v3/private/answer-key.json").read_text())
        cls.reference_review = json.loads((ROOT / "benchmarks/original_sin_overlap_reference_repair_review_v3.json").read_text())
        cls.direct_answer = json.loads((EVIDENCE / "direct_substitution_repair_round_v2/private/answer-key.json").read_text())
        cls.direct_review = json.loads((ROOT / "benchmarks/original_sin_direct_substitution_repair_review_v2.json").read_text())

    def test_reference_requires_all_24_candidates(self):
        review = deepcopy(self.reference_review)
        review["results"].pop(next(iter(review["results"])))
        with self.assertRaisesRegex(V3ReviewError, "24 candidates"):
            analyze_reference(self.reference_answer, review)

    def test_written_boundary_note_blocks_nominal_pass(self):
        report = analyze_reference(self.reference_answer, self.reference_review)
        doctor = next(row for row in report["character_decisions"] if row["character"] == "The Doctor")
        self.assertEqual(doctor["outcome"], OUTCOME_REPAIR)
        self.assertIsNone(doctor["selected_candidate_id"])

    def test_clean_and_restricted_reference_winners(self):
        report = analyze_reference(self.reference_answer, self.reference_review)
        decisions = {row["character"]: row for row in report["character_decisions"]}
        self.assertEqual(decisions["Bernice Summerfield"]["outcome"], OUTCOME_NEUTRAL)
        self.assertEqual(decisions["Bernice Summerfield"]["selected_candidate_id"], "09dfbec8d8b78cac")
        self.assertEqual(decisions["Chris Cwej"]["outcome"], OUTCOME_NEUTRAL)
        self.assertEqual(decisions["Chris Cwej"]["selected_candidate_id"], "1e691578853f9a75")
        self.assertEqual(decisions["Beltempest"]["outcome"], OUTCOME_PERFORMANCE)
        self.assertEqual(decisions["Beltempest"]["selected_candidate_id"], "6945549ae256b0f7")

    def test_under_sergeant_intercom_note_prevents_anchor(self):
        report = analyze_reference(self.reference_answer, self.reference_review)
        decision = next(row for row in report["character_decisions"] if row["character"] == "Under-Sergeant")
        self.assertEqual(decision["outcome"], OUTCOME_REPAIR)
        self.assertIsNone(decision["selected_candidate_id"])

    def test_direct_requires_all_14_candidates(self):
        review = deepcopy(self.direct_review)
        review["results"].pop(next(iter(review["results"])))
        with self.assertRaisesRegex(V3ReviewError, "14 candidates"):
            analyze_direct(self.direct_answer, review)

    def test_only_rashid_is_directly_eligible(self):
        report = analyze_direct(self.direct_answer, self.direct_review)
        selected = [row for row in report["chunk_decisions"] if row["outcome"] == OUTCOME_EXACT]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["chunk_id"], 405)
        self.assertEqual(selected[0]["selected_candidate_id"], "d6d57762ed1461ad")


if __name__ == "__main__":
    unittest.main()
