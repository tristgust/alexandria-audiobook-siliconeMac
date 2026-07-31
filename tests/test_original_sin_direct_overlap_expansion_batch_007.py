from __future__ import annotations

from collections import Counter
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = json.loads(
    (ROOT / "benchmarks/original_sin_direct_overlap_expansion_batch_007_plan.json").read_text()
)
LEDGER = json.loads(
    (ROOT / "benchmarks/original_sin_strict_direct_overlap_ledger_v2.json").read_text()
)


class ExpansionBatch007Tests(unittest.TestCase):
    def test_has_eighteen_unique_bound_chunks(self) -> None:
        selected = PLAN["selected_chunk_ids"]
        self.assertEqual(len(selected), 18)
        self.assertEqual(len(set(selected)), 18)
        rows = {row["chunk_id"]: row for row in LEDGER["rows"]}
        for chunk_id in selected:
            self.assertIsNotNone(rows[chunk_id]["selected_window"])
            self.assertFalse(rows[chunk_id]["previously_direct_reviewed"])
            self.assertFalse(rows[chunk_id]["already_blind_approved"])

    def test_does_not_recycle_prior_broad_batch_chunks(self) -> None:
        prior: set[int] = set()
        for number in range(1, 7):
            path = ROOT / f"benchmarks/original_sin_direct_overlap_expansion_batch_{number:03d}_plan.json"
            payload = json.loads(path.read_text())
            prior.update(int(value) for value in payload["selected_chunk_ids"])
        self.assertFalse(set(PLAN["selected_chunk_ids"]) & prior)

    def test_character_balance(self) -> None:
        rows = {row["chunk_id"]: row for row in LEDGER["rows"]}
        counts = Counter(rows[chunk_id]["speaker"] for chunk_id in PLAN["selected_chunk_ids"])
        self.assertEqual(
            counts,
            Counter({"DOCTOR": 6, "BERNICE": 5, "ROZ FORRESTER": 5, "BELTEMPEST": 2}),
        )

    def test_corrected_timing_contract(self) -> None:
        self.assertEqual(PLAN["requested_segment_tail_seconds"], 0.30)
        self.assertEqual(PLAN["adjacent_speaker_guard_seconds"], 0.006)
        self.assertEqual(PLAN["appended_silence_milliseconds"], 100)

    def test_alignment_aliases_are_narrow(self) -> None:
        overrides = PLAN["line_alignment_overrides"]
        self.assertEqual(overrides["2231"]["word_aliases"], {"so": ["say"]})
        self.assertEqual(overrides["3451"]["word_aliases"], {"favour": ["favor"]})

    def test_no_production_changes(self) -> None:
        self.assertFalse(PLAN["production_changes"])


if __name__ == "__main__":
    unittest.main()
