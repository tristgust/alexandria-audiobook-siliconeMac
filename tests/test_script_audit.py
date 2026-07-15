from __future__ import annotations

import unittest

from script_audit import audit_script_chunk
from script_audit import format_audit_summary
from script_audit import split_source_segments


def entry(
    speaker: str,
    text: str,
    instruct: str = "Test delivery.",
):
    return {
        "speaker": speaker,
        "text": text,
        "instruct": instruct,
    }


class SourceBoundaryTests(unittest.TestCase):
    def test_straight_quote_boundaries(self):
        segments = split_source_segments(
            '"Wait," he said. "Listen to me."'
        )

        self.assertEqual(
            [
                segment.kind
                for segment in segments
            ],
            [
                "dialogue",
                "narration",
                "dialogue",
            ],
        )

        self.assertEqual(
            [
                segment.text
                for segment in segments
            ],
            [
                "Wait,",
                "he said.",
                "Listen to me.",
            ],
        )

    def test_curly_double_quote_boundaries(self):
        segments = split_source_segments(
            "“Wait,” he said. “Listen to me.”"
        )

        self.assertEqual(
            len(segments),
            3,
        )

    def test_curly_single_quote_boundaries(self):
        segments = split_source_segments(
            "‘Wait,’ he said. ‘Listen to me.’"
        )

        self.assertEqual(
            [
                segment.kind
                for segment in segments
            ],
            [
                "dialogue",
                "narration",
                "dialogue",
            ],
        )


class PassingAuditTests(unittest.TestCase):
    def test_exact_interrupted_dialogue(self):
        result = audit_script_chunk(
            (
                '"Wait," he said, looking away. '
                '"Listen to me."'
            ),
            [
                entry(
                    "MARCUS",
                    "Wait,",
                ),
                entry(
                    "NARRATOR",
                    "he said, looking away.",
                ),
                entry(
                    "MARCUS",
                    "Listen to me.",
                ),
            ],
        )

        self.assertTrue(result.passed)

        self.assertEqual(
            result.metrics[
                "exact_match_count"
            ],
            3,
        )

    def test_optional_pronoun_clarification(self):
        result = audit_script_chunk(
            (
                '"Wait," he said, looking away. '
                '"Listen to me."'
            ),
            [
                entry(
                    "MARCUS",
                    "Wait,",
                ),
                entry(
                    "NARRATOR",
                    "Marcus said, looking away.",
                ),
                entry(
                    "MARCUS",
                    "Listen to me.",
                ),
            ],
        )

        self.assertTrue(result.passed)

        self.assertEqual(
            result.metrics[
                "attribution_clarification_count"
            ],
            1,
        )

    def test_original_pronoun_is_also_valid(self):
        result = audit_script_chunk(
            '"Wait," she replied quietly.',
            [
                entry(
                    "BERNICE",
                    "Wait,",
                ),
                entry(
                    "NARRATOR",
                    "she replied quietly.",
                ),
            ],
        )

        self.assertTrue(result.passed)

    def test_inverted_attribution_is_preserved(self):
        result = audit_script_chunk(
            (
                '"There is nothing to tell," '
                "said Marcus, unable to meet her gaze."
            ),
            [
                entry(
                    "MARCUS",
                    "There is nothing to tell,",
                ),
                entry(
                    "NARRATOR",
                    (
                        "said Marcus, unable to meet "
                        "her gaze."
                    ),
                ),
            ],
        )

        self.assertTrue(result.passed)

    def test_one_source_segment_may_use_multiple_entries(
        self,
    ):
        result = audit_script_chunk(
            (
                '"This is the first sentence. '
                'This is the second sentence."'
            ),
            [
                entry(
                    "MARCUS",
                    "This is the first sentence.",
                ),
                entry(
                    "MARCUS",
                    "This is the second sentence.",
                ),
            ],
        )

        self.assertTrue(result.passed)

        self.assertEqual(
            result.metrics[
                "matched_segment_count"
            ],
            1,
        )

    def test_explicit_tts_conversion(self):
        result = audit_script_chunk(
            (
                "Dr. Smith entered on the 3rd day "
                "of Chapter IV & sat down."
            ),
            [
                entry(
                    "NARRATOR",
                    (
                        "Doctor Smith entered on the "
                        "third day of Chapter Four "
                        "and sat down."
                    ),
                ),
            ],
        )

        self.assertTrue(result.passed)

        self.assertEqual(
            result.metrics[
                "tts_conversion_count"
            ],
            1,
        )


