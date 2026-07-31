from __future__ import annotations

import json
import unittest
from pathlib import Path

from benchmarks.original_sin_overlap_word_alignment import (
    TimedWord,
    WordAlignmentError,
    exact_alignment_record,
    alias_aware_word_error_rate,
    locate_declared_span,
    locate_exact_span,
    normalized_words,
    transcript_comparison,
    word_error_rate,
)


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "benchmarks" / "original_sin_overlap_reference_repair_plan.json"


class OriginalSinOverlapWordAlignmentTests(unittest.TestCase):
    def test_exact_span_locates_expected_words_inside_context(self) -> None:
        words = [
            TimedWord("before", 0.0, 0.2, 0.9),
            TimedWord("keep", 0.3, 0.5, 0.9),
            TimedWord("up", 0.5, 0.6, 0.9),
            TimedWord("now", 0.7, 0.9, 0.9),
        ]
        self.assertEqual(locate_exact_span("Keep up", words), (1, 2))

    def test_exact_span_rejects_changed_word(self) -> None:
        words = [TimedWord("keep", 0.0, 0.2, 0.9), TimedWord("down", 0.2, 0.4, 0.9)]
        with self.assertRaises(WordAlignmentError):
            locate_exact_span("Keep up", words)

    def test_declared_alignment_alias_is_bounded_and_recorded(self) -> None:
        words = [
            TimedWord("enough", 0.0, 0.2, 0.9),
            TimedWord("bus", 0.2, 0.4, 0.8),
            TimedWord("already", 0.4, 0.6, 0.9),
        ]
        match = locate_declared_span(
            ["enough fuss already"],
            words,
            {"fuss": ["bus"]},
        )
        self.assertEqual(match["first_index"], 0)
        self.assertEqual(
            match["word_aliases_used"],
            [{"position": 1, "expected": "fuss", "observed": "bus"}],
        )

    def test_undeclared_wording_change_remains_ineligible(self) -> None:
        self.assertGreater(
            alias_aware_word_error_rate(
                "you were going to let",
                "you were gonna let",
            ),
            0.0,
        )

    def test_declared_adaptation_variant_can_match_without_rewriting_book_text(self) -> None:
        comparison = transcript_comparison(
            [
                "But you were going to let the Doctor die.",
                "But you were gonna let the Doctor die.",
            ],
            "But you were gonna let the Doctor die.",
        )
        self.assertEqual(comparison["word_error_rate"], 0.0)
        self.assertTrue(comparison["matched_accepted_transcript"])

    def test_alignment_record_preserves_first_and_last_word_times(self) -> None:
        result = {
            "text": "Noise. Keep up with the news. More.",
            "segments": [
                {
                    "words": [
                        {"word": " Noise.", "start": 0.0, "end": 0.2, "probability": 0.8},
                        {"word": " Keep", "start": 0.3, "end": 0.5, "probability": 0.9},
                        {"word": " up", "start": 0.5, "end": 0.6, "probability": 0.95},
                        {"word": " with", "start": 0.6, "end": 0.7, "probability": 0.95},
                        {"word": " the", "start": 0.7, "end": 0.8, "probability": 0.95},
                        {"word": " news.", "start": 0.8, "end": 1.0, "probability": 0.92},
                    ]
                }
            ],
        }
        record = exact_alignment_record("Keep up with the news.", result)
        self.assertEqual(record["first_word"], "keep")
        self.assertEqual(record["last_word"], "news")
        self.assertEqual(record["word_start_seconds"], 0.3)
        self.assertEqual(record["word_end_seconds"], 1.0)

    def test_normalization_and_wer_handle_curly_apostrophes(self) -> None:
        self.assertEqual(normalized_words("I’m ready."), ["i'm", "ready"])
        self.assertEqual(word_error_rate("I’m ready.", "I'm ready"), 0.0)

    def test_normalization_discards_quote_marker_hallucinations(self) -> None:
        self.assertEqual(
            normalized_words("\\'I have been frightened.\\'"),
            ["i", "have", "been", "frightened"],
        )

    def test_repair_plan_is_bounded_and_encodes_vaughn_robot_context(self) -> None:
        plan = json.loads(PLAN.read_text(encoding="utf-8"))
        self.assertEqual(len(plan["groups"]), 11)
        self.assertEqual(sum(len(group["treatments"]) for group in plan["groups"]), 33)
        self.assertEqual(plan["candidate_count"], 33)
        vaughn = next(group for group in plan["groups"] if group["book_speaker"] == "TOBIAS VAUGHN")
        self.assertIn("machine-bodied throughout", vaughn["review_context"])
        homeless = next(group for group in plan["groups"] if group["book_speaker"] == "HOMELESS FORSAKEN")
        self.assertNotIn("mel_roformer_vocal", homeless["treatments"])
        self.assertEqual(
            homeless["treatments"],
            ["source_mix", "mossformer2_blend50", "mossformer2_blend70"],
        )
        self.assertLessEqual(homeless["trailing_margin_seconds"], 0.08)
        chris = next(group for group in plan["groups"] if group["book_speaker"] == "CHRIS CWEJ")
        self.assertEqual(chris["expected_transcript"], "But you were gonna let the Doctor and Bernice die.")
        self.assertEqual(
            chris["accepted_transcript_variants"],
            ["But you were going to let the Doctor and Bernice die."],
        )
        self.assertEqual(chris["semantic_variant_approval"]["status"], "explicitly accepted as semantically equivalent by the user")
        under_sergeant = next(
            group for group in plan["groups"] if group["book_speaker"] == "UNDER-SERGEANT"
        )
        self.assertEqual(
            under_sergeant["alignment_word_aliases"],
            {"fuss": ["bus", "but"]},
        )
        self.assertTrue(homeless["preserve_segment_end"])


if __name__ == "__main__":
    unittest.main()
