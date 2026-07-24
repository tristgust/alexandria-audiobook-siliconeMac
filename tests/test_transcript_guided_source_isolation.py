from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))

import build_transcript_guided_source_bank as bank
import prepare_transcript_guided_source_isolation as contexts


class TranscriptGuidedSourceIsolationTests(unittest.TestCase):
    def test_every_candidate_is_transcript_and_scene_justified(self) -> None:
        self.assertEqual(len(bank.DECISIONS), 14)
        self.assertEqual(len({row["clip_id"] for row in bank.DECISIONS}), 14)
        for row in bank.DECISIONS:
            self.assertGreater(len(row["transcript"].split()), 4)
            self.assertTrue(row["speaker_role"])
            self.assertTrue(row["primary_emotion"])
            self.assertTrue(row["secondary_emotion"])
            self.assertTrue(row["dramatic_function"])
            self.assertGreaterEqual(row["intensity_1_to_5"], 1)
            self.assertLessEqual(row["intensity_1_to_5"], 5)
            self.assertIn("transcript", row["selection_reason"].casefold()) if row["clip_id"] == "benny_criminal_restrained_relief" else self.assertTrue(row["selection_reason"])

    def test_embeddings_are_locator_only(self) -> None:
        source = (BENCHMARKS / "prepare_transcript_guided_source_isolation.py").read_text(encoding="utf-8")
        self.assertIn('"speaker_embedding_role": "coarse_locator_only"', source)
        self.assertIn('"transcript_required_before_inclusion": True', source)
        self.assertIn('"complete_utterance_required": True', source)
        self.assertIn('"scene_continuity_required": True', source)

    def test_known_bad_seed_windows_are_rejected(self) -> None:
        rejected = {context_id for row in bank.REJECTIONS for context_id in row["context_ids"]}
        self.assertIn("criminal_code_06", rejected)
        self.assertIn("all_consuming_fire_01", rejected)
        self.assertIn("all_consuming_fire_02", rejected)
        self.assertIn("all_consuming_fire_06", rejected)

    def test_overlapping_hesitation_windows_are_consolidated(self) -> None:
        cluster = [
            row for row in bank.DECISIONS
            if set(row["context_ids"]) == {
                "hesitation_deviation_04",
                "hesitation_deviation_05",
                "hesitation_deviation_06",
            }
        ]
        self.assertEqual(len(cluster), 3)
        intervals = sorted((row["transcript_start_seconds"], row["transcript_end_seconds"]) for row in cluster)
        for left, right in zip(intervals, intervals[1:]):
            self.assertLessEqual(left[1], right[0])

    def test_sources_are_original_uploaded_recordings(self) -> None:
        self.assertEqual(set(contexts.SOURCES), {"criminal_code", "hesitation_deviation", "all_consuming_fire"})
        for spec in contexts.SOURCES.values():
            self.assertIn("CloudKit", spec["path"])
            self.assertEqual(len(spec["seeds"]), 6)


if __name__ == "__main__":
    unittest.main()
