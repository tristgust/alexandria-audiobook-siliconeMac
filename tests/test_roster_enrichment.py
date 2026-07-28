from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from roster_enrichment import (
    RosterEnrichmentError,
    clear_plan,
    load_plan,
    save_plan,
    update_plan,
)


class RosterEnrichmentPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_plan_defaults_relationships_to_included_and_tracks_checked_enrichment(self) -> None:
        plan = save_plan(
            root_dir=self.root,
            candidate_id="structured_fixture",
            draft_fingerprint="d" * 64,
            create_designed_voice_profiles=True,
            discover_visual_details=True,
            created_at_utc="2026-07-27T00:00:00Z",
        )
        self.assertTrue(plan["relationships_included"])
        self.assertTrue(plan["options"]["create_designed_voice_profiles"])
        self.assertTrue(plan["options"]["discover_visual_details"])
        self.assertEqual(plan["state"], "pending_roster_approval")
        self.assertEqual(
            plan["steps"]["relationships"]["state"],
            "included_in_roster_draft",
        )
        self.assertEqual(load_plan(self.root), plan)

    def test_plan_update_activates_after_approval_without_losing_options(self) -> None:
        before = save_plan(
            root_dir=self.root,
            candidate_id="structured_fixture",
            draft_fingerprint="d" * 64,
            create_designed_voice_profiles=False,
            discover_visual_details=True,
            created_at_utc="2026-07-27T00:00:00Z",
        )
        after = update_plan(
            root_dir=self.root,
            changes={
                "state": "ready",
                "approved_roster_fingerprint": "a" * 64,
                "steps": {"relationships": {"state": "complete", "required": True}},
            },
        )
        self.assertNotEqual(before["plan_fingerprint"], after["plan_fingerprint"])
        self.assertFalse(after["options"]["create_designed_voice_profiles"])
        self.assertTrue(after["options"]["discover_visual_details"])
        self.assertEqual(after["steps"]["relationships"]["state"], "complete")

    def test_invalid_or_missing_plan_fails_closed(self) -> None:
        with self.assertRaisesRegex(RosterEnrichmentError, "No pending"):
            update_plan(root_dir=self.root, changes={"state": "ready"})
        self.assertFalse(clear_plan(self.root))
        save_plan(
            root_dir=self.root,
            candidate_id="structured_fixture",
            draft_fingerprint="d" * 64,
            create_designed_voice_profiles=True,
            discover_visual_details=False,
            created_at_utc="2026-07-27T00:00:00Z",
        )
        self.assertTrue(clear_plan(self.root))
        self.assertIsNone(load_plan(self.root))


if __name__ == "__main__":
    unittest.main()
