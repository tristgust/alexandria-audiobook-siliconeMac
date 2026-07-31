from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "benchmarks/original_sin_direct_overlap_expansion_batch_002_plan.json"
LEDGER = ROOT / "benchmarks/original_sin_strict_direct_overlap_ledger_v2.json"


class DirectOverlapExpansionBatch002Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = json.loads(PLAN.read_text(encoding="utf-8"))
        cls.ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        cls.rows = {row["chunk_id"]: row for row in cls.ledger["rows"]}

    def test_batch_has_eighteen_new_chunks(self) -> None:
        selected = self.plan["selected_chunk_ids"]
        self.assertEqual(len(selected), 18)
        self.assertEqual(len(set(selected)), 18)
        for chunk_id in selected:
            self.assertFalse(self.rows[chunk_id]["previously_direct_reviewed"])
            self.assertFalse(self.rows[chunk_id]["already_blind_approved"])

    def test_character_balance_caps_two_per_speaker(self) -> None:
        counts = Counter(
            self.rows[chunk_id]["speaker"]
            for chunk_id in self.plan["selected_chunk_ids"]
        )
        self.assertLessEqual(max(counts.values()), 2)
        self.assertGreaterEqual(len(counts), 11)

    def test_under_sergeant_intercom_is_included(self) -> None:
        self.assertIn(2002, self.plan["selected_chunk_ids"])
        allowance = self.plan["character_correct_effect_allowances"]["2002"]
        self.assertTrue(allowance["allowed"])
        self.assertEqual(allowance["kind"], "character_correct_intercom")

    def test_narrow_transcript_exceptions_are_declared(self) -> None:
        overrides = self.plan["line_alignment_overrides"]
        self.assertEqual(
            overrides["2002"]["word_aliases"],
            {"fuss": ["bus"]},
        )
        self.assertEqual(
            overrides["1247"]["word_aliases"],
            {"Hith": ["hit"]},
        )
        self.assertIn("gonna let", overrides["5037"]["accepted_transcripts"][0])
        self.assertEqual(
            overrides["5037"]["policy"],
            "explicitly_approved_performance_variant",
        )

    def test_timing_contract_is_corrected(self) -> None:
        contract = self.plan["voice_only_contract"]
        self.assertTrue(contract["segment_tail_required"])
        self.assertTrue(contract["deterministic_post_silence_required"])
        self.assertFalse(contract["adjacent_dialogue_allowed"])

    def test_no_production_changes(self) -> None:
        self.assertFalse(self.plan["production_changes"])


if __name__ == "__main__":
    unittest.main()
