from __future__ import annotations

import unittest

from benchmarks.screen_original_sin_overlap_reference_repair_round import (
    CHARACTER_ORDER,
    SHORTLIST_ROUND_ID,
    V1_ROUND_ID,
    V2_ROUND_ID,
    build_screen,
)


def _candidate(character: str, treatment: str, *, eligible: bool, path: str) -> dict:
    return {
        "character": character,
        "book_speaker": character.upper(),
        "treatment": treatment,
        "path": path,
        "automatic_transcript": "exact words" if eligible else "changed words",
        "word_error_rate": 0.0 if eligible else 0.5,
        "first_word_present": True,
        "last_word_present": eligible,
        "transcript": "exact words",
    }


class OriginalSinRepairScreenTests(unittest.TestCase):
    def _keys(self):
        counts = {
            "Bernice Summerfield": 3,
            "The Doctor": 3,
            "Chris Cwej": 3,
            "Beltempest": 3,
            "Under-Sergeant": 2,
            "Computer": 3,
            "Doc Dantalion": 2,
            "Homeless Forsaken": 3,
            "Evan Claple": 3,
            "Shythe Shahid": 3,
            "Tobias Vaughn / Robot": 2,
        }
        candidates = {}
        index = 0
        for character in CHARACTER_ORDER:
            eligible_count = counts[character]
            for treatment_index in range(3):
                candidate_id = f"v2-{index:02d}"
                candidates[candidate_id] = _candidate(
                    character,
                    f"treatment-{treatment_index}",
                    eligible=treatment_index < eligible_count,
                    path=f"/tmp/{candidate_id}.wav",
                )
                index += 1
        v2 = {"round_id": V2_ROUND_ID, "candidates": candidates}
        v1 = {
            "round_id": V1_ROUND_ID,
            "candidates": {
                "prior-under": {
                    "character": "Under-Sergeant",
                    "book_speaker": "UNDER-SERGEANT",
                    "variant": "mel_roformer_vocal",
                    "path": "/tmp/prior-under.wav",
                    "automatic_transcript": "exact words",
                    "word_error_rate": 0.0,
                    "first_word_present": True,
                    "transcript": "exact words",
                }
            },
        }
        return v2, v1

    def test_screen_accounts_for_all_v2_candidates(self) -> None:
        v2, v1 = self._keys()
        report = build_screen(v2, v1)
        v2_rows = [row for row in report["candidates"] if row["source_round_id"] == V2_ROUND_ID]
        self.assertEqual(len(v2_rows), 33)

    def test_only_objective_candidates_enter_shortlist(self) -> None:
        v2, v1 = self._keys()
        report = build_screen(v2, v1)
        self.assertEqual(report["v2_objective_eligible_count"], 30)
        self.assertEqual(report["shortlist_candidate_count"], 30)
        self.assertTrue(
            all(row["objective_eligible"] for row in report["candidates"] if row["shortlisted"])
        )

    def test_prior_under_sergeant_candidate_is_preserved_but_not_carried(self) -> None:
        v2, v1 = self._keys()
        report = build_screen(v2, v1)
        self.assertEqual(report["prior_candidate_count"], 0)
        self.assertEqual(report["prior_exact_candidate_excluded_count"], 1)
        self.assertFalse(
            any(row["source_round_id"] == V1_ROUND_ID for row in report["candidates"])
        )

    def test_homeless_is_included_after_bounded_end_repair(self) -> None:
        v2, v1 = self._keys()
        report = build_screen(v2, v1)
        decision = next(
            row for row in report["character_decisions"] if row["character"] == "Homeless Forsaken"
        )
        self.assertEqual(decision["outcome"], "ready for blind repair review")
        self.assertEqual(len(decision["shortlist_candidate_ids"]), 3)

    def test_screen_declares_no_production_mutation(self) -> None:
        v2, v1 = self._keys()
        report = build_screen(v2, v1)
        self.assertEqual(report["round_id"], SHORTLIST_ROUND_ID)
        self.assertFalse(report["production_changes"])
        self.assertFalse(report["project_voice_config_changed"])
        self.assertFalse(report["project_chunks_changed"])


if __name__ == "__main__":
    unittest.main()
