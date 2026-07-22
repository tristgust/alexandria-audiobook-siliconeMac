from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from character_roster import save_character_roster
from tests.test_voice_training_projects import VoiceTrainingProjectFixture


class VoiceTrainingRouteTests(
    unittest.TestCase,
    VoiceTrainingProjectFixture,
):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_path = self.root / "book.txt"
        self.source_path.write_text(self.SOURCE_TEXT, encoding="utf-8")
        self.roster = self.approved_roster(self.source_path)
        self.roster_path = self.root / "character_roster.json"
        save_character_roster(
            self.roster,
            self.roster_path,
            source_text=self.SOURCE_TEXT,
            expected_status="approved",
        )
        self.projects_root = self.root / "voice_training_projects"
        self.state_path = self.root / "state.json"
        self.state_path.write_text(
            json.dumps({"input_file_path": str(self.source_path)}),
            encoding="utf-8",
        )
        self.doctor = next(
            item
            for item in self.roster["entries"]
            if item["canonical_name"] == "THE DOCTOR"
        )
        self.tardis = next(
            item
            for item in self.roster["entries"]
            if item["canonical_name"] == "THE TARDIS"
        )
        self.protected = [
            self.root / "annotated_script.json",
            self.root / "annotated_script.meta.json",
            self.root / "chunks.json",
            self.root / "voice_config.json",
            self.root / "generation_state.json",
        ]
        for index, path in enumerate(self.protected):
            path.write_text(
                json.dumps({"protected": index}),
                encoding="utf-8",
            )
        self.patchers = [
            patch.object(app_module, "ROOT_DIR", str(self.root)),
            patch.object(
                app_module,
                "CHARACTER_ROSTER_PATH",
                str(self.roster_path),
            ),
            patch.object(
                app_module,
                "CHARACTER_ROSTER_DRAFT_PATH",
                str(self.root / "character_roster.draft.json"),
            ),
            patch.object(
                app_module,
                "CHARACTER_ROSTER_STATE_PATH",
                str(self.root / "character_roster_state.json"),
            ),
            patch.object(
                app_module,
                "VOICE_TRAINING_PROJECTS_DIR",
                str(self.projects_root),
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.client.close()
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    @staticmethod
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def protected_hashes(self) -> dict[str, str]:
        return {path.name: self.digest(path) for path in self.protected}

    def create_candidate(self) -> dict:
        response = self.client.post(
            f"/api/voice_training/{self.doctor['id']}/create",
            json={
                "priority": "primary",
                "desired_description": "Draft description.",
                "desired_ref_text": "Draft reference.",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_status_is_model_free_file_pure_and_lists_eligibility(self) -> None:
        before = self.protected_hashes()
        response = self.client.get("/api/voice_training/status")
        after = self.protected_hashes()
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["available"])
        self.assertTrue(payload["source_compatible"])
        self.assertEqual(payload["candidate_count"], 0)
        doctor = next(
            item
            for item in payload["entries"]
            if item["character_id"] == self.doctor["id"]
        )
        tardis = next(
            item
            for item in payload["entries"]
            if item["character_id"] == self.tardis["id"]
        )
        self.assertTrue(doctor["eligible"])
        self.assertEqual(doctor["status"], "absent")
        self.assertFalse(tardis["eligible"])
        self.assertEqual(tardis["status"], "ineligible")
        self.assertEqual(before, after)
        self.assertFalse(self.projects_root.exists())

    def test_pending_roster_reconciliation_blocks_script_fallback_and_preserves_projects(self) -> None:
        self.roster_path.unlink()
        legacy_project = (
            self.projects_root
            / "character_7b53448747348a2df328"
            / "project.json"
        )
        legacy_project.parent.mkdir(parents=True)
        legacy_project.write_text('{"preserved": true}', encoding="utf-8")
        pending = {
            "schema_version": 1,
            "status": "pending",
            "summary": {"imported_observations": 83},
        }
        with patch.object(
            app_module,
            "get_pending_roster_import_reconciliation",
            return_value=pending,
        ):
            response = self.client.get("/api/voice_training/status")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertFalse(payload["available"])
        self.assertEqual(payload["entries"], [])
        self.assertEqual(
            payload["blocker_code"],
            "character_roster_reconciliation_required",
        )
        self.assertEqual(payload["blocking_tab"], "characters")
        self.assertEqual(payload["pending_observation_count"], 83)
        self.assertEqual(payload["preserved_project_count"], 1)
        self.assertIn("83 imported observations", payload["reason"])

        blocked = self.client.post(
            f"/api/voice_training/{self.doctor['id']}/create",
            json={
                "priority": "primary",
                "desired_description": "",
                "desired_ref_text": "",
            },
        )
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(
            blocked.json()["detail"]["code"],
            "approved_roster_required",
        )
        self.assertEqual(
            legacy_project.read_text(encoding="utf-8"),
            '{"preserved": true}',
        )

    def test_create_get_and_action_routes_use_full_contract(self) -> None:
        before = self.protected_hashes()
        created = self.create_candidate()
        response = self.client.get(
            f"/api/voice_training/{self.doctor['id']}"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), created)
        response = self.client.post(
            f"/api/voice_training/{self.doctor['id']}/action",
            json={
                "project_fingerprint": created["project_fingerprint"],
                "action": "approve_persona",
                "payload": {
                    "description": "A precise, alert older traveler.",
                    "ref_text": "Tell me what happened.",
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        self.assertEqual(
            updated["desired_base_persona"]["approval_status"],
            "approved",
        )
        self.assertEqual(before, self.protected_hashes())
        self.assertFalse((self.root / "lora_models").exists())
        self.assertFalse((self.root / "lora_datasets").exists())

    def test_stale_action_returns_409_machine_readable_detail(self) -> None:
        created = self.create_candidate()
        first = self.client.post(
            f"/api/voice_training/{self.doctor['id']}/action",
            json={
                "project_fingerprint": created["project_fingerprint"],
                "action": "update_persona",
                "payload": {
                    "description": "Changed description.",
                    "ref_text": "Changed reference.",
                },
            },
        )
        self.assertEqual(first.status_code, 200, first.text)
        stale = self.client.post(
            f"/api/voice_training/{self.doctor['id']}/action",
            json={
                "project_fingerprint": created["project_fingerprint"],
                "action": "refresh_readiness",
                "payload": {},
            },
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(
            stale.json()["detail"]["code"],
            "stale_voice_training_project",
        )

    def test_ineligible_character_and_missing_project_errors(self) -> None:
        response = self.client.post(
            f"/api/voice_training/{self.tardis['id']}/create",
            json={
                "priority": "secondary",
                "desired_description": "",
                "desired_ref_text": "",
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "character_ineligible",
        )
        response = self.client.get(
            f"/api/voice_training/{self.doctor['id']}"
        )
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "voice_training_project_not_found",
        )

    def test_source_change_is_visible_in_status_and_blocks_mutation(self) -> None:
        changed_source = self.root / "changed-book.txt"
        changed_source.write_text(
            self.SOURCE_TEXT + " Another sentence.",
            encoding="utf-8",
        )
        self.state_path.write_text(
            json.dumps({"input_file_path": str(changed_source)}),
            encoding="utf-8",
        )
        status = self.client.get("/api/voice_training/status")
        self.assertEqual(status.status_code, 200, status.text)
        self.assertFalse(status.json()["source_compatible"])
        self.assertIn(
            "different source",
            status.json()["context_error"],
        )
        response = self.client.post(
            f"/api/voice_training/{self.doctor['id']}/create",
            json={
                "priority": "primary",
                "desired_description": "",
                "desired_ref_text": "",
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "approved_roster_source_mismatch",
        )
        self.assertFalse(self.projects_root.exists())

    def test_missing_selected_source_blocks_actions_but_status_still_reads(self) -> None:
        self.state_path.write_text("{}", encoding="utf-8")
        status = self.client.get("/api/voice_training/status")
        self.assertEqual(status.status_code, 200, status.text)
        self.assertIsNone(status.json()["source_compatible"])
        self.assertIn(
            "No source file",
            status.json()["context_error"],
        )
        response = self.client.post(
            f"/api/voice_training/{self.doctor['id']}/create",
            json={
                "priority": "primary",
                "desired_description": "",
                "desired_ref_text": "",
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "voice_training_context_unavailable",
        )

    def test_route_request_schema_rejects_unknown_action(self) -> None:
        created = self.create_candidate()
        response = self.client.post(
            f"/api/voice_training/{self.doctor['id']}/action",
            json={
                "project_fingerprint": created["project_fingerprint"],
                "action": "train_adapter_now",
                "payload": {},
            },
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_routes_are_registered_once_and_status_precedes_dynamic_get(self) -> None:
        paths = [route.path for route in app_module.app.routes]
        expected = [
            "/api/voice_training/status",
            "/api/voice_training/{character_id}",
            "/api/voice_training/{character_id}/create",
            "/api/voice_training/{character_id}/action",
        ]
        for path in expected:
            self.assertEqual(paths.count(path), 1)
        self.assertLess(
            paths.index("/api/voice_training/status"),
            paths.index("/api/voice_training/{character_id}"),
        )


if __name__ == "__main__":
    unittest.main()
