from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from character_roster import build_draft_roster, read_character_roster, save_character_roster
from character_roster_actions import approve_character_roster_file
from external_workflows import get_structured_result_candidate, store_structured_result_candidate
from generation_state import fingerprint_text, fingerprint_value
from roster_import_reconciliation import (
    RosterImportReconciliationValidationError,
    apply_roster_import_reconciliation,
    get_pending_roster_import_reconciliation,
)


class RosterImportReconciliationTests(unittest.TestCase):
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
        self.state_path = self.root / "character_roster_state.json"
        self.current_doctor_id = "character_11111111111111111111"
        self._prepare_approved_roster()
        self.result = self._imported_result()
        self.candidate = self._store_candidate(self.result)

    def tearDown(self) -> None:
        self.temporary.cleanup()

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

    def _prepare_approved_roster(self) -> None:
        doctor_evidence = self._native_evidence("The Doctor", "name")
        draft = build_draft_roster(
            source=self.source_snapshot,
            discovery={
                "created_at_utc": "2026-07-19T12:00:00Z",
                "model_name": "test-model",
                "backend": "test",
                "generation_fingerprint": "a" * 64,
                "batch_count": 1,
                "completed_batches": 1,
            },
            entries=[
                {
                    "id": self.current_doctor_id,
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
                    "first_evidence_location": doctor_evidence["source_location"],
                    "additional_evidence_locations": [],
                    "confidence": 0.95,
                    "resolution_status": "resolved",
                    "possible_duplicate_ids": [],
                    "mistaken_merge_risk": False,
                    "unresolved_questions": [],
                    "evidence": [doctor_evidence],
                    "voice_clues": [],
                    "sample_lines": [],
                }
            ],
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
            approved_at_utc="2026-07-19T12:05:00Z",
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
                        self._evidence(
                            "The UNIT soldiers waited.",
                            "other",
                        )
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
                    unresolved_questions=["Is this officer named later?"],
                    evidence=[self._evidence("unknown officer", "other")],
                ),
            ],
            "warnings": ["One identity remains intentionally unresolved."],
        }

    def _store_candidate(
        self,
        result: dict,
        *,
        handoff_id: str = "handoff_roster_import_test_1",
        created_at_utc: str = "2026-07-19T12:10:00Z",
    ) -> dict:
        return store_structured_result_candidate(
            root_dir=self.root,
            validated={
                "handoff_id": handoff_id,
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
            created_at_utc=created_at_utc,
        )

    def _pending(self) -> dict:
        value = get_pending_roster_import_reconciliation(
            root_dir=self.root,
            source_snapshot=self.source_snapshot,
            source_text=self.source,
            draft_path=self.draft_path,
            approved_path=self.approved_path,
        )
        self.assertIsNotNone(value)
        assert value is not None
        return value

    def test_saved_candidate_reopens_as_actionable_current_imported_comparison(self) -> None:
        pending = self._pending()

        self.assertEqual(pending["candidate_id"], self.candidate["candidate_id"])
        self.assertEqual(pending["current_kind"], "approved")
        self.assertEqual(pending["summary"]["current_entries"], 1)
        self.assertEqual(pending["summary"]["imported_observations"], 4)
        self.assertEqual(pending["summary"]["proposed_merges"], 1)
        self.assertEqual(pending["summary"]["groups"], 1)
        self.assertEqual(pending["summary"]["unresolved"], 2)
        self.assertEqual(pending["summary"]["semantic_invalid"], 1)
        self.assertEqual(pending["summary"]["aliases"], 3)
        self.assertEqual(len(pending["observations"]), 4)
        doctor = next(
            item for item in pending["observations"]
            if item["canonical_name"] == "THE DOCTOR"
        )
        self.assertEqual(doctor["proposed_action"], "merge")
        self.assertEqual(
            doctor["proposed_current_entry_id"],
            self.current_doctor_id,
        )
        narrator = next(
            item for item in pending["observations"]
            if item["entity_kind"] == "narrator_role"
        )
        self.assertEqual(narrator["resolved_evidence_count"], 1)
        self.assertEqual(narrator["native_semantic_status"], "invalid")
        self.assertEqual(narrator["proposed_action"], "unresolved")
        self.assertTrue(narrator["native_semantic_errors"])
        stored = get_structured_result_candidate(
            root_dir=self.root,
            candidate_id=self.candidate["candidate_id"],
        )
        self.assertEqual(stored["status"], "inspected")
        self.assertEqual(stored["result"], self.result)

    def test_semantically_invalid_observation_cannot_be_added_or_merged(self) -> None:
        pending = self._pending()
        draft_before = self.draft_path.read_bytes()
        approved_before = self.approved_path.read_bytes()
        decisions = [
            {
                "import_id": item["import_id"],
                "action": (
                    "add"
                    if item["native_semantic_status"] == "invalid"
                    else item["proposed_action"]
                ),
                "current_entry_id": item["proposed_current_entry_id"],
            }
            for item in pending["observations"]
        ]

        with self.assertRaises(
            RosterImportReconciliationValidationError
        ) as context:
            apply_roster_import_reconciliation(
                root_dir=self.root,
                candidate_id=pending["candidate_id"],
                expected_result_fingerprint=pending["result_fingerprint"],
                expected_current_kind=pending["current_kind"],
                expected_current_fingerprint=pending["current_fingerprint"],
                decisions=decisions,
                source_snapshot=self.source_snapshot,
                source_text=self.source,
                draft_path=self.draft_path,
                approved_path=self.approved_path,
            )

        self.assertEqual(
            context.exception.code,
            "roster_import_semantic_validation_required",
        )
        self.assertEqual(self.draft_path.read_bytes(), draft_before)
        self.assertEqual(self.approved_path.read_bytes(), approved_before)
        stored = get_structured_result_candidate(
            root_dir=self.root,
            candidate_id=self.candidate["candidate_id"],
        )
        self.assertEqual(stored["status"], "inspected")
        self.assertEqual(stored["result"], self.result)

    def test_applied_result_suppresses_older_identical_pending_candidate(self) -> None:
        duplicate = self._store_candidate(
            self.result,
            handoff_id="handoff_roster_import_test_2",
            created_at_utc="2026-07-19T12:11:00Z",
        )
        pending = self._pending()
        self.assertEqual(pending["candidate_id"], duplicate["candidate_id"])
        decisions = [
            {
                "import_id": item["import_id"],
                "action": item["proposed_action"],
                "current_entry_id": item["proposed_current_entry_id"],
            }
            for item in pending["observations"]
        ]

        apply_roster_import_reconciliation(
            root_dir=self.root,
            candidate_id=pending["candidate_id"],
            expected_result_fingerprint=pending["result_fingerprint"],
            expected_current_kind=pending["current_kind"],
            expected_current_fingerprint=pending["current_fingerprint"],
            decisions=decisions,
            source_snapshot=self.source_snapshot,
            source_text=self.source,
            draft_path=self.draft_path,
            approved_path=self.approved_path,
            applied_at_utc="2026-07-19T12:15:00Z",
        )

        self.assertIsNone(
            get_pending_roster_import_reconciliation(
                root_dir=self.root,
                source_snapshot=self.source_snapshot,
                source_text=self.source,
                draft_path=self.draft_path,
                approved_path=self.approved_path,
            )
        )
        self.assertIsNone(
            get_pending_roster_import_reconciliation(
                root_dir=self.root,
                source_snapshot=self.source_snapshot,
                source_text=self.source,
                draft_path=self.draft_path,
                approved_path=self.approved_path,
                candidate_id=self.candidate["candidate_id"],
            )
        )
        older = get_structured_result_candidate(
            root_dir=self.root,
            candidate_id=self.candidate["candidate_id"],
        )
        applied = get_structured_result_candidate(
            root_dir=self.root,
            candidate_id=duplicate["candidate_id"],
        )
        self.assertEqual(older["status"], "inspected")
        self.assertEqual(older["result"], self.result)
        self.assertEqual(applied["status"], "transferred")
        self.assertEqual(applied["result"], self.result)

    def test_explicit_partition_creates_draft_and_preserves_approved_roster(self) -> None:
        pending = self._pending()
        approved_before = self.approved_path.read_bytes()
        decisions = [
            {
                "import_id": item["import_id"],
                "action": item["proposed_action"],
                "current_entry_id": item["proposed_current_entry_id"],
            }
            for item in pending["observations"]
        ]

        result = apply_roster_import_reconciliation(
            root_dir=self.root,
            candidate_id=pending["candidate_id"],
            expected_result_fingerprint=pending["result_fingerprint"],
            expected_current_kind=pending["current_kind"],
            expected_current_fingerprint=pending["current_fingerprint"],
            decisions=decisions,
            source_snapshot=self.source_snapshot,
            source_text=self.source,
            draft_path=self.draft_path,
            approved_path=self.approved_path,
            applied_at_utc="2026-07-19T12:15:00Z",
        )

        self.assertEqual(result["status"], "transferred")
        self.assertEqual(result["application"]["observation_count"], 4)
        self.assertEqual(result["application"]["merged_count"], 1)
        self.assertEqual(result["application"]["added_count"], 1)
        self.assertEqual(result["application"]["unresolved_count"], 2)
        self.assertTrue(result["application"]["approved_roster_preserved"])
        self.assertEqual(self.approved_path.read_bytes(), approved_before)
        approved = read_character_roster(
            self.approved_path,
            source_text=self.source,
            expected_status="approved",
        )
        self.assertEqual(len(approved["entries"]), 1)
        draft = read_character_roster(
            self.draft_path,
            source_text=self.source,
            expected_status="draft",
        )
        self.assertEqual(len(draft["entries"]), 4)
        doctor = next(
            entry for entry in draft["entries"]
            if entry["id"] == self.current_doctor_id
        )
        self.assertEqual(len(doctor["evidence"]), 2)
        self.assertNotIn("Doctor", doctor["aliases"])
        self.assertTrue(
            any(entry["entity_kind"] == "group" for entry in draft["entries"])
        )
        self.assertTrue(
            any(
                entry["resolution_status"] == "unresolved"
                for entry in draft["entries"]
            )
        )
        self.assertFalse(self.state_path.exists())
        stored = get_structured_result_candidate(
            root_dir=self.root,
            candidate_id=self.candidate["candidate_id"],
        )
        self.assertEqual(stored["status"], "transferred")
        self.assertEqual(stored["result"], self.result)
        self.assertIsNone(
            get_pending_roster_import_reconciliation(
                root_dir=self.root,
                source_snapshot=self.source_snapshot,
                source_text=self.source,
                draft_path=self.draft_path,
                approved_path=self.approved_path,
            )
        )


if __name__ == "__main__":
    unittest.main()
