from __future__ import annotations

from collections import Counter
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = json.loads((ROOT / "benchmarks/original_sin_direct_overlap_expansion_batch_008_plan.json").read_text())
LEDGER = json.loads((ROOT / "benchmarks/original_sin_strict_direct_overlap_ledger_v2.json").read_text())


class ExpansionBatch008Tests(unittest.TestCase):
    def test_has_sixteen_unique_bound_chunks(self) -> None:
        selected = PLAN["selected_chunk_ids"]
        self.assertEqual(len(selected), 16)
        self.assertEqual(len(set(selected)), 16)
        rows = {row["chunk_id"]: row for row in LEDGER["rows"]}
        objective_omissions = {811, 4610}
        reviewed = set(selected) - objective_omissions
        for chunk_id in selected:
            if chunk_id == 4610:
                self.assertIsNone(rows[chunk_id]["selected_window"])
                self.assertEqual(
                    rows[chunk_id]["binding_basis"],
                    "excluded_audio_text_mismatch",
                )
            elif chunk_id == 2840:
                self.assertIsNone(rows[chunk_id]["selected_window"])
                self.assertEqual(
                    rows[chunk_id]["binding_basis"],
                    "excluded_wrong_speaker_context",
                )
            else:
                self.assertIsNotNone(rows[chunk_id]["selected_window"])
            self.assertEqual(
                rows[chunk_id]["previously_direct_reviewed"],
                chunk_id in reviewed,
            )
            self.assertFalse(rows[chunk_id]["already_blind_approved"])

    def test_does_not_recycle_prior_broad_batch_chunks(self) -> None:
        prior: set[int] = set()
        for number in range(1, 8):
            path = ROOT / f"benchmarks/original_sin_direct_overlap_expansion_batch_{number:03d}_plan.json"
            prior.update(json.loads(path.read_text())["selected_chunk_ids"])
        self.assertFalse(set(PLAN["selected_chunk_ids"]) & prior)

    def test_character_balance(self) -> None:
        rows = {row["chunk_id"]: row for row in LEDGER["rows"]}
        counts = Counter(rows[chunk_id]["speaker"] for chunk_id in PLAN["selected_chunk_ids"])
        self.assertEqual(counts, Counter({"DOCTOR": 8, "BERNICE": 5, "ROZ FORRESTER": 3}))

    def test_exhausts_unreviewed_unattempted_bound_rows(self) -> None:
        selected = set(PLAN["selected_chunk_ids"])
        rows = {row["chunk_id"]: row for row in LEDGER["rows"]}
        self.assertTrue(all(rows[chunk_id]["previously_direct_attempted"] for chunk_id in selected))
        remaining = {
            row["chunk_id"]
            for row in LEDGER["rows"]
            if row["selected_window"]
            and not row["previously_direct_attempted"]
            and not row["previously_direct_reviewed"]
            and not row["already_blind_approved"]
        }
        self.assertEqual(remaining, set())

    def test_corrected_timing_contract(self) -> None:
        self.assertEqual(PLAN["requested_segment_tail_seconds"], 0.30)
        self.assertEqual(PLAN["adjacent_speaker_guard_seconds"], 0.006)
        self.assertEqual(PLAN["appended_silence_milliseconds"], 100)

    def test_no_production_changes(self) -> None:
        self.assertFalse(PLAN["production_changes"])


if __name__ == "__main__":
    unittest.main()
