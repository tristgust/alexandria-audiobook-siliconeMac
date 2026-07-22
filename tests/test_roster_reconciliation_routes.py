from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from character_roster import (
    build_draft_roster,
    read_character_roster,
    save_character_roster,
)
from character_roster_actions import (
    apply_character_roster_action,
    approve_character_roster_file,
)
from external_workflows import store_structured_result_candidate
from generation_state import fingerprint_text, fingerprint_value


class RosterReconciliationRouteTests(unittest.TestCase):
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
        self.source_fingerprint = fingerprint_text(self.source)
        self.source_snapshot = {
            "path": str(self.source_path),
            "basename": self.source_path.name,
            "fingerprint": self.source_fingerprint,
            "character_count": len(self.source),
        }
        (self.root / "state.json").write_text(
            json.dumps({"input_file_path": str(self.source_path)}),
            encoding="utf-8",
        )
        self.draft_path = self.root / "character_roster.draft.json"
        self.approved_path = self.root / "character_roster.json"
        self.history_root = self.root / "character_roster_history"
        self.doctor_id = "character_11111111111111111111"
        self._prepare_approved_roster()
        self.candidate = self._store_candidate(self._imported_result())
        self.protected = [
            self.root / "state.json",
            self.draft_path,
            self.approved_path,
        ]
        self.patchers = [
            patch.object(app_module, "ROOT_DIR", str(self.root)),
            patch.object(
                app_module,
                "CHARACTER_ROSTER_DRAFT_PATH",
                str(self.draft_path),
            ),
            patch.object(
                app_module,
                "CHARACTER_ROSTER_PATH",
                str(self.approved_path),
            ),
            patch.object(
                app_module,
                "CHARACTER_ROSTER_HISTORY_DIR",
                str(self.history_root),
            ),
            patch.object(
                app_module,
                "_external_import_busy_stage",
                return_value=None,
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temporary.cleanup()

    @staticmethod
    def _digest(path: Path) -> str:
        if not path.exists():
            return "<absent>"
        return hashlib.sha256(path.read_bytes()).hexdigest()

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

    def _prepare_approved_roster(self) -> None:
        evidence = self._native_evidence("The Doctor", "name")
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
            entries=[
                {
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
            source_fingerprint=self.source_fingerprint,
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
                "handoff_id": "handoff_roster_route_test_1",
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
                    "source_fingerprint": self.source_fingerprint,
                    "artifact_fingerprints": {},
                },
                "input": {},
            },
            created_at_utc="2026-07-20T12:02:00Z",
        )

    def _status(self) -> dict:
        response = self.client.get("/api/character_roster/reconciliation")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _apply_issues(self) -> dict:
        status = self._status()
        pending = status["pending_import"]
        response = self.client.post(
            "/api/character_roster/reconciliation/apply",
            json={
                "candidate_id": pending["candidate_id"],
                "result_fingerprint": pending["result_fingerprint"],
                "current_kind": pending["current_kind"],
                "current_fingerprint": pending["current_fingerprint"],
                "decisions": [
                    {
                        "import_id": issue["import_id"],
                        "action": "unresolved",
                        "current_entry_id": None,
                    }
                    for issue in pending["issues"]
                ],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_routes_are_registered_once(self) -> None:
        paths = [route.path for route in app_module.app.routes]
        for path in (
            "/api/character_roster/reconciliation",
            "/api/character_roster/reconciliation/apply",
            "/api/character_roster/reconciliation/approve",
        ):
            self.assertEqual(paths.count(path), 1)

    def test_status_is_read_only_model_free_and_hides_safe_changes_from_issues(self) -> None:
        before = {path: self._digest(path) for path in self.protected}
        with (
            patch.object(
                app_module.project_manager,
                "get_engine",
                side_effect=AssertionError("status must not load TTS"),
            ),
            patch.object(
                app_module,
                "build_runtime_client",
                side_effect=AssertionError("status must not connect to LLM"),
            ),
            patch.object(
                app_module,
                "download_or_repair_model",
                side_effect=AssertionError("status must not download models"),
            ),
        ):
            status = self._status()
        after = {path: self._digest(path) for path in self.protected}
        self.assertEqual(before, after)
        self.assertEqual(status["state"], "import_issues")
        self.assertEqual(status["summary"]["safe_change_count"], 2)
        self.assertEqual(status["pending_import"]["summary"]["issue_count"], 2)
        self.assertEqual(
            {item["canonical_name"] for item in status["safe_changes"]},
            {"THE DOCTOR", "UNIT soldiers"},
        )
        self.assertEqual(
            {item["code"] for item in status["pending_import"]["issues"]},
            {"invalid_evidence", "unresolved_identity"},
        )

    def test_apply_requires_only_displayed_issue_decisions(self) -> None:
        result = self._apply_issues()
        self.assertEqual(result["status"], "transferred")
        self.assertEqual(result["reconciliation"]["state"], "acknowledgement_required")
        self.assertEqual(
            result["application"]["decision_summary"],
            {
                "mode": "issue_focused",
                "safe_change_count": 2,
                "operator_issue_count": 2,
            },
        )
        self.assertEqual(
            result["routing"]["native_destination"],
            "cast",
        )

    def test_incomplete_issue_partition_is_machine_readable(self) -> None:
        status = self._status()
        pending = status["pending_import"]
        response = self.client.post(
            "/api/character_roster/reconciliation/apply",
            json={
                "candidate_id": pending["candidate_id"],
                "result_fingerprint": pending["result_fingerprint"],
                "current_kind": pending["current_kind"],
                "current_fingerprint": pending["current_fingerprint"],
                "decisions": [],
            },
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "incomplete_roster_issue_reconciliation",
        )

    def test_approval_requires_one_explicit_unresolved_acknowledgement(self) -> None:
        applied = self._apply_issues()
        reconciliation = applied["reconciliation"]
        response = self.client.post(
            "/api/character_roster/reconciliation/approve",
            json={
                "action": "approve_resolved",
                "draft_fingerprint": reconciliation["current"]["draft_fingerprint"],
                "expected_approved_fingerprint": reconciliation["current"][
                    "approved_fingerprint"
                ],
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "roster_unresolved_acknowledgement_required",
        )

        response = self.client.post(
            "/api/character_roster/reconciliation/approve",
            json={
                "action": "approve_with_unresolved",
                "draft_fingerprint": reconciliation["current"]["draft_fingerprint"],
                "expected_approved_fingerprint": reconciliation["current"][
                    "approved_fingerprint"
                ],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "replaced")
        self.assertIsNotNone(payload["revision"])
        self.assertTrue(payload["reconciliation"]["rollback"]["available"])
        self.assertEqual(payload["reconciliation"]["state"], "approved")

    def test_stale_draft_fingerprint_blocks_approval(self) -> None:
        applied = self._apply_issues()
        reconciliation = applied["reconciliation"]
        response = self.client.post(
            "/api/character_roster/reconciliation/approve",
            json={
                "action": "approve_with_unresolved",
                "draft_fingerprint": "stale",
                "expected_approved_fingerprint": reconciliation["current"][
                    "approved_fingerprint"
                ],
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "stale_roster_draft",
        )

    def test_fully_resolved_replacement_approves_in_one_bulk_action(self) -> None:
        shutil.rmtree(self.root / "external_workflows")
        approved = read_character_roster(
            self.approved_path,
            source_text=self.source,
            expected_status="approved",
        )
        source_draft = read_character_roster(
            self.draft_path,
            source_text=self.source,
            expected_status="draft",
        )
        replacement = apply_character_roster_action(
            source_draft,
            expected_fingerprint=source_draft["draft_fingerprint"],
            source_fingerprint=self.source_fingerprint,
            source_text=self.source,
            action="add_alias",
            entry_id=self.doctor_id,
            value="THE TRAVELER",
            at_utc="2026-07-20T12:05:00Z",
        )
        save_character_roster(
            replacement,
            self.draft_path,
            source_text=self.source,
            expected_status="draft",
        )
        status = self._status()
        self.assertEqual(status["state"], "ready_to_approve")
        self.assertTrue(status["approval"]["can_approve_resolved"])
        self.assertFalse(
            status["approval"]["requires_unresolved_acknowledgement"]
        )

        response = self.client.post(
            "/api/character_roster/reconciliation/approve",
            json={
                "action": "approve_resolved",
                "draft_fingerprint": replacement["draft_fingerprint"],
                "expected_approved_fingerprint": approved[
                    "roster_fingerprint"
                ],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "replaced")
        self.assertEqual(payload["reconciliation"]["state"], "approved")
        self.assertEqual(
            payload["approved"]["approval_summary"][
                "acknowledged_unresolved"
            ],
            False,
        )


if __name__ == "__main__":
    unittest.main()
