from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))

import import_source_isolation_review as workflow


class SourceIsolationImportTests(unittest.TestCase):
    def shortlist(self):
        return {
            "rows": [
                {
                    "candidate_id": "benny_criminal_code_01",
                    "target": "benny",
                    "source_title": "Criminal Code",
                    "source": "criminal-code.m4b",
                    "file": "candidate.mp3",
                    "sha256": "abc123",
                    "start_seconds": 12.5,
                    "end_seconds": 17.5,
                    "speaker_probability": 0.97,
                },
                {
                    "candidate_id": "doctor_all_consuming_fire_01",
                    "target": "doctor",
                    "source_title": "All-Consuming Fire",
                    "source": "all-consuming-fire.m4b",
                    "file": "doctor.mp3",
                    "sha256": "def456",
                    "start_seconds": 101.0,
                    "end_seconds": 106.0,
                    "speaker_probability": 0.93,
                },
            ]
        }

    def test_clean_target_character_becomes_approved_reference(self) -> None:
        review = {
            "round_id": workflow.ROUND_ID,
            "rows": [
                {
                    "candidate_id": "benny_criminal_code_01",
                    "speaker_match": "Definitely target speaker",
                    "performance_role": "Target character performance",
                    "dramatic_family": "Controlled anger",
                    "intensity_1_to_5": "4",
                    "clean_reference_audio": True,
                    "mine_nearby_audio": True,
                    "notes": "Useful restrained anger.",
                }
            ],
        }
        bank = workflow.build_bank(self.shortlist(), review)
        self.assertEqual(bank["approved_reference_count"], 1)
        row = bank["approved_references"][0]
        self.assertEqual(row["dramatic_family"], "controlled_anger")
        self.assertEqual(row["intensity_1_to_5"], 4)
        self.assertTrue(row["mine_nearby_audio"])
        self.assertFalse(row["production_promotion_allowed"])
        self.assertEqual(bank["missing_review_candidate_ids"], ["doctor_all_consuming_fire_01"])

    def test_wrong_character_or_dirty_audio_is_rejected(self) -> None:
        review = {
            "round_id": workflow.ROUND_ID,
            "rows": [
                {
                    "candidate_id": "doctor_all_consuming_fire_01",
                    "speaker_match": "Probably target speaker",
                    "performance_role": "Actor performing another character",
                    "dramatic_family": "Firm / authoritative",
                    "intensity_1_to_5": 4,
                    "clean_reference_audio": False,
                }
            ],
        }
        bank = workflow.build_bank(self.shortlist(), review)
        self.assertEqual(bank["approved_reference_count"], 0)
        self.assertEqual(bank["rejected_reference_count"], 1)
        reasons = set(bank["rejected_candidates"][0]["rejection_reasons"])
        self.assertEqual(
            reasons,
            {"not_target_character_performance", "audio_not_approved_clean"},
        )

    def test_unknown_candidate_is_rejected(self) -> None:
        review = {
            "round_id": workflow.ROUND_ID,
            "rows": [{"candidate_id": "unknown"}],
        }
        with self.assertRaises(workflow.IsolationImportError):
            workflow.build_bank(self.shortlist(), review)

    def test_unreviewed_or_incomplete_candidate_cannot_sneak_into_bank(self) -> None:
        review = {
            "round_id": workflow.ROUND_ID,
            "rows": [
                {
                    "candidate_id": "benny_criminal_code_01",
                    "speaker_match": "Definitely target speaker",
                    "performance_role": "Target character performance",
                    "dramatic_family": "Fearful / anxious",
                    "intensity_1_to_5": "",
                    "clean_reference_audio": True,
                }
            ],
        }
        bank = workflow.build_bank(self.shortlist(), review)
        self.assertEqual(bank["approved_reference_count"], 0)
        self.assertIn(
            "intensity_missing_or_invalid",
            bank["rejected_candidates"][0]["rejection_reasons"],
        )

    def test_validate_requires_no_automatic_production_promotion(self) -> None:
        payload = {
            "approved_references": [],
            "rejected_candidates": [],
            "production_promotion_allowed": True,
        }
        with self.assertRaises(workflow.IsolationImportError):
            workflow.validate_bank(payload)


if __name__ == "__main__":
    unittest.main()
