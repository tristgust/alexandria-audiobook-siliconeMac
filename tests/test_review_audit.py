from __future__ import annotations

import unittest

from review_audit import audit_review_batch
from review_audit import format_review_audit_summary


def entry(
    speaker: str,
    text: str,
    instruct: str = "Neutral delivery.",
):
    return {
        "speaker": speaker,
        "text": text,
        "instruct": instruct,
    }


class PassingReviewAuditTests(
    unittest.TestCase
):
    def test_unchanged_entries_pass(self):
        original = [
            entry(
                "NARRATOR",
                "The door opened.",
            ),
            entry(
                "MARCUS",
                "Wait.",
            ),
        ]

        result = audit_review_batch(
            original,
            original,
        )

        self.assertTrue(result.passed)

        self.assertTrue(
            result.metrics["exact_text_match"]
        )

    def test_entry_split_passes(self):
        original = [
            entry(
                "NARRATOR",
                (
                    "The door opened. "
                    "Marcus stepped inside."
                ),
            )
        ]

        corrected = [
            entry(
                "NARRATOR",
                "The door opened.",
            ),
            entry(
                "NARRATOR",
                "Marcus stepped inside.",
            ),
        ]

        result = audit_review_batch(
            original,
            corrected,
        )

        self.assertTrue(result.passed)

        self.assertEqual(
            result.metrics["entry_delta"],
            1,
        )

    def test_entry_merge_passes(self):
        original = [
            entry(
                "NARRATOR",
                "The door opened.",
            ),
            entry(
                "NARRATOR",
                "Marcus stepped inside.",
            ),
        ]

        corrected = [
            entry(
                "NARRATOR",
                (
                    "The door opened. "
                    "Marcus stepped inside."
                ),
            )
        ]

        result = audit_review_batch(
            original,
            corrected,
        )

        self.assertTrue(result.passed)

    def test_speaker_and_instruct_changes_pass(self):
        original = [
            entry(
                "NARRATOR",
                "Tell me the truth.",
                "Neutral narration.",
            )
        ]

        corrected = [
            entry(
                "ELENA",
                "Tell me the truth.",
                "Firm, restrained anger.",
            )
        ]

        result = audit_review_batch(
            original,
            corrected,
        )

        self.assertTrue(result.passed)

    def test_whitespace_normalization_passes(self):
        original = [
            entry(
                "NARRATOR",
                "The door\nopened.",
            )
        ]

        corrected = [
            entry(
                "NARRATOR",
                "The   door opened.",
            )
        ]

        result = audit_review_batch(
            original,
            corrected,
        )

        self.assertTrue(result.passed)


