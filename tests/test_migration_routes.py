from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from generation_state import atomic_json_write


class MigrationRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config_path = self.root / "app" / "config.json"
        self.config_path.parent.mkdir(parents=True)
        atomic_json_write(
            {
                "llm": {
                    "model_name": "qwen3.5:35b-mlx",
                    "custom": "preserve",
                },
                "tts": {"mode": "local"},
                "unknown_root": {"keep": True},
            },
            self.config_path,
        )
        atomic_json_write(
            [
                {
                    "speaker": "NARRATOR",
                    "text": "The room was quiet.",
                    "instruct": "Neutral narration.",
                }
            ],
            self.root / "annotated_script.json",
        )
        self.patchers = [
            patch.object(app_module, "ROOT_DIR", str(self.root / "active-project")),
            patch.object(app_module, "MIGRATION_ROOT_DIR", str(self.root)),
            patch.object(app_module, "CONFIG_PATH", str(self.config_path)),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.client.close()
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def status(self) -> dict:
        response = self.client.get("/api/migration/status")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_status_is_file_pure_and_reports_plan(self) -> None:
        before = self.config_path.read_bytes()
        payload = self.status()
        self.assertTrue(payload["migration_required"])
        self.assertFalse(payload["migration_blocked"])
        self.assertEqual(
            payload["actions"][0]["action"],
            "add_empty_llm_profiles",
        )
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertFalse((self.root / "migration_state.json").exists())

    def test_history_route_is_file_pure_and_not_captured_as_operation_id(self) -> None:
        before = self.config_path.read_bytes()
        response = self.client.get("/api/migration/history")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["operations"], [])
        self.assertEqual(payload["invalid_records"], [])
        self.assertTrue(payload["history_fingerprint"])
        self.assertEqual(self.config_path.read_bytes(), before)

    def test_apply_operation_read_and_rollback_routes(self) -> None:
        initial = self.status()
        applied = self.client.post(
            "/api/migration/apply",
            json={
                "plan_fingerprint": initial["plan_fingerprint"],
                "confirm": True,
            },
        )
        self.assertEqual(applied.status_code, 200, applied.text)
        payload = applied.json()
        operation_id = payload["operation"]["operation_id"]
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["llm"]["profiles"], {})
        self.assertEqual(saved["unknown_root"], {"keep": True})

        history = self.client.get(
            f"/api/migration/history/{operation_id}"
        )
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(history.json()["operation_id"], operation_id)

        history = self.client.get("/api/migration/history")
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(
            history.json()["operations"][0]["operation_id"],
            operation_id,
        )
        self.assertTrue(
            history.json()["operations"][0]["rollback_available"]
        )

        rollback = self.client.post(
            "/api/migration/rollback",
            json={"operation_id": operation_id},
        )
        self.assertEqual(rollback.status_code, 200, rollback.text)
        restored = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertNotIn("profiles", restored["llm"])
        self.assertTrue(rollback.json()["status"]["migration_required"])
        history = self.client.get("/api/migration/history")
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(
            [item["operation"] for item in history.json()["operations"]],
            ["rollback", "migration"],
        )
        self.assertEqual(
            history.json()["operations"][1]["state"],
            "rolled_back",
        )

    def test_stale_and_unconfirmed_apply_are_machine_readable(self) -> None:
        stale = self.client.post(
            "/api/migration/apply",
            json={"plan_fingerprint": "stale", "confirm": True},
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(
            stale.json()["detail"]["code"],
            "stale_migration_plan",
        )

        unconfirmed = self.client.post(
            "/api/migration/apply",
            json={
                "plan_fingerprint": self.status()["plan_fingerprint"],
                "confirm": False,
            },
        )
        self.assertEqual(unconfirmed.status_code, 422, unconfirmed.text)
        self.assertEqual(
            unconfirmed.json()["detail"]["code"],
            "migration_rejected",
        )

    def test_blocked_plan_and_missing_history_are_explicit(self) -> None:
        self.config_path.write_text("[]", encoding="utf-8")
        status = self.status()
        self.assertTrue(status["migration_blocked"])
        rejected = self.client.post(
            "/api/migration/apply",
            json={
                "plan_fingerprint": status["plan_fingerprint"],
                "confirm": True,
            },
        )
        self.assertEqual(rejected.status_code, 422, rejected.text)

        missing = self.client.get(
            "/api/migration/history/"
            "migration_000000000000000000000000"
        )
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(
            missing.json()["detail"]["code"],
            "migration_operation_not_found",
        )

    def test_invalid_request_schema_is_rejected(self) -> None:
        apply = self.client.post(
            "/api/migration/apply",
            json={"confirm": True},
        )
        self.assertEqual(apply.status_code, 422, apply.text)
        rollback = self.client.post(
            "/api/migration/rollback",
            json={},
        )
        self.assertEqual(rollback.status_code, 422, rollback.text)

    def test_routes_are_registered_once(self) -> None:
        registrations = [
            (route.path, frozenset(getattr(route, "methods", set())))
            for route in app_module.app.routes
        ]
        expected = (
            ("/api/migration/status", "GET"),
            ("/api/migration/history/{operation_id}", "GET"),
            ("/api/migration/apply", "POST"),
            ("/api/migration/rollback", "POST"),
        )
        for path, method in expected:
            self.assertEqual(
                sum(
                    route_path == path and method in methods
                    for route_path, methods in registrations
                ),
                1,
            )


if __name__ == "__main__":
    unittest.main()
