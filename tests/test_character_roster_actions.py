from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from character_roster import (
    CharacterRosterSourceMismatchError,
    CharacterRosterValidationError,
    build_draft_roster,
    compute_draft_fingerprint,
    save_character_roster,
    validate_character_roster,
)
from character_roster_actions import (
    CharacterRosterActionError,
    CharacterRosterConflictError,
    apply_character_roster_action,
    approve_character_roster_file,
    build_approved_roster,
    mutate_character_roster_draft_file,
)
from tests.test_character_roster import CharacterRosterFixture


class CharacterRosterActionTests(
    unittest.TestCase,
    CharacterRosterFixture,
):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_path = self.root / "book.txt"
        self.source_path.write_text(self.SOURCE_TEXT, encoding="utf-8")
        self.source_snapshot = self.source(self.source_path)
        self.draft = self.draft(self.source_snapshot)
        self.source_fingerprint = self.source_snapshot["fingerprint"]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def action(self, action: str, **kwargs):
        return apply_character_roster_action(
            self.draft,
            expected_fingerprint=self.draft["draft_fingerprint"],
            source_fingerprint=self.source_fingerprint,
            source_text=self.SOURCE_TEXT,
            action=action,
            at_utc="2026-07-16T21:00:00Z",
            **kwargs,
        )

    def duplicate_draft(self):
        first = self.entry("THE DOCTOR", "The Doctor")
        second = self.entry("ROZ", "Roz")
        first["resolution_status"] = "duplicate_candidate"
        second["resolution_status"] = "duplicate_candidate"
        first["possible_duplicate_ids"] = [second["id"]]
        second["possible_duplicate_ids"] = [first["id"]]
        first["mistaken_merge_risk"] = True
        second["mistaken_merge_risk"] = True
        return build_draft_roster(
            source=self.source_snapshot,
            discovery=self.draft["discovery"],
            entries=[first, second],
            duplicate_candidates=[
                {
                    "entry_ids": [first["id"], second["id"]],
                    "reason": "Potential identity overlap.",
                    "confidence": 0.5,
                    "evidence": [
                        *copy.deepcopy(first["evidence"]),
                        *copy.deepcopy(second["evidence"]),
                    ],
                }
            ],
            source_text=self.SOURCE_TEXT,
        )

    def test_stale_fingerprint_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            CharacterRosterConflictError,
            "changed after this edit",
        ):
            apply_character_roster_action(
                self.draft,
                expected_fingerprint="stale",
                source_fingerprint=self.source_fingerprint,
                source_text=self.SOURCE_TEXT,
                action="confirm",
                entry_id=self.draft["entries"][0]["id"],
            )

    def test_source_mismatch_is_rejected(self) -> None:
        with self.assertRaises(CharacterRosterSourceMismatchError):
            apply_character_roster_action(
                self.draft,
                expected_fingerprint=self.draft["draft_fingerprint"],
                source_fingerprint="different",
                source_text=self.SOURCE_TEXT,
                action="confirm",
                entry_id=self.draft["entries"][0]["id"],
            )

    def test_rename_preserves_id_evidence_and_history(self) -> None:
        original = self.draft["entries"][0]
        updated = self.action(
            "rename",
            entry_id=original["id"],
            value="THE SEVENTH DOCTOR",
            display_name="The Seventh Doctor",
        )
        renamed = next(
            entry for entry in updated["entries"]
            if entry["id"] == original["id"]
        )
        self.assertEqual(renamed["canonical_name"], "THE SEVENTH DOCTOR")
        self.assertEqual(renamed["evidence"], original["evidence"])
        self.assertIn("THE DOCTOR", renamed["aliases"])
        self.assertEqual(updated["review_history"][-1]["action"], "rename")
        self.assertEqual(
            updated["review_history"][-1]["before_entries"][0]["id"],
            original["id"],
        )
        self.assertNotEqual(
            updated["draft_fingerprint"],
            self.draft["draft_fingerprint"],
        )

    def test_add_and_reject_alias_are_audited(self) -> None:
        entry_id = self.draft["entries"][0]["id"]
        added = self.action(
            "add_alias",
            entry_id=entry_id,
            value="DOCTOR",
        )
        self.assertIn("DOCTOR", added["entries"][0]["aliases"])
        rejected = apply_character_roster_action(
            added,
            expected_fingerprint=added["draft_fingerprint"],
            source_fingerprint=self.source_fingerprint,
            source_text=self.SOURCE_TEXT,
            action="reject_alias",
            entry_id=entry_id,
            value="DOCTOR",
            at_utc="2026-07-16T21:01:00Z",
        )
        self.assertNotIn("DOCTOR", rejected["entries"][0]["aliases"])
        self.assertEqual(
            [item["action"] for item in rejected["review_history"]],
            ["add_alias", "reject_alias"],
        )

    def test_keep_separate_removes_duplicate_relation_and_records_rejection(self) -> None:
        draft = self.duplicate_draft()
        first, second = draft["entries"]
        updated = apply_character_roster_action(
            draft,
            expected_fingerprint=draft["draft_fingerprint"],
            source_fingerprint=self.source_fingerprint,
            source_text=self.SOURCE_TEXT,
            action="keep_separate",
            entry_id=first["id"],
            other_entry_id=second["id"],
            reason="They appear together as separate people.",
            at_utc="2026-07-16T21:00:00Z",
        )
        self.assertEqual(updated["duplicate_candidates"], [])
        self.assertEqual(updated["entries"][0]["possible_duplicate_ids"], [])
        self.assertEqual(updated["entries"][1]["possible_duplicate_ids"], [])
        history = updated["review_history"][-1]
        self.assertEqual(history["action"], "keep_separate")
        self.assertEqual(set(history["entry_ids"]), {first["id"], second["id"]})

    def test_duplicate_candidates_must_be_decided_before_approval(self) -> None:
        draft = self.duplicate_draft()
        with self.assertRaisesRegex(
            CharacterRosterActionError,
            "merged or kept separate",
        ):
            build_approved_roster(
                draft,
                expected_fingerprint=draft["draft_fingerprint"],
                source_fingerprint=self.source_fingerprint,
                source_text=self.SOURCE_TEXT,
                acknowledged_unresolved=True,
            )

    def test_merge_preserves_identity_evidence_and_aliases(self) -> None:
        draft = self.duplicate_draft()
        primary, secondary = draft["entries"]
        updated = apply_character_roster_action(
            draft,
            expected_fingerprint=draft["draft_fingerprint"],
            source_fingerprint=self.source_fingerprint,
            source_text=self.SOURCE_TEXT,
            action="merge",
            entry_id=primary["id"],
            other_entry_id=secondary["id"],
            reason="Confirmed by source context.",
            at_utc="2026-07-16T21:00:00Z",
        )
        self.assertEqual(len(updated["entries"]), 1)
        merged = updated["entries"][0]
        self.assertEqual(merged["id"], primary["id"])
        self.assertIn(secondary["canonical_name"], merged["aliases"])
        self.assertEqual(len(merged["evidence"]), 2)
        self.assertEqual(updated["duplicate_candidates"], [])
        self.assertEqual(len(updated["review_history"][-1]["before_entries"]), 2)

        approved = build_approved_roster(
            updated,
            expected_fingerprint=updated["draft_fingerprint"],
            source_fingerprint=self.source_fingerprint,
            source_text=self.SOURCE_TEXT,
            acknowledged_unresolved=False,
            approved_at_utc="2026-07-16T21:10:00Z",
        )
        self.assertEqual(approved["approval_summary"]["merged_count"], 1)

    def test_mark_unresolved_requires_acknowledgment_on_approval(self) -> None:
        entry_id = self.draft["entries"][0]["id"]
        updated = self.action(
            "mark_unresolved",
            entry_id=entry_id,
            reason="Which incarnation is this?",
        )
        self.assertEqual(updated["entries"][0]["resolution_status"], "unresolved")
        self.assertEqual(len(updated["unresolved"]), 1)

        with self.assertRaisesRegex(
            CharacterRosterActionError,
            "explicit acknowledgment",
        ):
            build_approved_roster(
                updated,
                expected_fingerprint=updated["draft_fingerprint"],
                source_fingerprint=self.source_fingerprint,
                source_text=self.SOURCE_TEXT,
                acknowledged_unresolved=False,
            )

        approved = build_approved_roster(
            updated,
            expected_fingerprint=updated["draft_fingerprint"],
            source_fingerprint=self.source_fingerprint,
            source_text=self.SOURCE_TEXT,
            acknowledged_unresolved=True,
            approved_at_utc="2026-07-16T21:10:00Z",
        )
        self.assertTrue(
            approved["approval_summary"]["acknowledged_unresolved"]
        )

    def test_exclusion_preserves_evidence_and_removes_relations(self) -> None:
        entry = self.draft["entries"][2]
        updated = self.action(
            "exclude",
            entry_id=entry["id"],
            reason="Named object, not a roster character.",
        )
        self.assertNotIn(entry["id"], {item["id"] for item in updated["entries"]})
        self.assertEqual(updated["excluded_entities"][0]["evidence"], entry["evidence"])
        self.assertEqual(updated["review_history"][-1]["action"], "exclude")

    def test_review_operation_id_detects_history_tampering(self) -> None:
        updated = self.action(
            "add_alias",
            entry_id=self.draft["entries"][0]["id"],
            value="DOCTOR",
        )
        tampered = copy.deepcopy(updated)
        tampered["review_history"][0]["reason"] = "Altered history"
        tampered["draft_fingerprint"] = compute_draft_fingerprint(
            tampered
        )
        with self.assertRaisesRegex(
            CharacterRosterValidationError,
            "operation_id does not match",
        ):
            validate_character_roster(
                tampered,
                source_text=self.SOURCE_TEXT,
                expected_status="draft",
            )

    def test_unnamed_identity_must_be_renamed_before_confirmation(self) -> None:
        draft = copy.deepcopy(self.draft)
        entry = draft["entries"][0]
        entry["canonical_name"] = ""
        entry["resolution_status"] = "unnamed"
        entry["unresolved_questions"] = ["Who is this?"]
        draft["unresolved"] = [
            {
                "entry_id": entry["id"],
                "question": "Who is this?",
                "confidence": entry["confidence"],
            }
        ]
        draft["draft_fingerprint"] = __import__(
            "character_roster"
        ).compute_draft_fingerprint(draft)

        with self.assertRaisesRegex(
            CharacterRosterActionError,
            "renamed before confirmation",
        ):
            apply_character_roster_action(
                draft,
                expected_fingerprint=draft["draft_fingerprint"],
                source_fingerprint=self.source_fingerprint,
                source_text=self.SOURCE_TEXT,
                action="confirm",
                entry_id=entry["id"],
            )

    def test_first_action_upgrades_legacy_draft_fingerprint_shape(self) -> None:
        legacy = copy.deepcopy(self.draft)
        legacy.pop("review_history")
        legacy["draft_fingerprint"] = compute_draft_fingerprint(
            legacy,
            include_review_history=False,
        )
        updated = apply_character_roster_action(
            legacy,
            expected_fingerprint=legacy["draft_fingerprint"],
            source_fingerprint=self.source_fingerprint,
            source_text=self.SOURCE_TEXT,
            action="add_alias",
            entry_id=legacy["entries"][0]["id"],
            value="DOCTOR",
            at_utc="2026-07-16T21:00:00Z",
        )
        self.assertIn("review_history", updated)
        self.assertEqual(
            updated["review_history"][0]["action"],
            "add_alias",
        )
        self.assertNotEqual(
            updated["draft_fingerprint"],
            legacy["draft_fingerprint"],
        )

    def test_legacy_draft_can_be_approved_without_prior_edit(self) -> None:
        legacy = copy.deepcopy(self.draft)
        legacy.pop("review_history")
        legacy["draft_fingerprint"] = compute_draft_fingerprint(
            legacy,
            include_review_history=False,
        )
        approved = build_approved_roster(
            legacy,
            expected_fingerprint=legacy["draft_fingerprint"],
            source_fingerprint=self.source_fingerprint,
            source_text=self.SOURCE_TEXT,
            acknowledged_unresolved=False,
            approved_at_utc="2026-07-16T21:10:00Z",
        )
        self.assertEqual(approved["review_history"], [])
        self.assertEqual(approved["status"], "approved")

    def test_file_actions_are_atomic_and_approved_roster_is_not_overwritten(self) -> None:
        draft_path = self.root / "character_roster.draft.json"
        approved_path = self.root / "character_roster.json"
        save_character_roster(
            self.draft,
            draft_path,
            source_text=self.SOURCE_TEXT,
            expected_status="draft",
        )
        updated = mutate_character_roster_draft_file(
            draft_path=draft_path,
            source_text=self.SOURCE_TEXT,
            source_fingerprint=self.source_fingerprint,
            expected_fingerprint=self.draft["draft_fingerprint"],
            action="add_alias",
            entry_id=self.draft["entries"][0]["id"],
            value="DOCTOR",
            at_utc="2026-07-16T21:00:00Z",
        )
        approved = approve_character_roster_file(
            draft_path=draft_path,
            approved_path=approved_path,
            source_text=self.SOURCE_TEXT,
            source_fingerprint=self.source_fingerprint,
            expected_fingerprint=updated["draft_fingerprint"],
            acknowledged_unresolved=False,
            approved_at_utc="2026-07-16T21:10:00Z",
        )
        self.assertEqual(approved["status"], "approved")
        self.assertTrue(approved_path.exists())
        self.assertFalse(approved_path.with_name(approved_path.name + ".tmp").exists())

        with self.assertRaisesRegex(
            CharacterRosterConflictError,
            "already exists",
        ):
            approve_character_roster_file(
                draft_path=draft_path,
                approved_path=approved_path,
                source_text=self.SOURCE_TEXT,
                source_fingerprint=self.source_fingerprint,
                expected_fingerprint=updated["draft_fingerprint"],
                acknowledged_unresolved=False,
            )


if __name__ == "__main__":
    unittest.main()
