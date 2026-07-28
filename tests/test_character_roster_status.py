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
from character_roster import (
    save_character_roster,
)
from tests.test_character_roster import (
    CharacterRosterFixture,
)


class CharacterRosterStatusAPITests(
    unittest.TestCase,
    CharacterRosterFixture,
):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_path = self.root / "book.txt"
        self.source_path.write_text(
            self.SOURCE_TEXT,
            encoding="utf-8",
        )
        self.source_snapshot = self.source(
            self.source_path
        )
        (self.root / "state.json").write_text(
            json.dumps(
                {
                    "input_file_path": str(
                        self.source_path
                    )
                }
            ),
            encoding="utf-8",
        )
        self.draft_path = (
            self.root / "character_roster.draft.json"
        )
        self.approved_path = (
            self.root / "character_roster.json"
        )
        self.state_path = (
            self.root / "character_roster_state.json"
        )

        self.patchers = [
            patch.object(
                app_module,
                "ACTIVE_PROJECT_ID",
                "fixture-project",
            ),
            patch.object(
                app_module,
                "ROOT_DIR",
                str(self.root),
            ),
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
                "CHARACTER_ROSTER_STATE_PATH",
                str(self.state_path),
            ),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    @staticmethod
    def digest(path: Path) -> str:
        if not path.exists():
            return "<absent>"
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def status(self):
        return asyncio.run(
            app_module.get_character_roster_status()
        )

    def test_missing_rosters_have_model_free_status(self):
        before = {
            path.name: self.digest(path)
            for path in (
                self.source_path,
                self.root / "state.json",
                self.draft_path,
                self.approved_path,
                self.state_path,
            )
        }

        with (
            patch.object(
                app_module.project_manager,
                "get_engine",
                side_effect=AssertionError(
                    "TTS must not initialize"
                ),
            ) as get_engine,
            patch.object(
                app_module,
                "build_runtime_client",
                side_effect=AssertionError(
                    "LLM must not initialize"
                ),
            ) as build_client,
            patch.object(
                app_module,
                "build_script_generation_snapshot",
                side_effect=AssertionError(
                    "Script snapshot must not run"
                ),
            ) as script_snapshot,
        ):
            status = self.status()

        after = {
            path.name: self.digest(path)
            for path in (
                self.source_path,
                self.root / "state.json",
                self.draft_path,
                self.approved_path,
                self.state_path,
            )
        }

        self.assertEqual(status["active"], "none")
        self.assertTrue(status["source"]["available"])
        self.assertEqual(
            status["source"]["basename"],
            "book.txt",
        )
        self.assertEqual(before, after)
        get_engine.assert_not_called()
        build_client.assert_not_called()
        script_snapshot.assert_not_called()

    def test_draft_status_exposes_counts_and_compatibility(self):
        draft = self.draft(self.source_snapshot)
        save_character_roster(
            draft,
            self.draft_path,
            source_text=self.SOURCE_TEXT,
            expected_status="draft",
        )
        status = self.status()
        self.assertEqual(status["active"], "draft")
        self.assertEqual(
            status["draft"]["status"],
            "draft",
        )
        self.assertTrue(
            status["draft"]["compatible_source"]
        )
        self.assertEqual(
            status["draft"]["counts"]["entries"],
            3,
        )

    def test_approved_roster_takes_active_precedence(self):
        draft = self.draft(self.source_snapshot)
        approved = self.approved(draft)
        save_character_roster(
            draft,
            self.draft_path,
            source_text=self.SOURCE_TEXT,
        )
        save_character_roster(
            approved,
            self.approved_path,
            source_text=self.SOURCE_TEXT,
        )
        status = self.status()
        self.assertEqual(status["active"], "approved")
        self.assertEqual(
            status["approved"]["status"],
            "approved",
        )

    def test_changed_source_is_incompatible_not_invalid(self):
        draft = self.draft(self.source_snapshot)
        save_character_roster(
            draft,
            self.draft_path,
            source_text=self.SOURCE_TEXT,
        )
        self.source_path.write_text(
            self.SOURCE_TEXT + " Changed.",
            encoding="utf-8",
        )
        status = self.status()
        self.assertEqual(
            status["draft"]["status"],
            "incompatible_source",
        )
        self.assertFalse(
            status["draft"]["compatible_source"]
        )
        self.assertIsNone(status["draft"]["error"])

    def test_missing_selected_source_is_reported(self):
        (self.root / "state.json").unlink()
        status = self.status()
        self.assertFalse(status["source"]["available"])
        self.assertIn(
            "No source file",
            status["source"]["error"],
        )

    def test_draft_read_api_returns_plain_validated_object(self):
        draft = self.draft(self.source_snapshot)
        save_character_roster(
            draft,
            self.draft_path,
            source_text=self.SOURCE_TEXT,
        )
        result = asyncio.run(
            app_module.get_character_roster_draft()
        )
        self.assertEqual(result, draft)
        self.assertEqual(result["status"], "draft")

    def test_missing_read_apis_return_404(self):
        with self.assertRaises(HTTPException) as draft_error:
            asyncio.run(
                app_module.get_character_roster_draft()
            )
        self.assertEqual(
            draft_error.exception.status_code,
            404,
        )

        with self.assertRaises(HTTPException) as approved_error:
            asyncio.run(
                app_module.get_character_roster()
            )
        self.assertEqual(
            approved_error.exception.status_code,
            404,
        )

    def test_invalid_read_api_returns_conflict(self):
        self.draft_path.write_text(
            "{broken",
            encoding="utf-8",
        )
        with self.assertRaises(HTTPException) as error:
            asyncio.run(
                app_module.get_character_roster_draft()
            )
        self.assertEqual(error.exception.status_code, 409)

    def test_real_routes_return_model_free_status_and_draft(self):
        draft = self.draft(self.source_snapshot)
        save_character_roster(
            draft,
            self.draft_path,
            source_text=self.SOURCE_TEXT,
        )

        with (
            patch.object(
                app_module.project_manager,
                "get_engine",
                side_effect=AssertionError(
                    "TTS must not initialize"
                ),
            ),
            patch.object(
                app_module,
                "build_runtime_client",
                side_effect=AssertionError(
                    "LLM must not initialize"
                ),
            ),
            TestClient(app_module.app) as client,
        ):
            status_response = client.get(
                "/api/character_roster/status"
            )
            draft_response = client.get(
                "/api/character_roster/draft"
            )

        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(
            status_response.json()["active"],
            "draft",
        )
        self.assertEqual(draft_response.status_code, 200)
        self.assertEqual(draft_response.json(), draft)

    def test_routes_are_registered_once(self):
        expected = {
            "/api/character_roster/status",
            "/api/character_roster/draft",
            "/api/character_roster",
        }
        paths = [
            route.path
            for route in app_module.app.routes
            if getattr(route, "path", None) in expected
        ]
        self.assertEqual(sorted(paths), sorted(expected))


if __name__ == "__main__":
    unittest.main()
