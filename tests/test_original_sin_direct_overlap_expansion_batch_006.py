from __future__ import annotations

from collections import Counter
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = json.loads((ROOT / "benchmarks/original_sin_direct_overlap_expansion_batch_006_plan.json").read_text())
LEDGER = json.loads((ROOT / "benchmarks/original_sin_strict_direct_overlap_ledger_v2.json").read_text())


class ExpansionBatch006Tests(unittest.TestCase):
    def test_has_eighteen_unique_new_chunks(self) -> None:
        selected = PLAN["selected_chunk_ids"]
        self.assertEqual(len(selected), 18)
        self.assertEqual(len(set(selected)), 18)
        rows = {row["chunk_id"]: row for row in LEDGER["rows"]}
        for chunk_id in selected:
            self.assertIsNotNone(rows[chunk_id]["selected_window"])
            self.assertFalse(rows[chunk_id]["previously_direct_reviewed"])
            self.assertFalse(rows[chunk_id]["already_blind_approved"])

    def test_includes_remaining_one_off_speakers(self) -> None:
        self.assertIn(1094, PLAN["selected_chunk_ids"])
        self.assertIn(3948, PLAN["selected_chunk_ids"])
        self.assertIn(5273, PLAN["selected_chunk_ids"])

    def test_character_balance(self) -> None:
        rows = {row["chunk_id"]: row for row in LEDGER["rows"]}
        counts = Counter(rows[cid]["speaker"] for cid in PLAN["selected_chunk_ids"])
        self.assertEqual(len(counts), 7)
        self.assertLessEqual(max(counts.values()), 5)

    def test_biochip_alias_is_bounded(self) -> None:
        override = PLAN["line_alignment_overrides"]["1094"]
        self.assertEqual(override["word_aliases"], {"biochip": ["biochem"]})
        self.assertEqual(override["policy"], "bounded_recognizer_alias")

    def test_corrected_timing_contract(self) -> None:
        self.assertEqual(PLAN["requested_segment_tail_seconds"], 0.30)
        self.assertEqual(PLAN["adjacent_speaker_guard_seconds"], 0.006)
        self.assertEqual(PLAN["appended_silence_milliseconds"], 100)

    def test_no_production_changes(self) -> None:
        self.assertFalse(PLAN["production_changes"])


if __name__ == "__main__":
    unittest.main()