class BlockingReviewAuditTests(
    unittest.TestCase
):
    def test_omitted_text_blocks(self):
        original = [
            entry(
                "NARRATOR",
                (
                    "The door opened. "
                    "Marcus stepped inside."
                ),
            )
        ]

        corrected = [
            entry(
                "NARRATOR",
                "The door opened.",
            )
        ]

        result = audit_review_batch(
            original,
            corrected,
        )

        self.assertFalse(result.passed)

        self.assertIn(
            "text_omitted",
            {
                issue.code
                for issue in result.blocking_issues
            },
        )

    def test_added_text_blocks(self):
        original = [
            entry(
                "NARRATOR",
                "The door opened.",
            )
        ]

        corrected = [
            entry(
                "NARRATOR",
                (
                    "The door opened. "
                    "Marcus stepped inside."
                ),
            )
        ]

        result = audit_review_batch(
            original,
            corrected,
        )

        self.assertFalse(result.passed)

        self.assertIn(
            "text_added",
            {
                issue.code
                for issue in result.blocking_issues
            },
        )

    def test_punctuation_change_blocks(self):
        original = [
            entry(
                "MARCUS",
                "No,",
            )
        ]

        corrected = [
            entry(
                "MARCUS",
                "No.",
            )
        ]

        result = audit_review_batch(
            original,
            corrected,
        )

        self.assertFalse(result.passed)

        self.assertIn(
            "punctuation_or_structure_changed",
            {
                issue.code
                for issue in result.blocking_issues
            },
        )

    def test_wording_change_blocks(self):
        original = [
            entry(
                "NARRATOR",
                "He said in confusion.",
            )
        ]

        corrected = [
            entry(
                "NARRATOR",
                "He seemed confused.",
            )
        ]

        result = audit_review_batch(
            original,
            corrected,
        )

        self.assertFalse(result.passed)

        self.assertIn(
            "wording_or_order_changed",
            {
                issue.code
                for issue in result.blocking_issues
            },
        )

    def test_shorter_rewrite_is_not_mislabeled_omission(
        self,
    ):
        original = [
            entry(
                "NARRATOR",
                (
                    "Marcus was unable to meet "
                    "her gaze."
                ),
            )
        ]

        corrected = [
            entry(
                "NARRATOR",
                "Marcus looked away.",
            )
        ]

        result = audit_review_batch(
            original,
            corrected,
        )

        codes = {
            issue.code
            for issue in result.blocking_issues
        }

        self.assertIn(
            "wording_or_order_changed",
            codes,
        )

        self.assertNotIn(
            "text_omitted",
            codes,
        )

    def test_reordered_text_blocks(self):
        original = [
            entry(
                "NARRATOR",
                "The door opened.",
            ),
            entry(
                "NARRATOR",
                "Marcus stepped inside.",
            ),
        ]

        corrected = [
            entry(
                "NARRATOR",
                "Marcus stepped inside.",
            ),
            entry(
                "NARRATOR",
                "The door opened.",
            ),
        ]

        result = audit_review_batch(
            original,
            corrected,
        )

        self.assertFalse(result.passed)

        self.assertIn(
            "wording_or_order_changed",
            {
                issue.code
                for issue in result.blocking_issues
            },
        )

    def test_invalid_corrected_schema_blocks(self):
        original = [
            entry(
                "NARRATOR",
                "The door opened.",
            )
        ]

        corrected = [
            {
                "speaker": "NARRATOR",
                "text": "The door opened.",
            }
        ]

        result = audit_review_batch(
            original,
            corrected,
        )

        self.assertFalse(result.passed)

        self.assertIn(
            "invalid_corrected_entry_fields",
            {
                issue.code
                for issue in result.blocking_issues
            },
        )

    def test_empty_corrected_text_blocks(self):
        original = [
            entry(
                "NARRATOR",
                "The door opened.",
            )
        ]

        corrected = [
            {
                "speaker": "NARRATOR",
                "text": "",
                "instruct": "Neutral narration.",
            }
        ]

        result = audit_review_batch(
            original,
            corrected,
        )

        self.assertFalse(result.passed)

        self.assertIn(
            "invalid_corrected_text",
            {
                issue.code
                for issue in result.blocking_issues
            },
        )


class ReviewAuditReportingTests(
    unittest.TestCase
):
    def test_pass_summary(self):
        original = [
            entry(
                "NARRATOR",
                "The door opened.",
            )
        ]

        result = audit_review_batch(
            original,
            original,
        )

        summary = "\n".join(
            format_review_audit_summary(
                result
            )
        )

        self.assertIn(
            "Review text audit: PASS",
            summary,
        )

        self.assertIn(
            "exact=True",
            summary,
        )

    def test_blocked_result_serializes(self):
        original = [
            entry(
                "NARRATOR",
                "The door opened.",
            )
        ]

        corrected = [
            entry(
                "NARRATOR",
                "The door closed.",
            )
        ]

        result = audit_review_batch(
            original,
            corrected,
        )

        serialized = result.to_dict()

        self.assertFalse(
            serialized["passed"]
        )

        self.assertGreater(
            serialized["blocking_count"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
