from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "benchmarks" / "original_sin_overlap_character_coverage_ledger_v1.json"


class OriginalSinCharacterCoverageLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        cls.characters = cls.ledger["characters"]

    def test_roster_has_nineteen_unique_character_identities(self) -> None:
        self.assertEqual(self.ledger["roster_count"], 19)
        names = [row["character"] for row in self.characters]
        self.assertEqual(len(names), 19)
        self.assertEqual(len(set(names)), 19)

    def test_summary_accounts_for_entire_roster(self) -> None:
        summary = self.ledger["summary_before_v3_review"]
        self.assertEqual(summary["covered"], 6)
        self.assertEqual(summary["pending_generation_review"], 10)
        self.assertEqual(summary["blocked_identity_source"], 3)
        self.assertEqual(
            summary["covered"]
            + summary["pending_generation_review"]
            + summary["blocked_identity_source"],
            19,
        )

    def test_bot_label_has_explicit_two_identity_split(self) -> None:
        security = next(row for row in self.characters if row["character"] == "Securitybot")
        tobias = next(row for row in self.characters if row["character"] == "Tobias Vaughn / Robot")
        self.assertEqual(security["speaker_split"]["chunk_ids"], [491, 493, 495, 497, 501, 503, 618, 622, 634])
        self.assertEqual(tobias["speaker_split"]["chunk_ids"], [1341, 3669, 3674, 3676, 3680, 3682, 3684])
        self.assertTrue(set(security["speaker_split"]["chunk_ids"]).isdisjoint(tobias["speaker_split"]["chunk_ids"]))

    def test_unresolved_identities_require_salvage_then_generation(self) -> None:
        unresolved = {
            row["character"]: row
            for row in self.characters
            if row["coverage_status"] == "blocked_identity_source"
        }
        self.assertEqual(set(unresolved), {"Doc Dantalion", "Homeless Forsaken", "Shythe Shahid"})
        for row in unresolved.values():
            self.assertIn("then run", row["next_required_step"])

    def test_no_character_is_declared_covered_below_its_mode_target(self) -> None:
        for row in self.characters:
            if row["coverage_status"] not in {"covered", "covered_restricted"}:
                continue
            self.assertGreaterEqual(len(row["accepted_modes"]), row["required_mode_count"])


if __name__ == "__main__":
    unittest.main()
