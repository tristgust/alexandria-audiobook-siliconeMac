from __future__ import annotations

import json
import unittest
from pathlib import Path

from benchmarks.build_original_sin_overlap_promotion_manifest import build_manifest


ROOT = Path(__file__).resolve().parents[1]
PROJECT = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665"
)
PLAN = ROOT / "benchmarks/original_sin_overlap_promotion_plan_v1.json"


class PromotionManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = json.loads(PLAN.read_text())
        cls.manifest = build_manifest(PROJECT, cls.plan)

    def test_expected_counts(self) -> None:
        self.assertEqual(self.manifest["identity_anchor_count"], 12)
        self.assertEqual(self.manifest["adaptation_performance_reference_count"], 1)
        self.assertEqual(self.manifest["expressive_mode_count"], 8)
        self.assertEqual(self.manifest["direct_substitution_count"], 6)
        self.assertEqual(self.manifest["unresolved_character_count"], 4)

    def test_direct_chunks_are_unique(self) -> None:
        chunks = [row["chunk_id"] for row in self.manifest["direct_substitutions"]]
        self.assertEqual(len(chunks), len(set(chunks)))
        self.assertEqual(chunks, [405, 618, 1322, 1684, 2954, 4366])

    def test_no_promotable_expressive_route_used_fallback(self) -> None:
        for mode in self.manifest["expressive_modes"]:
            self.assertFalse(mode["primary"]["fallback_used"])
            self.assertTrue(all(not row["fallback_used"] for row in mode["alternates"]))

    def test_roz_fallback_is_restricted_evidence(self) -> None:
        row = next(
            mode
            for mode in self.manifest["expressive_modes"]
            if mode["character"] == "Roz Forrester"
        )
        self.assertEqual(len(row["restricted_fallback_evidence"]), 1)
        self.assertTrue(row["restricted_fallback_evidence"][0]["fallback_used"])

    def test_manifest_is_no_mutation(self) -> None:
        self.assertFalse(self.manifest["installation_authorized"])
        self.assertFalse(self.manifest["production_changes"])
        self.assertEqual(
            self.manifest["protected_project_hashes_before"],
            self.manifest["protected_project_hashes_after"],
        )


if __name__ == "__main__":
    unittest.main()
