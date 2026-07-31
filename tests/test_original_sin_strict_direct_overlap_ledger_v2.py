from __future__ import annotations

import unittest
from pathlib import Path

from benchmarks.build_original_sin_strict_direct_overlap_ledger_v2 import (
    DEFAULT_PROJECT,
    build_ledger,
)


class StrictDirectOverlapLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ledger = build_ledger(Path(DEFAULT_PROJECT))

    def test_reproduces_historical_strict_count(self) -> None:
        self.assertEqual(self.ledger["book_chunk_match_count"], 144)
        self.assertEqual(self.ledger["unique_quotation_count"], 142)

    def test_all_but_wrong_speaker_occurrence_are_bound(self) -> None:
        self.assertEqual(self.ledger["resolved_binding_count"], 143)
        self.assertEqual(self.ledger["excluded_binding_count"], 1)

    def test_repeated_lines_use_scene_context(self) -> None:
        rows = {row["chunk_id"]: row for row in self.ledger["rows"]}
        self.assertEqual(rows[696]["selected_window"]["segment_start"], 479)
        self.assertEqual(rows[4580]["selected_window"]["segment_start"], 990)
        self.assertEqual(rows[4366]["selected_window"]["segment_start"], 2017)

    def test_text_match_does_not_override_wrong_speaker_context(self) -> None:
        rows = {row["chunk_id"]: row for row in self.ledger["rows"]}
        self.assertIsNone(rows[1297]["selected_window"])
        self.assertEqual(rows[1297]["binding_basis"], "excluded_wrong_speaker_context")

    def test_existing_pilot_is_separate_from_strict_subset(self) -> None:
        self.assertEqual(self.ledger["promotion_manifest_direct_count"], 6)
        self.assertEqual(self.ledger["already_blind_approved_count"], 3)

    def test_prior_failed_direct_rounds_are_not_new_work(self) -> None:
        rows = {row["chunk_id"]: row for row in self.ledger["rows"]}
        self.assertTrue(rows[5207]["previously_direct_reviewed"])
        self.assertTrue(rows[3098]["previously_direct_reviewed"])
        self.assertTrue(rows[3908]["previously_direct_reviewed"])
        self.assertFalse(rows[2718]["previously_direct_reviewed"])

    def test_ledger_is_non_mutating(self) -> None:
        self.assertFalse(self.ledger["production_changes"])


if __name__ == "__main__":
    unittest.main()
