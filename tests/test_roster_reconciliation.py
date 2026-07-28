from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from character_roster import (
    build_draft_roster,
    read_character_roster,
    save_character_roster,
)
from character_roster_actions import (
    approve_character_roster_file,
    replace_approved_character_roster_file,
)
from external_workflows import store_structured_result_candidate
from generation_state import fingerprint_text, fingerprint_value
from roster_import_reconciliation import (
    apply_issue_focused_roster_import_reconciliation,
    build_issue_focused_roster_import_reconciliation,
    get_pending_issue_focused_roster_reconciliation,
)
from roster_reconciliation import inspect_roster_reconciliation_project


class RosterReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = (
            "The Doctor entered. The UNIT soldiers waited. "
            "A narrator described the room. Run, said the Doctor. "
            "An unknown officer watched."
        )
        self.source_path = self.root / "book.txt"
        self.source_path.write_text(self.source, encoding="utf-8")
        self.source_snapshot = {
            "path": str(self.source_path),
            "basename": self.source_path.name,
            "fingerprint": fingerprint_text(self.source),
            "character_count": len(self.source),
        }
        self.draft_path = self.root / "character_roster.draft.json"
        self.approved_path = self.root / "character_roster.json"
        self.history_root = self.root / "character_roster_history"
        self.doctor_id = "character_11111111111111111111"
        self._prepare_approved_roster()
        self.result = self._imported_result()
        self.candidate = self._store_candidate(self.result)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _native_evidence(self, quote: str, category: str) -> dict:
        start = self.source.index(quote)
        end = start + len(quote)
        return {
            "source_quote": quote,
            "source_location": f"characters {start}-{end}",
            "start_char": start,
            "end_char": end,
            "passage_index": 1,
            "entry_index": None,
            "batch_index": 1,
            "category": category,
            "confidence": 1.0,
            "basis": "explicit",
        }

    def _evidence(self, quote: str, category: str) -> dict:
        start = self.source.index(quote)
        return {
            "quote": quote,
            "start_char": start,
            "end_char": start + len(quote),
            "category": category,
            "confidence": 1.0,
            "basis": "explicit",
        }

    def _entry(self) -> dict:
        evidence = self._native_evidence("The Doctor", "name")
        return {
            "id": self.doctor_id,
            "canonical_name": "THE DOCTOR",
            "display_name": "The Doctor",
            "entity_kind": "character",
            "speaking_status": "speaker",
            "titles": ["Doctor"],
            "aliases": [],
            "nicknames": [],
            "pronouns": [],
            "species": [],
            "relationships": [],
            "first_evidence_location": evidence["source_location"],
            "additional_evidence_locations": [],
            "confidence": 0.95,
            "resolution_status": "resolved",
            "possible_duplicate_ids": [],
            "mistaken_merge_risk": False,
            "unresolved_questions": [],
            "evidence": [evidence],
            "voice_clues": [],
            "sample_lines": [],
        }

    def _prepare_approved_roster(self) -> None:
        draft = build_draft_roster(
            source=self.source_snapshot,
            discovery={
                "created_at_utc": "2026-07-20T12:00:00Z",
                "model_name": "test-model",
                "backend": "test",
                "generation_fingerprint": "a" * 64,
                "batch_count": 1,
                "completed_batches": 1,
            },
            entries=[self._entry()],
            source_text=self.source,
        )
        save_character_roster(
            draft,
            self.draft_path,
            source_text=self.source,
            expected_status="draft",
        )
        approve_character_roster_file(
            draft_path=self.draft_path,
            approved_path=self.approved_path,
            source_text=self.source,
            source_fingerprint=self.source_snapshot["fingerprint"],
            expected_fingerprint=draft["draft_fingerprint"],
            acknowledged_unresolved=False,
            approved_at_utc="2026-07-20T12:01:00Z",
        )

    def _entity(
        self,
        *,
        seed: str,
        canonical_name: str,
        display_name: str,
        entity_kind: str,
        speaking_status: str,
        aliases: list[str],
        resolution_status: str,
        evidence: list[dict],
        unresolved_questions: list[str] | None = None,
    ) -> dict:
        return {
            "identity_seed": seed,
            "canonical_name": canonical_name,
            "display_name": display_name,
            "entity_kind": entity_kind,
            "speaking_status": speaking_status,
            "titles": [],
            "aliases": aliases,
            "nicknames": [],
            "pronouns": [],
            "species": [],
            "relationships": [],
            "voice_clues": [],
            "sample_lines": [],
            "confidence": 0.9,
            "resolution_status": resolution_status,
            "unresolved_questions": unresolved_questions or [],
            "evidence": evidence,
        }

    def _imported_result(self) -> dict:
        return {
            "entities": [
                self._entity(
                    seed="speaker:narrator",
                    canonical_name="Narrator",
                    display_name="Narrator",
                    entity_kind="narrator_role",
                    speaking_status="narrator",
                    aliases=["NARRATOR"],
                    resolution_status="resolved",
                    evidence=[
                        self._evidence("The UNIT soldiers waited.", "other")
                    ],
                ),
                self._entity(
                    seed="speaker:doctor",
                    canonical_name="THE DOCTOR",
                    display_name="The Doctor",
                    entity_kind="character",
                    speaking_status="speaker",
                    aliases=["Doctor"],
                    resolution_status="resolved",
                    evidence=[
                        self._evidence("The Doctor", "name"),
                        self._evidence("Run,", "speaking"),
                    ],
                ),
                self._entity(
                    seed="group:unit-soldiers",
                    canonical_name="UNIT soldiers",
                    display_name="UNIT soldiers",
                    entity_kind="group",
                    speaking_status="non_speaker",
                    aliases=["UNIT"],
                    resolution_status="resolved",
                    evidence=[self._evidence("UNIT soldiers", "name")],
                ),
                self._entity(
                    seed="unknown:officer",
                    canonical_name="Unknown officer",
                    display_name="Unknown officer",
                    entity_kind="unknown",
                    speaking_status="uncertain",
                    aliases=[],
                    resolution_status="unresolved",
                    evidence=[self._evidence("unknown officer", "other")],
                    unresolved_questions=["Is this officer named later?"],
                ),
            ],
            "warnings": ["One identity remains intentionally unresolved."],
        }

    def _store_candidate(self, result: dict) -> dict:
        return store_structured_result_candidate(
            root_dir=self.root,
            validated={
                "handoff_id": "handoff_roster_issue_test_1",
                "task_type": "roster_discovery",
                "result_fingerprint": fingerprint_value(result),
                "review": {
                    "root_type": "object",
                    "item_count": len(result["entities"]),
                    "source_fingerprint_verified": True,
                    "artifact_fingerprints_verified": [],
                },
                "result": copy.deepcopy(result),
            },
            handoff={
                "manifest": {
                    "source_fingerprint": self.source_snapshot["fingerprint"],
                    "artifact_fingerprints": {},
                },
                "input": {},
            },
            created_at_utc="2026-07-20T12:02:00Z",
        )

    def _focused(self) -> dict:
        result = get_pending_issue_focused_roster_reconciliation(
            root_dir=self.root,
            source_snapshot=self.source_snapshot,
            source_text=self.source,
            draft_path=self.draft_path,
            approved_path=self.approved_path,
        )
        self.assertIsNotNone(result)
        assert result is not None
        return result

    def test_safe_merges_and_additions_stay_out_of_issue_queue(self) -> None:
        focused = self._focused()
        self.assertEqual(focused["summary"]["safe_change_count"], 2)
        self.assertEqual(focused["summary"]["safe_merge_count"], 1)
        self.assertEqual(focused["summary"]["safe_addition_count"], 1)
        self.assertEqual(focused["summary"]["issue_count"], 2)
        self.assertEqual(
            {item["canonical_name"] for item in focused["safe_changes"]},
            {"THE DOCTOR", "UNIT soldiers"},
        )
        self.assertEqual(
            {item["code"] for item in focused["issues"]},
            {"invalid_evidence", "unresolved_identity"},
        )

    def test_issue_focused_apply_needs_decisions_only_for_displayed_issues(self) -> None:
        focused = self._focused()
        issue_decisions = [
            {
                "import_id": item["import_id"],
                "action": "unresolved",
                "current_entry_id": None,
            }
            for item in focused["issues"]
        ]
        result = apply_issue_focused_roster_import_reconciliation(
            root_dir=self.root,
            candidate_id=focused["candidate_id"],
            expected_result_fingerprint=focused["result_fingerprint"],
            expected_current_kind=focused["current_kind"],
            expected_current_fingerprint=focused["current_fingerprint"],
            issue_decisions=issue_decisions,
            source_snapshot=self.source_snapshot,
            source_text=self.source,
            draft_path=self.draft_path,
            approved_path=self.approved_path,
            applied_at_utc="2026-07-20T12:03:00Z",
        )
        self.assertEqual(result["status"], "transferred")
        self.assertEqual(
            result["application"]["decision_summary"],
            {
                "mode": "issue_focused",
                "safe_change_count": 2,
                "operator_issue_count": 2,
            },
        )
        draft = read_character_roster(
            self.draft_path,
            source_text=self.source,
            expected_status="draft",
        )
        self.assertEqual(len(draft["entries"]), 4)
        doctor = next(item for item in draft["entries"] if item["id"] == self.doctor_id)
        self.assertEqual(len(doctor["evidence"]), 2)
        self.assertTrue(
            any(item["canonical_name"] == "UNIT soldiers" for item in draft["entries"])
        )
        self.assertEqual(
            sum(item["resolution_status"] == "unresolved" for item in draft["entries"]),
            2,
        )

    def test_unresolved_identity_with_one_exact_match_can_confirm_merge(self) -> None:
        full = {
            "status": "pending",
            "candidate_id": "candidate_exact_unresolved_match",
            "result_fingerprint": "r" * 64,
            "current_kind": "approved",
            "current_fingerprint": "c" * 64,
            "current_entries": [],
            "source": self.source_snapshot,
            "warnings": [],
            "summary": {},
            "observations": [
                {
                    "import_id": "imported_exact_unresolved_match",
                    "canonical_name": "Unnamed Doctor",
                    "display_name": "The Doctor",
                    "aliases": [],
                    "nicknames": [],
                    "entry": {
                        "id": "character_imported_unresolved",
                        "canonical_name": "Unnamed Doctor",
                        "display_name": "The Doctor",
                        "resolution_status": "unresolved",
                    },
                    "native_semantic_status": "valid",
                    "repaired_evidence_count": 0,
                    "invalid_evidence_count": 0,
                    "resolution_status": "unnamed",
                    "current_matches": [
                        {
                            "id": self.doctor_id,
                            "canonical_name": "THE DOCTOR",
                            "display_name": "The Doctor",
                        }
                    ],
                    "proposed_action": "merge",
                    "proposed_current_entry_id": self.doctor_id,
                    "proposal_reason": "One current identity matches exactly.",
                }
            ],
        }
        focused = build_issue_focused_roster_import_reconciliation(full)
        self.assertEqual(len(focused["issues"]), 1)
        issue = focused["issues"][0]
        self.assertEqual(issue["proposed_action"], "merge")
        self.assertIn("merge", issue["allowed_actions"])
        self.assertEqual(issue["proposed_current_entry_id"], self.doctor_id)

    def test_repaired_evidence_and_colliding_additions_are_operator_issues(self) -> None:
        focused = self._focused()
        synthetic = copy.deepcopy(focused)
        repaired = copy.deepcopy(synthetic["safe_changes"][1])
        observation = next(
            item
            for item in get_pending_issue_focused_roster_reconciliation(
                root_dir=self.root,
                source_snapshot=self.source_snapshot,
                source_text=self.source,
                draft_path=self.draft_path,
                approved_path=self.approved_path,
            )["safe_changes"]
            if item["canonical_name"] == "UNIT soldiers"
        )
        self.assertEqual(observation["action"], "add")

        full = {
            "status": "pending",
            "candidate_id": "candidate_synthetic",
            "result_fingerprint": "r" * 64,
            "current_kind": "none",
            "current_fingerprint": None,
            "current_entries": [],
            "source": self.source_snapshot,
            "warnings": [],
            "summary": {},
            "observations": [],
        }
        base_entry = {
            "id": "character_aaaaaaaaaaaaaaaaaaaa",
            "canonical_name": "New Guard",
            "display_name": "New Guard",
            "entity_kind": "character",
            "speaking_status": "speaker",
            "titles": [],
            "aliases": ["Guard"],
            "nicknames": [],
            "pronouns": [],
            "species": [],
            "relationships": [],
            "first_evidence_location": "characters 0-3",
            "additional_evidence_locations": [],
            "confidence": 0.9,
            "resolution_status": "resolved",
            "possible_duplicate_ids": [],
            "mistaken_merge_risk": False,
            "unresolved_questions": [],
            "evidence": [self._native_evidence("The Doctor", "name")],
            "voice_clues": [],
            "sample_lines": [],
        }
        for index in range(2):
            entry = copy.deepcopy(base_entry)
            entry["id"] = f"character_{index + 1:020d}"
            full["observations"].append(
                {
                    "import_id": f"imported_{index + 1:024d}",
                    "canonical_name": f"New Guard {index + 1}",
                    "display_name": f"New Guard {index + 1}",
                    "aliases": ["Guard"],
                    "nicknames": [],
                    "entry": entry,
                    "native_semantic_status": "valid",
                    "repaired_evidence_count": 1 if index == 0 else 0,
                    "invalid_evidence_count": 0,
                    "resolution_status": "resolved",
                    "current_matches": [],
                    "proposed_action": "add",
                    "proposed_current_entry_id": None,
                    "proposal_reason": "No current match.",
                }
            )
        issue_view = build_issue_focused_roster_import_reconciliation(full)
        self.assertEqual(issue_view["safe_changes"], [])
        self.assertEqual(
            {item["code"] for item in issue_view["issues"]},
            {"repaired_evidence", "duplicate_candidate"},
        )

    def test_aggregate_requires_one_bulk_unresolved_acknowledgement_then_exposes_rollback(self) -> None:
        focused = self._focused()
        apply_issue_focused_roster_import_reconciliation(
            root_dir=self.root,
            candidate_id=focused["candidate_id"],
            expected_result_fingerprint=focused["result_fingerprint"],
            expected_current_kind=focused["current_kind"],
            expected_current_fingerprint=focused["current_fingerprint"],
            issue_decisions=[
                {
                    "import_id": item["import_id"],
                    "action": "unresolved",
                    "current_entry_id": None,
                }
                for item in focused["issues"]
            ],
            source_snapshot=self.source_snapshot,
            source_text=self.source,
            draft_path=self.draft_path,
            approved_path=self.approved_path,
        )
        aggregate = inspect_roster_reconciliation_project(
            root_dir=self.root,
            source_snapshot=self.source_snapshot,
            source_text=self.source,
            draft_path=self.draft_path,
            approved_path=self.approved_path,
            history_root=self.history_root,
        )
        self.assertEqual(aggregate["state"], "acknowledgement_required")
        self.assertFalse(aggregate["approval"]["can_approve_resolved"])
        self.assertTrue(aggregate["approval"]["can_approve_with_unresolved"])
        self.assertEqual(
            aggregate["summary"]["unresolved_acknowledgement_count"],
            2,
        )

        approved = read_character_roster(
            self.approved_path,
            source_text=self.source,
            expected_status="approved",
        )
        replacement, revision = replace_approved_character_roster_file(
            draft_path=self.draft_path,
            approved_path=self.approved_path,
            history_root=self.history_root,
            source_text=self.source,
            source_fingerprint=self.source_snapshot["fingerprint"],
            expected_draft_fingerprint=aggregate["current"]["draft_fingerprint"],
            expected_approved_fingerprint=approved["roster_fingerprint"],
            acknowledged_unresolved=True,
            approved_at_utc="2026-07-20T12:04:00Z",
        )
        after = inspect_roster_reconciliation_project(
            root_dir=self.root,
            source_snapshot=self.source_snapshot,
            source_text=self.source,
            draft_path=self.draft_path,
            approved_path=self.approved_path,
            history_root=self.history_root,
        )
        self.assertEqual(after["state"], "approved")
        self.assertTrue(after["rollback"]["available"])
        self.assertEqual(
            after["rollback"]["revision"]["revision_id"],
            revision["revision_id"],
        )
        self.assertEqual(
            after["current"]["approved_fingerprint"],
            replacement["roster_fingerprint"],
        )

    def test_incompatible_approved_roster_is_an_issue_not_a_crash(self) -> None:
        changed_source = {
            **self.source_snapshot,
            "fingerprint": "f" * 64,
            "basename": "changed.txt",
        }
        aggregate = inspect_roster_reconciliation_project(
            root_dir=self.root,
            source_snapshot=changed_source,
            source_text="Different source text.",
            draft_path=self.draft_path,
            approved_path=self.approved_path,
            history_root=self.history_root,
        )
        self.assertEqual(aggregate["state"], "incompatible")
        self.assertIn(
            "incompatible_approved_roster",
            {item["code"] for item in aggregate["issues"]},
        )

    def test_invalid_stable_id_relationship_is_reported_explicitly(self) -> None:
        raw = json.loads(self.draft_path.read_text(encoding="utf-8"))
        raw["entries"][0]["possible_duplicate_ids"] = [
            "character_missing000000000"
        ]
        self.draft_path.write_text(json.dumps(raw), encoding="utf-8")
        aggregate = inspect_roster_reconciliation_project(
            root_dir=self.root,
            source_snapshot=self.source_snapshot,
            source_text=self.source,
            draft_path=self.draft_path,
            approved_path=self.approved_path,
            history_root=self.history_root,
        )
        codes = {item["code"] for item in aggregate["issues"]}
        self.assertIn("invalid_stable_id_relationship", codes)
        self.assertIn("invalid_roster_artifact", codes)


if __name__ == "__main__":
    unittest.main()
