from __future__ import annotations

from collections import Counter
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = json.loads((ROOT / "benchmarks/original_sin_direct_overlap_expansion_batch_006_plan.json").read_text())
LEDGER = json.loads((ROOT / "benchmarks/original_sin_strict_direct_overlap_ledger_v2.json").read_text())
ANSWER = json.loads(
    Path(
        "/Users/tristan/Library/Application Support/Alexandria/Projects/"
        "original-sin--e6286665/external_workflows/big_finish_overlap_reference_v1/"
        "direct_overlap_expansion_batch_006/private/answer-key.json"
    ).read_text()
)


class ExpansionBatch006Tests(unittest.TestCase):
    def test_completed_batch_lifecycle_is_truthful(self) -> None:
        selected = PLAN["selected_chunk_ids"]
        self.assertEqual(len(selected), 18)
        self.assertEqual(len(set(selected)), 18)
        rows = {row["chunk_id"]: row for row in LEDGER["rows"]}
        heard = {int(row["chunk_id"]) for row in ANSWER["candidates"].values()}
        excluded_after_evidence = {4580, 864, 3948, 626}
        for chunk_id in selected:
            self.assertTrue(rows[chunk_id]["previously_direct_attempted"])
            self.assertEqual(rows[chunk_id]["previously_direct_reviewed"], chunk_id in heard)
            self.assertFalse(rows[chunk_id]["already_blind_approved"])
            if chunk_id in excluded_after_evidence:
                self.assertIsNone(rows[chunk_id]["selected_window"])
            else:
                self.assertIsNotNone(rows[chunk_id]["selected_window"])

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
