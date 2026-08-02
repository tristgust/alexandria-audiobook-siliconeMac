from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from typing import Callable

import engine_qualification as qualification
import engine_qualification_review as review


def items() -> list[dict[str, object]]:
    return [
        {
            "expected_item_id": f"private_{index}", "subject_id": "qwen3_base", "record_fingerprint": "a" * 64,
            "profile_hash": "b" * 64, "audio_identity": f"audio-{index}", "required_playback": True,
            "restriction_options": ["fixture_only"],
        }
        for index in range(3)
    ]


def vote(label: str) -> dict[str, object]:
    return {
        "label": label, "playback_complete": True, **{field: 3 for field in review.RATING_FIELDS},
        "restrictions": [], "notes": "data only", "included": True, "exclusion_reason": None,
    }


class EngineQualificationReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.package = review.build_review_package(items(), "b20-t07-fixture-v1")

    def assert_code(self, code: str, callback: Callable[[], object]) -> None:
        with self.assertRaises(qualification.QualificationError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)

    def test_same_seed_is_byte_identical(self) -> None:
        other = review.build_review_package(items(), "b20-t07-fixture-v1")
        self.assertEqual(qualification.canonical_bytes(self.package), qualification.canonical_bytes(other))

    def test_different_seed_changes_labels(self) -> None:
        other = review.build_review_package(items(), "another-seed")
        self.assertNotEqual(self.package["public"]["items"], other["public"]["items"])

    def test_public_package_contains_no_private_item_ids(self) -> None:
        raw = qualification.canonical_bytes(self.package["public"])
        self.assertNotIn(b"private_", raw)
        self.assertNotIn(b"item_id", raw)
        self.assertNotIn(b"audio-0", raw)

    def test_answer_key_preserves_identity_mapping(self) -> None:
        self.assertEqual({entry["expected_item_id"] for entry in self.package["answer_key"]["mappings"]}, {f"private_{index}" for index in range(3)})
        self.assertEqual({entry["audio_identity"] for entry in self.package["answer_key"]["mappings"]}, {f"audio-{index}" for index in range(3)})

    def test_template_lists_every_item_incomplete(self) -> None:
        labels = [item["label"] for item in self.package["public"]["items"]]
        self.assertEqual(self.package["result_template"]["incomplete_labels"], labels)

    def test_partial_result_navigation_lists_only_unrated(self) -> None:
        label = self.package["public"]["items"][0]["label"]
        result = review.build_review_result(self.package, [vote(label)])
        self.assertNotIn(label, result["incomplete_labels"])
        self.assertEqual(len(result["incomplete_labels"]), 2)

    def test_complete_result_round_trips(self) -> None:
        votes = [vote(item["label"]) for item in self.package["public"]["items"]]
        result = review.build_review_result(self.package, votes)
        self.assertEqual(review.validate_review_result(self.package["public"], self.package["answer_key"], result)["incomplete_labels"], [])
        self.assert_code("invalid_review_vote_set", lambda: review.build_review_result(self.package, list(reversed(votes))))

    def test_tampered_result_fails_closed(self) -> None:
        result = copy.deepcopy(self.package["result_template"])
        result["incomplete_labels"] = []
        self.assert_code("tampered_review", lambda: review.validate_review_result(self.package["public"], self.package["answer_key"], result))

    def test_key_mismatch_fails_closed(self) -> None:
        key = copy.deepcopy(self.package["answer_key"])
        key["package_hash"] = "0" * 64
        self.assert_code("review_key_mismatch", lambda: review.validate_review_package(self.package["public"], key))

    def test_bool_rating_is_rejected(self) -> None:
        one = vote(self.package["public"]["items"][0]["label"])
        one[review.RATING_FIELDS[0]] = True
        self.assert_code("invalid_review_rating", lambda: review.build_review_result(self.package, [one]))

    def test_incomplete_playback_is_rejected(self) -> None:
        one = vote(self.package["public"]["items"][0]["label"])
        one["playback_complete"] = False
        self.assert_code("incomplete_playback", lambda: review.build_review_result(self.package, [one]))

    def test_publication_requires_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "package"
            interrupted = Path(temporary) / "interrupted"
            private_parent = Path(temporary) / "controlled"; private_parent.mkdir()
            interrupted_private = private_parent / "interrupted-key"
            self.assert_code("review_publication_interrupted", lambda: review.publish_review_package(interrupted, interrupted_private, self.package, interrupt_at="before_rename"))
            self.assertFalse(interrupted.exists())
            key_root = private_parent / "answer-key"
            review.publish_review_package(root, key_root, self.package)
            self.assertEqual({path.name for path in root.iterdir()}, {"public.json", "result-template.json"})
            self.assertEqual({path.name for path in key_root.iterdir()}, {"answer-key.json"})
            self.assert_code("review_key_destination_unsafe", lambda: review.publish_review_package(Path(temporary) / "same-a", Path(temporary) / "same-b", self.package))


if __name__ == "__main__":
    unittest.main()
