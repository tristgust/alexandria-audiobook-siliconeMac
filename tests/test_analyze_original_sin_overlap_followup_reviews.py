from __future__ import annotations

import unittest
from copy import deepcopy

from benchmarks.analyze_original_sin_overlap_followup_reviews import (
    DIRECT_ROUND_ID,
    EXPECTED_DIRECT_CHUNKS,
    EXPECTED_REPAIR_CHARACTERS,
    FollowupReviewError,
    REPAIR_ROUND_ID,
    analyze_direct,
    analyze_repair,
)


def _repair_fixture():
    candidates = {}
    results = {}
    remaining = 30
    for index, character in enumerate(EXPECTED_REPAIR_CHARACTERS):
        count = 2 if index >= 8 else 3
        if index == 7:
            count = 3
        for item in range(count):
            candidate_id = f"r-{index}-{item}"
            candidates[candidate_id] = {
                "character": character,
                "book_speaker": character.upper(),
                "treatment": "source_mix" if item == 0 else "mossformer2_source_mix",
                "automatic_transcript": "complete line",
                "word_error_rate": 0.0,
                "first_word_present": True,
                "last_word_present": True,
            }
            results[candidate_id] = {
                "decision": "pass" if item == 0 else "fail",
                "isolation": "5",
                "naturalness": "5",
                "identity": "5",
                "usefulness": "5",
            }
            remaining -= 1
    while remaining > 0:
        character = EXPECTED_REPAIR_CHARACTERS[0]
        candidate_id = f"r-extra-{remaining}"
        candidates[candidate_id] = {
            "character": character,
            "book_speaker": "BERNICE",
            "treatment": "mel_roformer_vocal",
            "automatic_transcript": "complete line",
            "word_error_rate": 0.0,
            "first_word_present": True,
            "last_word_present": True,
        }
        results[candidate_id] = {"decision": "fail", "isolation": "1", "naturalness": "5", "identity": "5", "usefulness": "1"}
        remaining -= 1
    return {"candidates": candidates}, {"round_id": REPAIR_ROUND_ID, "results": results}


def _direct_fixture():
    candidates = {}
    results = {}
    for index, chunk_id in enumerate(EXPECTED_DIRECT_CHUNKS):
        count = 1 if chunk_id in {3106, 493} else 2
        for item in range(count):
            candidate_id = f"d-{index}-{item}"
            candidates[candidate_id] = {
                "character": f"Character {index}",
                "book_speaker": f"SPEAKER {index}",
                "chunk_id": chunk_id,
                "treatment": "source_mix" if item == 0 else "mossformer2_source_mix",
                "proxy_sha256": candidate_id,
                "objective_eligible": True,
            }
            results[candidate_id] = {
                "decision": "pass" if item == 0 else "fail",
                "boundaries": "5",
                "isolation": "5",
                "naturalness": "5",
                "usefulness": "5",
            }
    return {"candidates": candidates}, {"round_id": DIRECT_ROUND_ID, "results": results}


class FollowupReviewTests(unittest.TestCase):
    def test_repair_requires_all_30_ids(self):
        answer, review = _repair_fixture()
        review["results"].pop(next(iter(review["results"])))
        with self.assertRaisesRegex(FollowupReviewError, "IDs"):
            analyze_repair(answer, review)

    def test_repair_notes_block_nominal_pass(self):
        answer, review = _repair_fixture()
        candidate_id = next(iter(answer["candidates"]))
        review["results"][candidate_id]["notes"] = "Cuts off early and another voice remains."
        report = analyze_repair(answer, review)
        row = next(item for item in report["candidates"] if item["candidate_id"] == candidate_id)
        self.assertFalse(row["selected"])
        self.assertEqual(row["classification"], "useful after bounded repair")

    def test_repair_objective_failure_cannot_win(self):
        answer, review = _repair_fixture()
        candidate_id = next(iter(answer["candidates"]))
        answer["candidates"][candidate_id]["last_word_present"] = False
        report = analyze_repair(answer, review)
        row = next(item for item in report["candidates"] if item["candidate_id"] == candidate_id)
        self.assertFalse(row["selected"])
        self.assertEqual(row["classification"], "objective-ineligible")

    def test_direct_requires_all_10_ids(self):
        answer, review = _direct_fixture()
        review["results"].pop(next(iter(review["results"])))
        with self.assertRaisesRegex(FollowupReviewError, "IDs"):
            analyze_direct(answer, review)

    def test_direct_notes_block_nominal_pass(self):
        answer, review = _direct_fixture()
        candidate_id = next(iter(answer["candidates"]))
        review["results"][candidate_id]["notes"] = "Cuts out before finishing the last word."
        report = analyze_direct(answer, review)
        row = next(item for item in report["candidates"] if item["candidate_id"] == candidate_id)
        self.assertFalse(row["selected"])
        self.assertEqual(row["classification"], "useful after bounded repair")

    def test_analysis_is_pure(self):
        repair_answer, repair_review = _repair_fixture()
        direct_answer, direct_review = _direct_fixture()
        before = deepcopy((repair_answer, repair_review, direct_answer, direct_review))
        analyze_repair(repair_answer, repair_review)
        analyze_direct(direct_answer, direct_review)
        self.assertEqual((repair_answer, repair_review, direct_answer, direct_review), before)


if __name__ == "__main__":
    unittest.main()
