from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = json.loads((ROOT / "benchmarks/original_sin_direct_overlap_expansion_batch_003_plan.json").read_text())
LEDGER = json.loads((ROOT / "benchmarks/original_sin_strict_direct_overlap_ledger_v2.json").read_text())


class ExpansionBatch003Tests(unittest.TestCase):
    def test_has_eighteen_unique_new_chunks(self) -> None:
        selected = PLAN["selected_chunk_ids"]
        self.assertEqual(len(selected), 18)
        self.assertEqual(len(set(selected)), 18)
        rows = {row["chunk_id"]: row for row in LEDGER["rows"]}
        for chunk_id in selected:
            self.assertTrue(rows[chunk_id]["selected_window"])
            self.assertFalse(rows[chunk_id]["previously_direct_reviewed"])
            self.assertFalse(rows[chunk_id]["already_blind_approved"])

    def test_batch_is_reasonably_character_balanced(self) -> None:
        rows = {row["chunk_id"]: row for row in LEDGER["rows"]}
        counts = Counter(rows[chunk_id]["speaker"] for chunk_id in PLAN["selected_chunk_ids"])
        self.assertLessEqual(max(counts.values()), 3)
        self.assertGreaterEqual(len(counts), 7)

    def test_corrected_timing_contract(self) -> None:
        self.assertEqual(PLAN["requested_segment_tail_seconds"], 0.30)
        self.assertEqual(PLAN["adjacent_speaker_guard_seconds"], 0.006)
        self.assertEqual(PLAN["appended_silence_milliseconds"], 100)

    def test_no_production_changes(self) -> None:
        self.assertFalse(PLAN["production_changes"])


if __name__ == "__main__":
    unittest.main()
