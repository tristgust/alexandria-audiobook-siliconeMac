from __future__ import annotations

import unittest

from benchmarks.build_original_sin_overlap_complete_promotion_manifest_v2 import (
    build_manifest,
)


class CompletePromotionManifestV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = build_manifest()

    def test_complete_direct_counts(self) -> None:
        self.assertEqual(self.manifest["strict_clean_direct_substitution_count"], 81)
        self.assertEqual(self.manifest["restricted_direct_substitution_count"], 3)
        self.assertEqual(self.manifest["direct_substitution_count"], 84)
        self.assertEqual(len(self.manifest["direct_substitutions"]), 84)

    def test_direct_chunks_and_candidates_are_unique(self) -> None:
        rows = self.manifest["direct_substitutions"]
        self.assertEqual(len({row["chunk_id"] for row in rows}), 84)
        self.assertEqual(len({row["candidate_id"] for row in rows}), 84)

    def test_restricted_tier_is_separate_and_not_default(self) -> None:
        rows = self.manifest["restricted_direct_substitutions"]
        self.assertEqual({row["chunk_id"] for row in rows}, {1801, 3025, 4907})
        self.assertFalse(self.manifest["restricted_tier_default_inclusion"])
        self.assertTrue(self.manifest["requires_explicit_restricted_tier_confirmation"])
        self.assertTrue(all(not row["reference_bank_eligible"] for row in rows))

    def test_reference_bank_evidence_is_explicit(self) -> None:
        rows = self.manifest["reference_bank_evidence"]
        self.assertEqual(len(rows), 8)
        self.assertEqual(
            {row["chunk_id"] for row in rows},
            {1247, 3209, 561, 2398, 5462, 1731, 1939, 4443},
        )
        rolled_r = {row["chunk_id"] for row in rows if "rolled_r" in row["delivery_tags"]}
        self.assertEqual(rolled_r, {561, 1731})

    def test_reference_only_rows_are_not_direct_substitutions(self) -> None:
        reference_only = self.manifest["reference_only_evidence"]
        self.assertEqual({row["chunk_id"] for row in reference_only}, {1247, 3209})
        direct_chunks = {row["chunk_id"] for row in self.manifest["direct_substitutions"]}
        self.assertFalse({1247, 3209} & direct_chunks)

    def test_strict_overlap_is_exhausted(self) -> None:
        self.assertEqual(
            self.manifest["strict_overlap_expansion_status"],
            "completed_and_fully_dispositioned",
        )
        self.assertEqual(self.manifest["strict_overlap_untouched_bound_count"], 0)

    def test_terminal_failures_are_not_promoted(self) -> None:
        self.assertEqual(
            set(self.manifest["terminal_rejected_chunk_ids"]),
            {5055, 3116, 3016, 4715, 4888},
        )
        direct_chunks = {row["chunk_id"] for row in self.manifest["direct_substitutions"]}
        self.assertFalse(set(self.manifest["terminal_rejected_chunk_ids"]) & direct_chunks)

    def test_manifest_is_non_installing(self) -> None:
        self.assertFalse(self.manifest["installation_authorized"])
        self.assertFalse(self.manifest["production_changes"])
        self.assertTrue(self.manifest["requires_separate_promotion_receipt"])


if __name__ == "__main__":
    unittest.main()
