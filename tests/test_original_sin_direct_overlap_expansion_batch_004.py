from __future__ import annotations

from collections import Counter
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "benchmarks/original_sin_direct_overlap_expansion_batch_004_plan.json"
LEDGER = ROOT / "benchmarks/original_sin_strict_direct_overlap_ledger_v2.json"


class ExpansionBatch004Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = json.loads(PLAN.read_text(encoding="utf-8"))
        cls.ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        cls.rows = {row["chunk_id"]: row for row in cls.ledger["rows"]}

    def test_completed_batch_records_only_heard_lines_reviewed(self) -> None:
        selected = self.plan["selected_chunk_ids"]
        self.assertEqual(len(selected), 18)
        self.assertEqual(len(set(selected)), 18)
        for chunk_id in selected:
            if chunk_id != 1676:
                self.assertIsNotNone(self.rows[chunk_id]["selected_window"])
            self.assertEqual(
                self.rows[chunk_id]["previously_direct_reviewed"],
                chunk_id not in {1582, 3094},
            )
            self.assertFalse(self.rows[chunk_id]["already_blind_approved"])

    def test_character_balance(self) -> None:
        counts = Counter(self.rows[cid]["speaker"] for cid in self.plan["selected_chunk_ids"])
        self.assertEqual(set(counts.values()), {3})
        self.assertEqual(len(counts), 6)

    def test_corrected_timing_contract(self) -> None:
        contract = self.plan["voice_only_contract"]
        self.assertTrue(contract["segment_tail_required"])
        self.assertTrue(contract["deterministic_post_silence_required"])
        self.assertFalse(contract["adjacent_dialogue_allowed"])

    def test_no_production_changes(self) -> None:
        self.assertFalse(self.plan["production_changes"])


if __name__ == "__main__":
    unittest.main()
