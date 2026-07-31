from __future__ import annotations

import copy
import unittest

from benchmarks.analyze_original_sin_alternate_and_expression_repair_reviews import (
    ReviewAnalysisError,
    analyze_direct,
    analyze_expression,
)


class AlternateAndExpressionReviewTests(unittest.TestCase):
    def direct_fixture(self):
        candidates = {}
        results = {}
        for chunk, count in ((1317, 2), (4366, 3), (3829, 3)):
            for index in range(count):
                cid = f"d{chunk}-{index}"
                candidates[cid] = {"chunk_id": chunk, "character": str(chunk), "treatment": "source_mix"}
                results[cid] = {"decision": "fail"}
        results["d4366-0"] = {"decision": "pass", "boundaries": "5", "isolation": "4", "naturalness": "5", "usefulness": "4"}
        return (
            {"round_id": "alexandria_original_sin_direct_substitution_alternate_source_v5", "candidates": candidates},
            {"round_id": "alexandria_original_sin_direct_substitution_alternate_source_v5", "results": results},
        )

    def expression_fixture(self):
        groups = [
            ("Bernice Summerfield", "urgent concern", 5),
            ("Bernice Summerfield", "dry irony", 4),
            ("Chris Cwej", "urgent authority", 3),
            ("Roz Forrester", "command authority", 3),
        ]
        candidates = {}
        results = {}
        for group_index, (character, mode, count) in enumerate(groups):
            for index in range(count):
                cid = f"e{group_index}-{index}"
                candidates[cid] = {
                    "character": character,
                    "mode": mode,
                    "route_key": "qwen_current_identity",
                    "requested_backend": "qwen_identity",
                    "actual_backend": "qwen3_instruction_controlled",
                    "fallback_used": False,
                }
                results[cid] = {"decision": "fail", "identity": "3", "delivery": "3", "naturalness": "5", "artifacts": "1"}
        results["e0-0"] = {"decision": "pass", "identity": "4", "delivery": "4", "naturalness": "5", "artifacts": "1"}
        results["e1-0"] = {"decision": "pass", "identity": "5", "delivery": "5", "naturalness": "5", "artifacts": "1"}
        results["e1-1"] = {"decision": "pass", "identity": "5", "delivery": "5", "naturalness": "5", "artifacts": "1"}
        results["e3-0"] = {"decision": "pass", "identity": "5", "delivery": "5", "naturalness": "5", "artifacts": "1"}
        return (
            {"round_id": "alexandria_original_sin_unseen_expression_repair_v2", "candidates": candidates},
            {"round_id": "alexandria_original_sin_unseen_expression_repair_v2", "results": results},
        )

    def test_only_clean_direct_candidate_wins(self):
        report = analyze_direct(*self.direct_fixture())
        winners = {row["chunk_id"]: row["selected_candidate_id"] for row in report["chunk_decisions"]}
        self.assertEqual(winners[4366], "d4366-0")
        self.assertIsNone(winners[1317])
        self.assertIsNone(winners[3829])

    def test_direct_note_blocks_nominal_pass(self):
        answer, review = self.direct_fixture()
        review["results"]["d4366-0"]["notes"] = "artifact at the start"
        report = analyze_direct(answer, review)
        decision = next(row for row in report["chunk_decisions"] if row["chunk_id"] == 4366)
        self.assertIsNone(decision["selected_candidate_id"])

    def test_expression_preserves_all_approved_routes(self):
        report = analyze_expression(*self.expression_fixture())
        dry = next(row for row in report["group_decisions"] if row["mode"] == "dry irony")
        self.assertEqual(len(dry["approved_candidate_ids"]), 2)
        urgent = next(row for row in report["group_decisions"] if row["character"] == "Chris Cwej")
        self.assertIsNone(urgent["primary_candidate_id"])

    def test_expression_delivery_below_four_is_not_approved(self):
        answer, review = self.expression_fixture()
        review["results"]["e0-0"]["delivery"] = "3"
        report = analyze_expression(answer, review)
        urgent = next(row for row in report["group_decisions"] if row["mode"] == "urgent concern")
        self.assertIsNone(urgent["primary_candidate_id"])

    def test_missing_candidate_is_rejected(self):
        answer, review = self.expression_fixture()
        review = copy.deepcopy(review)
        review["results"].pop(next(iter(review["results"])))
        with self.assertRaises(ReviewAnalysisError):
            analyze_expression(answer, review)


if __name__ == "__main__":
    unittest.main()
