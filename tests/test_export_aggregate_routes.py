from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module


class ExportAggregateRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "app").mkdir()
        self.config_path = self.root / "app" / "config.json"
        self.config_path.write_text(
            json.dumps(
                {
                    "tts": {
                        "pause_between_speakers_ms": 500,
                        "pause_same_speaker_ms": 250,
                    }
                }
            ),
            encoding="utf-8",
        )
        self.produce = {
            "state": "complete",
            "summary": {
                "required_chunk_count": 2,
                "current_count": 2,
                "complete": True,
            },
            "chunks": [
                {
                    "chunk_id": "chunk:0",
                    "speaker": "NARRATOR",
                    "text": "Prologue",
                    "duration_ms": 1000,
                    "pause_after_ms": None,
                    "state": "current",
                },
                {
                    "chunk_id": "chunk:1",
                    "speaker": "DOCTOR",
                    "text": "Chapter One",
                    "duration_ms": 1200,
                    "pause_after_ms": None,
                    "state": "current",
                },
            ],
            "fingerprints": {
                "aggregate": "produce-aggregate",
                "chunks": "produce-chunks",
                "voice_config": "voice-config",
                "synthesis": "synthesis",
            },
        }
        self.original_export_state = copy.deepcopy(
            app_module.process_state["export"]
        )
        app_module.process_state["export"] = {
            "running": False,
            "logs": [],
            "cancel": False,
            "operation_id": None,
            "plan_fingerprint": None,
            "dependency_fingerprint": None,
            "formats": [],
            "started_at": None,
            "finished_at": None,
            "last_error": None,
            "result": None,
        }
        self.patchers = [
            patch.object(app_module, "ROOT_DIR", str(self.root)),
            patch.object(app_module, "CONFIG_PATH", str(self.config_path)),
            patch.object(
                app_module,
                "_current_produce_status",
                return_value=self.produce,
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        app_module.process_state["export"] = self.original_export_state
        self.temporary.cleanup()

    def _protected_hashes(self) -> dict[str, str]:
        result = {}
        for name in (
            "app/config.json",
            "cloned_audiobook.mp3",
            "audiobook.m4b",
            "audacity_export.zip",
            "export_build.json",
        ):
            path = self.root / name
            result[name] = (
                hashlib.sha256(path.read_bytes()).hexdigest()
                if path.exists()
                else "<absent>"
            )
        return result

    @staticmethod
    def _request(formats=None) -> dict:
        return {
            "metadata": {
                "title": "Book",
                "author": "Author",
                "narrator": "Narrator",
                "year": "2026",
                "description": "Description",
            },
            "formats": formats or ["mp3", "m4b"],
            "chapter_mode": "smart",
        }

    def _plan(self, formats=None) -> dict:
        response = self.client.post(
            "/api/export/plan",
            json=self._request(formats),
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_routes_are_registered_once(self) -> None:
        paths = [route.path for route in app_module.app.routes]
        for path in (
            "/api/export",
            "/api/export/plan",
            "/api/export/build",
            "/api/export/cancel",
        ):
            self.assertEqual(paths.count(path), 1)

    def test_status_and_plan_are_read_only_and_model_free(self) -> None:
        before = self._protected_hashes()
        with (
            patch.object(
                app_module.project_manager,
                "get_engine",
                side_effect=AssertionError("status must not load TTS"),
            ),
            patch.object(
                app_module,
                "download_or_repair_model",
                side_effect=AssertionError("status must not download models"),
            ),
            patch.object(
                app_module,
                "build_runtime_client",
                side_effect=AssertionError("status must not connect to LLM"),
            ),
        ):
            status = self.client.get("/api/export")
            plan = self._plan()
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["state"], "blocked")
        self.assertIn(
            "export_metadata_missing",
            {item["code"] for item in status.json()["blockers"]},
        )
        self.assertTrue(plan["safe_to_execute"])
        self.assertEqual(plan["formats"], ["mp3", "m4b"])
        self.assertEqual(before, self._protected_hashes())

    def test_build_dispatches_reviewed_plan_and_records_terminal_result(self) -> None:
        plan = self._plan()
        captured = {}

        def fake_execute(**kwargs):
            captured.update(kwargs)
            return {
                "status": "complete",
                "build_id": "export_test",
                "committed": True,
                "receipt": {"build_id": "export_test"},
            }

        body = {
            **self._request(),
            "plan_fingerprint": plan["plan_fingerprint"],
            "dependency_fingerprint": plan["dependency_fingerprint"],
        }
        with patch.object(
            app_module,
            "execute_export_build",
            side_effect=fake_execute,
        ):
            response = self.client.post("/api/export/build", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(captured["root_dir"], str(self.root))
        self.assertEqual(
            captured["plan"]["plan_fingerprint"],
            plan["plan_fingerprint"],
        )
        state = app_module.process_state["export"]
        self.assertFalse(state["running"])
        self.assertEqual(state["result"]["build_id"], "export_test")
        self.assertIsNotNone(state["started_at"])
        self.assertIsNotNone(state["finished_at"])
        self.assertIn("Export build complete", state["logs"][-1])

    def test_stale_plan_and_dependency_fingerprints_fail_closed(self) -> None:
        plan = self._plan()
        stale_dependency = self.client.post(
            "/api/export/build",
            json={
                **self._request(),
                "plan_fingerprint": plan["plan_fingerprint"],
                "dependency_fingerprint": "stale",
            },
        )
        self.assertEqual(stale_dependency.status_code, 409)
        self.assertEqual(
            stale_dependency.json()["detail"]["code"],
            "export_dependencies_changed",
        )
        stale_plan = self.client.post(
            "/api/export/build",
            json={
                **self._request(),
                "plan_fingerprint": "stale",
                "dependency_fingerprint": plan["dependency_fingerprint"],
            },
        )
        self.assertEqual(stale_plan.status_code, 409)
        self.assertEqual(
            stale_plan.json()["detail"]["code"],
            "export_plan_stale",
        )

    def test_incomplete_produce_blocks_plan_and_build(self) -> None:
        incomplete = json.loads(json.dumps(self.produce))
        incomplete["summary"]["complete"] = False
        with patch.object(
            app_module,
            "_current_produce_status",
            return_value=incomplete,
        ):
            plan_response = self.client.post(
                "/api/export/plan",
                json=self._request(),
            )
            self.assertEqual(plan_response.status_code, 200, plan_response.text)
            plan = plan_response.json()
            self.assertFalse(plan["safe_to_execute"])
            build = self.client.post(
                "/api/export/build",
                json={
                    **self._request(),
                    "plan_fingerprint": plan["plan_fingerprint"],
                    "dependency_fingerprint": plan["dependency_fingerprint"],
                },
            )
        self.assertEqual(build.status_code, 409)
        self.assertEqual(
            build.json()["detail"]["code"],
            "export_plan_blocked",
        )

    def test_cancel_sets_flag_only_while_running(self) -> None:
        idle = self.client.post("/api/export/cancel")
        self.assertEqual(idle.status_code, 200)
        self.assertEqual(idle.json()["status"], "idle")

        app_module.process_state["export"]["running"] = True
        running = self.client.post("/api/export/cancel")
        self.assertEqual(running.status_code, 200)
        self.assertEqual(running.json()["status"], "cancelling")
        self.assertTrue(app_module.process_state["export"]["cancel"])
        self.assertIn(
            "Export cancellation requested.",
            app_module.process_state["export"]["logs"],
        )

    def test_unavailable_format_is_explicit(self) -> None:
        response = self.client.post(
            "/api/export/plan",
            json=self._request(["chapter_separated"]),
        )
        self.assertEqual(response.status_code, 200, response.text)
        plan = response.json()
        self.assertFalse(plan["safe_to_execute"])
        self.assertIn(
            "export_format_unavailable",
            {item["code"] for item in plan["blockers"]},
        )


if __name__ == "__main__":
    unittest.main()
