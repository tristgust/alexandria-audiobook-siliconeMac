from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from benchmarks.analyze_original_sin_overlap_reference_review import (
    EXPECTED_CHARACTERS,
    EXPECTED_TREATMENTS,
    OUTCOME_NEUTRAL,
    OUTCOME_REPAIR,
    ROUND_ID,
    ReviewAnalysisError,
    analyze_round,
)


def _complete_round():
    candidates = {}
    results = {}
    for character_index, character in enumerate(EXPECTED_CHARACTERS):
        for treatment_index, treatment in enumerate(sorted(EXPECTED_TREATMENTS)):
            candidate_id = f"candidate-{character_index:02d}-{treatment_index}"
            candidates[candidate_id] = {
                "character": character,
                "book_speaker": character.upper(),
                "variant": treatment,
                "automatic_transcript": "Complete test line",
                "word_error_rate": 0.0,
                "first_word_present": True,
                "path": f"/private/path/{candidate_id}.wav",
            }
            results[candidate_id] = {
                "decision": "pass" if treatment == "source_mix" else "fail",
                "isolation": "5" if treatment == "source_mix" else "3",
                "naturalness": "5",
                "identity": "5",
                "usefulness": "5" if treatment == "source_mix" else "3",
                "notes": "",
            }
    return (
        {"schema_version": 1, "round_id": ROUND_ID, "candidates": candidates},
        {"schema_version": 1, "round_id": ROUND_ID, "results": results},
    )


def _ids_for_character(answer_key, character):
    return [
        candidate_id
        for candidate_id, candidate in answer_key["candidates"].items()
        if candidate["character"] == character
    ]


def _id_for_treatment(answer_key, character, treatment):
    return next(
        candidate_id
        for candidate_id in _ids_for_character(answer_key, character)
        if answer_key["candidates"][candidate_id]["variant"] == treatment
    )


class OriginalSinOverlapReviewAnalysisTests(unittest.TestCase):
    def test_all_51_candidate_ids_must_be_accounted_for(self):
        answer_key, review = _complete_round()
        review["results"].pop(next(iter(review["results"])))

        with self.assertRaisesRegex(ReviewAnalysisError, "candidate mismatch"):
            analyze_round(answer_key, review)

    def test_all_17_character_groups_must_be_accounted_for(self):
        answer_key, review = _complete_round()
        first_id = next(iter(answer_key["candidates"]))
        answer_key["candidates"][first_id]["character"] = "Unexpected Character"

        with self.assertRaisesRegex(ReviewAnalysisError, "Character groups"):
            analyze_round(answer_key, review)

    def test_objective_failure_cannot_be_selected(self):
        answer_key, review = _complete_round()
        character = EXPECTED_CHARACTERS[0]
        source_id = _id_for_treatment(answer_key, character, "source_mix")
        alternate_id = _id_for_treatment(answer_key, character, "mel_roformer_vocal")
        answer_key["candidates"][source_id]["word_error_rate"] = 0.5
        review["results"][alternate_id].update(
            decision="pass", isolation="4", naturalness="5", identity="5", usefulness="4"
        )

        report = analyze_round(answer_key, review)
        source = next(row for row in report["candidates"] if row["candidate_id"] == source_id)
        decision = next(row for row in report["character_decisions"] if row["character"] == character)

        self.assertFalse(source["objective_eligible"])
        self.assertFalse(source["selected"])
        self.assertEqual(decision["selected_candidate_id"], alternate_id)

    def test_human_notes_can_block_promotion(self):
        answer_key, review = _complete_round()
        character = EXPECTED_CHARACTERS[0]
        source_id = _id_for_treatment(answer_key, character, "source_mix")
        alternate_id = _id_for_treatment(answer_key, character, "mel_roformer_vocal")
        review["results"][source_id]["notes"] = "Another voice and a background sound remain."
        review["results"][alternate_id].update(
            decision="pass", isolation="4", naturalness="5", identity="5", usefulness="4"
        )

        report = analyze_round(answer_key, review)
        source = next(row for row in report["candidates"] if row["candidate_id"] == source_id)
        decision = next(row for row in report["character_decisions"] if row["character"] == character)

        self.assertEqual(source["final_classification"], OUTCOME_REPAIR)
        self.assertFalse(source["selected"])
        self.assertEqual(decision["outcome"], OUTCOME_NEUTRAL)
        self.assertEqual(decision["selected_candidate_id"], alternate_id)

    def test_analysis_does_not_mutate_project_voice_or_chunk_state(self):
        answer_key, review = _complete_round()
        with TemporaryDirectory() as temporary_directory:
            project = Path(temporary_directory) / "project"
            project.mkdir()
            voice_config = project / "voice_config.json"
            chunks = project / "chunks.json"
            voice_config.write_text('{"sentinel":"voice"}\n', encoding="utf-8")
            chunks.write_text('[{"sentinel":"chunk"}]\n', encoding="utf-8")
            before = {
                voice_config: voice_config.read_bytes(),
                chunks: chunks.read_bytes(),
            }

            report = analyze_round(deepcopy(answer_key), deepcopy(review))

            self.assertFalse(report["production_changes"])
            self.assertFalse(report["project_voice_config_changed"])
            self.assertFalse(report["project_chunks_changed"])
            self.assertEqual({path: path.read_bytes() for path in before}, before)


if __name__ == "__main__":
    unittest.main()
