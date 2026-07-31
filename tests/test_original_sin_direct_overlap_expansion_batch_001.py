from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "benchmarks/original_sin_direct_overlap_expansion_batch_001_plan.json"
LEDGER = ROOT / "benchmarks/original_sin_strict_direct_overlap_ledger_v2.json"
ANSWER = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665/external_workflows/big_finish_overlap_reference_v1/"
    "direct_overlap_expansion_batch_001/private/answer-key.json"
)


class DirectOverlapExpansionBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = json.loads(PLAN.read_text(encoding="utf-8"))
        cls.ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        cls.answer = json.loads(ANSWER.read_text(encoding="utf-8"))

    def test_batch_has_eighteen_new_chunks(self) -> None:
        selected = self.plan["selected_chunk_ids"]
        self.assertEqual(len(selected), 18)
        self.assertEqual(len(set(selected)), 18)

    def test_batch_one_review_lifecycle_is_recorded(self) -> None:
        rows = {row["chunk_id"]: row for row in self.ledger["rows"]}
        reviewed = {
            int(candidate["chunk_id"])
            for candidate in self.answer["candidates"].values()
        }
        selected = set(self.plan["selected_chunk_ids"])
        self.assertEqual(len(reviewed), 14)
        for chunk_id in reviewed:
            self.assertTrue(rows[chunk_id]["previously_direct_reviewed"])
        later_reviewed = {3090}
        for chunk_id in selected - reviewed:
            self.assertEqual(
                rows[chunk_id]["previously_direct_reviewed"],
                chunk_id in later_reviewed,
            )

    def test_untreated_source_mix_is_not_a_candidate(self) -> None:
        self.assertEqual(
            self.plan["treatments"],
            ["mel_roformer_vocal", "mossformer2_source_mix"],
        )

    def test_voice_only_contract_is_strict(self) -> None:
        contract = self.plan["voice_only_contract"]
        self.assertFalse(contract["adjacent_dialogue_allowed"])
        self.assertFalse(contract["background_music_allowed"])
        self.assertFalse(contract["sound_effects_allowed"])
        self.assertFalse(contract["separator_artifacts_allowed"])
        self.assertTrue(contract["human_blind_review_required"])

    def test_intercom_line_is_excluded_before_review(self) -> None:
        exclusions = {
            item["chunk_id"]: item["reason"]
            for item in self.plan["known_voice_only_exclusions"]
        }
        self.assertIn(2002, exclusions)
        self.assertIn("intercom", exclusions[2002].casefold())

    def test_no_production_changes(self) -> None:
        self.assertFalse(self.plan["production_changes"])


if __name__ == "__main__":
    unittest.main()
