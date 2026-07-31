from __future__ import annotations

import json
import unittest
from pathlib import Path

from benchmarks.analyze_original_sin_overlap_final_reviews import (
    EXACT, NEUTRAL, REPAIR, FinalReviewError, analyze_direct, analyze_reference,
)

ROOT = Path(__file__).resolve().parents[1]
PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
EVIDENCE = PROJECT / "external_workflows/big_finish_overlap_reference_v1"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class FinalReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference_key = load(EVIDENCE / "reference_final_repair_round_v4/private/answer-key.json")
        cls.reference_review = load(ROOT / "benchmarks/original_sin_overlap_reference_final_repair_review_v4.json")
        cls.direct_key = load(EVIDENCE / "direct_substitution_final_repair_round_v3/private/answer-key.json")
        cls.direct_review = load(ROOT / "benchmarks/original_sin_direct_substitution_final_repair_review_v3.json")

    def test_all_candidates_are_required(self):
        review = json.loads(json.dumps(self.reference_review))
        review["results"].pop(next(iter(review["results"])))
        with self.assertRaises(FinalReviewError):
            analyze_reference(self.reference_key, review)

    def test_clean_reference_winners(self):
        report = analyze_reference(self.reference_key, self.reference_review)
        decisions = {row["character"]: row for row in report["character_decisions"]}
        self.assertEqual(decisions["Under-Sergeant"]["outcome"], NEUTRAL)
        self.assertEqual(decisions["Evan Claple"]["outcome"], NEUTRAL)
        self.assertEqual(decisions["Tobias Vaughn / Robot"]["outcome"], NEUTRAL)

    def test_boundary_and_contamination_notes_block_passes(self):
        report = analyze_reference(self.reference_key, self.reference_review)
        decisions = {row["character"]: row for row in report["character_decisions"]}
        self.assertEqual(decisions["Computer"]["outcome"], REPAIR)
        self.assertEqual(decisions["Shythe Shahid"]["outcome"], REPAIR)

    def test_only_securitybot_direct_line_is_clean(self):
        report = analyze_direct(self.direct_key, self.direct_review)
        decisions = {row["chunk_id"]: row for row in report["chunk_decisions"]}
        self.assertEqual(decisions[618]["outcome"], EXACT)
        self.assertEqual(decisions[5207]["outcome"], "requires repaired direct cut")
        self.assertEqual(decisions[3908]["outcome"], "requires repaired direct cut")
        self.assertEqual(decisions[3098]["outcome"], "requires repaired direct cut")

    def test_analysis_declares_no_production_change(self):
        before_voice = (PROJECT / "voice_config.json").read_bytes()
        before_chunks = (PROJECT / "chunks.json").read_bytes()
        analyze_reference(self.reference_key, self.reference_review)
        analyze_direct(self.direct_key, self.direct_review)
        self.assertEqual((PROJECT / "voice_config.json").read_bytes(), before_voice)
        self.assertEqual((PROJECT / "chunks.json").read_bytes(), before_chunks)


if __name__ == "__main__":
    unittest.main()
