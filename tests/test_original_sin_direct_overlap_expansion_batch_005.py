from __future__ import annotations

from collections import Counter
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = json.loads((ROOT / "benchmarks/original_sin_direct_overlap_expansion_batch_005_plan.json").read_text())
LEDGER = json.loads((ROOT / "benchmarks/original_sin_strict_direct_overlap_ledger_v2.json").read_text())


class ExpansionBatch005Tests(unittest.TestCase):
    def test_has_eighteen_unique_new_chunks(self):
        selected = PLAN["selected_chunk_ids"]
        self.assertEqual(len(selected), 18)
        self.assertEqual(len(set(selected)), 18)
        rows = {row["chunk_id"]: row for row in LEDGER["rows"]}
        for chunk_id in selected:
            self.assertIsNotNone(rows[chunk_id]["selected_window"])
            self.assertFalse(rows[chunk_id]["previously_direct_reviewed"])
            self.assertFalse(rows[chunk_id]["already_blind_approved"])

    def test_character_balance(self):
        rows = {row["chunk_id"]: row for row in LEDGER["rows"]}
        counts = Counter(rows[chunk_id]["speaker"] for chunk_id in PLAN["selected_chunk_ids"])
        self.assertLessEqual(max(counts.values()), 3)
        self.assertGreaterEqual(len(counts), 7)

    def test_zebulon_alias_is_bounded(self):
        override = PLAN["line_alignment_overrides"]["3090"]
        self.assertEqual(override["word_aliases"], {"frighten": ["threaten"]})
        self.assertEqual(override["policy"], "bounded_recognizer_alias")

    def test_corrected_timing_contract(self):
        self.assertEqual(PLAN["requested_segment_tail_seconds"], 0.30)
        self.assertEqual(PLAN["adjacent_speaker_guard_seconds"], 0.006)
        self.assertEqual(PLAN["appended_silence_milliseconds"], 100)

    def test_no_production_changes(self):
        self.assertFalse(PLAN["production_changes"])


if __name__ == "__main__":
    unittest.main()