class BlockingAuditTests(unittest.TestCase):
    def test_rewritten_inverted_attribution_blocks(
        self,
    ):
        result = audit_script_chunk(
            (
                '"There is nothing to tell," '
                "said Marcus, unable to meet her gaze."
            ),
            [
                entry(
                    "MARCUS",
                    "There is nothing to tell,",
                ),
                entry(
                    "NARRATOR",
                    (
                        "Marcus was unable to meet "
                        "her gaze."
                    ),
                ),
            ],
        )

        self.assertFalse(result.passed)

        self.assertIn(
            "attribution_changed",
            {
                issue.code
                for issue in result.blocking_issues
            },
        )

    def test_ordinary_pronoun_replacement_blocks(self):
        result = audit_script_chunk(
            "He walked away from the window.",
            [
                entry(
                    "NARRATOR",
                    (
                        "Marcus walked away from "
                        "the window."
                    ),
                ),
            ],
        )

        self.assertFalse(result.passed)

        self.assertIn(
            "source_text_changed",
            {
                issue.code
                for issue in result.blocking_issues
            },
        )

    def test_dialogue_merged_across_attribution_blocks(
        self,
    ):
        result = audit_script_chunk(
            (
                '"No," he said. '
                '"It rarely is."'
            ),
            [
                entry(
                    "THE DOCTOR",
                    "No, It rarely is.",
                ),
            ],
        )

        self.assertFalse(result.passed)

        self.assertIn(
            "merged_across_narrator_boundary",
            {
                issue.code
                for issue in result.blocking_issues
            },
        )

    def test_missing_attribution_boundary_blocks(
        self,
    ):
        result = audit_script_chunk(
            (
                '"No," he said. '
                '"It rarely is."'
            ),
            [
                entry(
                    "THE DOCTOR",
                    "No,",
                ),
                entry(
                    "THE DOCTOR",
                    "It rarely is.",
                ),
            ],
        )

        self.assertFalse(result.passed)

        self.assertIn(
            "missing_attribution_boundary",
            {
                issue.code
                for issue in result.blocking_issues
            },
        )

    def test_dialogue_as_narrator_blocks(self):
        result = audit_script_chunk(
            '"Do not touch that."',
            [
                entry(
                    "NARRATOR",
                    "Do not touch that.",
                ),
            ],
        )

        self.assertFalse(result.passed)

        self.assertIn(
            "dialogue_as_narrator",
            {
                issue.code
                for issue in result.blocking_issues
            },
        )

    def test_narration_as_character_blocks(self):
        result = audit_script_chunk(
            "The door opened.",
            [
                entry(
                    "MARCUS",
                    "The door opened.",
                ),
            ],
        )

        self.assertFalse(result.passed)

        self.assertIn(
            "narration_as_character",
            {
                issue.code
                for issue in result.blocking_issues
            },
        )

    def test_punctuation_change_blocks(self):
        result = audit_script_chunk(
            '"No,"',
            [
                entry(
                    "THE DOCTOR",
                    "No.",
                ),
            ],
        )

        self.assertFalse(result.passed)

        self.assertIn(
            "punctuation_changed",
            {
                issue.code
                for issue in result.blocking_issues
            },
        )

    def test_extra_output_blocks(self):
        result = audit_script_chunk(
            "The door opened.",
            [
                entry(
                    "NARRATOR",
                    "The door opened.",
                ),
                entry(
                    "NARRATOR",
                    "This sentence was invented.",
                ),
            ],
        )

        self.assertFalse(result.passed)

        self.assertIn(
            "extra_output",
            {
                issue.code
                for issue in result.blocking_issues
            },
        )

    def test_unbalanced_source_quotes_block(self):
        result = audit_script_chunk(
            '"This quotation never closes.',
            [
                entry(
                    "MARCUS",
                    "This quotation never closes.",
                ),
            ],
        )

        self.assertFalse(result.passed)

        self.assertIn(
            "unbalanced_source_quotes",
            {
                issue.code
                for issue in result.blocking_issues
            },
        )

    def test_invalid_entry_fields_block(self):
        result = audit_script_chunk(
            "The door opened.",
            [
                {
                    "speaker": "NARRATOR",
                    "text": "The door opened.",
                }
            ],
        )

        self.assertFalse(result.passed)

        self.assertIn(
            "invalid_entry_fields",
            {
                issue.code
                for issue in result.blocking_issues
            },
        )


class ReportingTests(unittest.TestCase):
    def test_summary_reports_pass(self):
        result = audit_script_chunk(
            '"Hello."',
            [
                entry(
                    "MARCUS",
                    "Hello.",
                ),
            ],
        )

        summary = "\n".join(
            format_audit_summary(result)
        )

        self.assertIn(
            "Fidelity audit: PASS",
            summary,
        )

        self.assertIn(
            "segments 1/1",
            summary,
        )

    def test_result_serializes(self):
        result = audit_script_chunk(
            '"Hello."',
            [
                entry(
                    "MARCUS",
                    "Hello.",
                ),
            ],
        )

        serialized = result.to_dict()

        self.assertTrue(
            serialized["passed"]
        )

        self.assertIn(
            "metrics",
            serialized,
        )

        self.assertIn(
            "matches",
            serialized,
        )


if __name__ == "__main__":
    unittest.main()
