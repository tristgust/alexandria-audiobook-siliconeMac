from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import app as app_module
from character_roster import save_character_roster
from character_roster_actions import apply_character_roster_action
from tests.test_character_roster import CharacterRosterFixture


class CharacterRosterActionAPITests(
    unittest.TestCase,
    CharacterRosterFixture,
):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_path = self.root / "book.txt"
        self.source_path.write_text(self.SOURCE_TEXT, encoding="utf-8")
        self.source_snapshot = self.source(self.source_path)
        (self.root / "state.json").write_text(
            json.dumps({"input_file_path": str(self.source_path)}),
            encoding="utf-8",
        )
        self.draft_path = self.root / "character_roster.draft.json"
        self.approved_path = self.root / "character_roster.json"
        self.protected = [
            self.root / "annotated_script.json",
            self.root / "annotated_script.meta.json",
            self.root / "generation_state.json",
            self.root / "chunks.json",
            self.root / "voice_config.json",
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
                "CHARACTER_ROSTER_DRAFT_PATH",
                str(self.draft_path),
            ),
            patch.object(
                app_module,
                "CHARACTER_ROSTER_PATH",
                str(self.approved_path),
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
        app_module.process_state["roster"] = {
            "running": False,
            "logs": [],
            "cancel": False,
            "process": None,
        }
        self.draft = self.draft(self.source_snapshot)
        save_character_roster(
            self.draft,
            self.draft_path,
            source_text=self.SOURCE_TEXT,
            expected_status="draft",
        )

    def tearDown(self) -> None:
        app_module.process_state["roster"] = {
            "running": False,
            "logs": [],
            "cancel": False,
            "process": None,
        }
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    @staticmethod
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def action_request(self, **kwargs):
        return app_module.CharacterRosterActionRequest(
            draft_fingerprint=kwargs.pop(
                "draft_fingerprint",
                self.draft["draft_fingerprint"],
            ),
            action=kwargs.pop("action", "confirm"),
            entry_id=kwargs.pop(
                "entry_id",
                self.draft["entries"][0]["id"],
            ),
            **kwargs,
        )

    def test_action_route_updates_draft_and_preserves_runtime_files(self) -> None:
        before = {path.name: self.digest(path) for path in self.protected}
        result = asyncio.run(
            app_module.update_character_roster_draft(
                self.action_request(
                    action="rename",
                    value="THE SEVENTH DOCTOR",
                    display_name="The Seventh Doctor",
                )
            )
        )
        after = {path.name: self.digest(path) for path in self.protected}
        self.assertEqual(before, after)
        self.assertEqual(result["status"], "updated")
        self.assertEqual(
            result["draft"]["entries"][0]["canonical_name"],
            "THE SEVENTH DOCTOR",
        )
        self.assertEqual(
            result["draft"]["review_history"][-1]["action"],
            "rename",
        )

    def test_stale_action_returns_machine_readable_conflict(self) -> None:
        with self.assertRaises(HTTPException) as error:
            asyncio.run(
                app_module.update_character_roster_draft(
                    self.action_request(draft_fingerprint="stale")
                )
            )
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(error.exception.detail["code"], "stale_draft")

    def test_action_is_blocked_while_discovery_runs(self) -> None:
        app_module.process_state["roster"]["running"] = True
        with self.assertRaises(HTTPException) as error:
            asyncio.run(
                app_module.update_character_roster_draft(
                    self.action_request()
                )
            )
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(error.exception.detail["code"], "roster_running")

    def test_action_is_blocked_after_approval(self) -> None:
        self.approved_path.write_text("{}", encoding="utf-8")
        with self.assertRaises(HTTPException) as error:
            asyncio.run(
                app_module.update_character_roster_draft(
                    self.action_request()
                )
            )
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(
            error.exception.detail["code"],
            "roster_already_approved",
        )

    def test_approval_requires_unresolved_acknowledgment(self) -> None:
        unresolved = apply_character_roster_action(
            self.draft,
            expected_fingerprint=self.draft["draft_fingerprint"],
            source_fingerprint=self.source_snapshot["fingerprint"],
            source_text=self.SOURCE_TEXT,
            action="mark_unresolved",
            entry_id=self.draft["entries"][0]["id"],
            reason="Which incarnation?",
            at_utc="2026-07-16T21:00:00Z",
        )
        save_character_roster(
            unresolved,
            self.draft_path,
            source_text=self.SOURCE_TEXT,
            expected_status="draft",
        )
        with self.assertRaises(HTTPException) as error:
            asyncio.run(
                app_module.approve_character_roster(
                    app_module.CharacterRosterApproveRequest(
                        draft_fingerprint=unresolved[
                            "draft_fingerprint"
                        ],
                        acknowledged_unresolved=False,
                    )
                )
            )
        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(
            error.exception.detail["code"],
            "approval_blocked",
        )

    def test_approval_is_atomic_and_changes_no_downstream_artifact(self) -> None:
        before = {path.name: self.digest(path) for path in self.protected}
        result = asyncio.run(
            app_module.approve_character_roster(
                app_module.CharacterRosterApproveRequest(
                    draft_fingerprint=self.draft["draft_fingerprint"],
                    acknowledged_unresolved=False,
                )
            )
        )
        after = {path.name: self.digest(path) for path in self.protected}
        self.assertEqual(before, after)
        self.assertEqual(result["status"], "approved")
        self.assertEqual(result["roster"]["status"], "approved")
        self.assertTrue(self.approved_path.exists())
        self.assertFalse(
            self.approved_path.with_name(
                self.approved_path.name + ".tmp"
            ).exists()
        )
        self.assertTrue(self.draft_path.exists())

    def test_real_routes_return_updated_and_approved_objects(self) -> None:
        client = TestClient(app_module.app)
        response = client.post(
            "/api/character_roster/draft/action",
            json={
                "draft_fingerprint": self.draft["draft_fingerprint"],
                "action": "add_alias",
                "entry_id": self.draft["entries"][0]["id"],
                "value": "DOCTOR",
            },
        )
        self.assertEqual(response.status_code, 200)
        updated = response.json()["draft"]
        response = client.post(
            "/api/character_roster/approve",
            json={
                "draft_fingerprint": updated["draft_fingerprint"],
                "acknowledged_unresolved": False,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["roster"]["status"], "approved")

    def test_routes_are_registered_once(self) -> None:
        paths = [route.path for route in app_module.app.routes]
        self.assertEqual(
            paths.count("/api/character_roster/draft/action"),
            1,
        )
        self.assertEqual(paths.count("/api/character_roster/approve"), 1)


if __name__ == "__main__":
    unittest.main()
