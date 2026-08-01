from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module


class BackendRenderPlanRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app_module.app)
        self.saved_state = copy.deepcopy(app_module.process_state["render_plan"])
        app_module.process_state["render_plan"].update(
            {
                "running": False,
                "logs": [],
                "cancel": False,
                "process": None,
                "started_at": None,
                "finished_at": None,
                "last_error": None,
            }
        )

    def tearDown(self):
        app_module.process_state["render_plan"].clear()
        app_module.process_state["render_plan"].update(self.saved_state)

    def test_status_exposes_plan_and_process_state(self):
        app_module.process_state["render_plan"].update(
            {
                "running": True,
                "logs": ["Planning batch 2/4."],
                "started_at": "2026-07-29T18:00:00Z",
            }
        )
        with (
            patch.object(
                app_module,
                "inspect_backend_render_plan",
                return_value={
                    "schema_version": 1,
                    "state": "missing",
                    "available": True,
                    "current": False,
                },
            ),
            patch.object(
                app_module,
                "_current_script_lifecycle_status",
                return_value={"accepted": True},
            ),
        ):
            response = self.client.get("/api/backend_render_plan/status")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["accepted"])
        self.assertTrue(payload["available"])
        self.assertTrue(payload["process"]["running"])
        self.assertEqual(payload["process"]["logs"], ["Planning batch 2/4."])

    def test_manual_local_generation_uses_script_stage(self):
        with (
            patch.object(
                app_module,
                "_current_script_lifecycle_status",
                return_value={"accepted": True},
            ),
            patch.object(
                app_module.project_manager,
                "load_chunks",
                return_value=[{"text": "One line."}],
            ),
            patch.object(
                app_module,
                "inspect_backend_render_plan",
                return_value={"current": False, "available": True},
            ),
            patch.object(
                app_module,
                "_start_backend_render_plan_thread",
                return_value=True,
            ) as starter,
        ):
            response = self.client.post("/api/backend_render_plan/generate", json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "started")
        starter.assert_called_once_with()

    def test_accepting_local_script_starts_same_render_plan_stage(self):
        request = app_module.ScriptLifecycleAcceptRequest(
            expected_script_fingerprint="a" * 64,
            expected_metadata_fingerprint="b" * 64,
            expected_source_fingerprint="c" * 64,
        )
        accepted = {
            "status": "accepted",
            "idempotent": False,
            "version": {
                "version_id": "script_version_fixture",
                "generation_method": "local",
            },
            "state_fingerprint": "state-before-handoff",
            "discovery_handoff": {"status": "pending"},
        }
        with (
            patch.object(
                app_module,
                "_current_script_lifecycle_status",
                return_value={"state": "review_required"},
            ),
            patch.object(
                app_module,
                "_current_character_roster_source_context",
                return_value={
                    "source_text": "Source.",
                    "source_fingerprint": "c" * 64,
                },
            ),
            patch.object(app_module, "accept_current_script", return_value=accepted),
            patch.object(
                app_module,
                "mark_discovery_handoff",
                return_value={
                    "discovery_handoff": {"status": "pending"},
                    "state_fingerprint": "state-after-pending",
                },
            ) as pending_handoff,
            patch.object(
                app_module,
                "_mark_accepted_script_handoff",
            ) as roster_handoff,
            patch.object(
                app_module,
                "inspect_backend_render_plan",
                return_value={"current": False, "plan_fingerprint": None},
            ),
            patch.object(
                app_module.project_manager,
                "load_chunks",
                return_value=[{"text": "Source."}],
            ),
            patch.object(
                app_module,
                "_start_backend_render_plan_thread",
                return_value=True,
            ) as starter,
        ):
            result = app_module._accept_current_script_request(request)
        self.assertEqual(result["delivery_plan_handoff"]["status"], "running")
        self.assertTrue(result["delivery_plan_handoff"]["local_started"])
        self.assertEqual(result["discovery_handoff"]["status"], "pending")
        pending_handoff.assert_called_once()
        roster_handoff.assert_not_called()
        starter.assert_called_once_with()

    def test_successful_local_planner_resumes_roster_afterwards(self):
        with (
            patch.object(app_module, "run_process", return_value=0),
            patch.object(
                app_module,
                "_resume_roster_after_backend_render_plan",
                return_value={
                    "discovery_handoff": {"status": "running"},
                    "state_fingerprint": "after-resume",
                },
            ) as resume,
            patch.object(app_module, "_append_process_log") as log,
        ):
            return_code = app_module._run_backend_render_plan_process()
        self.assertEqual(return_code, 0)
        resume.assert_called_once_with()
        log.assert_any_call(
            "render_plan",
            "Delivery plan complete. Character roster handoff resumed.",
        )

    def test_backend_plan_follow_on_resumes_only_for_delivery_plan_task(self):
        result = {
            "task_type": "backend_render_plan_generation",
            "application": {"destination": "script"},
        }
        with patch.object(
            app_module,
            "_resume_roster_after_backend_render_plan",
            return_value={
                "discovery_handoff": {"status": "running"},
                "state_fingerprint": "after-resume",
            },
        ) as resume:
            followed = app_module._with_backend_render_plan_follow_on(result)
        self.assertEqual(followed["follow_on"]["status"], "resumed")
        self.assertEqual(
            followed["follow_on"]["discovery_handoff"]["status"],
            "running",
        )
        resume.assert_called_once_with()

    def test_accepting_chatgpt_script_waits_for_selected_second_pass(self):
        request = app_module.ScriptLifecycleAcceptRequest(
            expected_script_fingerprint="a" * 64,
            expected_metadata_fingerprint="b" * 64,
            expected_source_fingerprint="c" * 64,
        )
        accepted = {
            "status": "accepted",
            "idempotent": False,
            "version": {
                "version_id": "script_version_fixture",
                "generation_method": "chatgpt_task_bundle",
            },
            "state_fingerprint": "state-before-handoff",
            "discovery_handoff": {"status": "pending"},
        }
        with (
            patch.object(
                app_module,
                "_current_script_lifecycle_status",
                return_value={"state": "review_required"},
            ),
            patch.object(
                app_module,
                "_current_character_roster_source_context",
                return_value={
                    "source_text": "Source.",
                    "source_fingerprint": "c" * 64,
                },
            ),
            patch.object(app_module, "accept_current_script", return_value=accepted),
            patch.object(
                app_module,
                "_mark_accepted_script_handoff",
                return_value={
                    "discovery_handoff": {"status": "running"},
                    "state_fingerprint": "state-after-handoff",
                },
            ),
            patch.object(
                app_module,
                "inspect_backend_render_plan",
                return_value={"current": False, "plan_fingerprint": None},
            ),
            patch.object(
                app_module,
                "_start_backend_render_plan_thread",
            ) as starter,
        ):
            result = app_module._accept_current_script_request(request)
        self.assertEqual(result["delivery_plan_handoff"]["status"], "pending")
        self.assertFalse(result["delivery_plan_handoff"]["local_started"])
        starter.assert_not_called()


if __name__ == "__main__":
    unittest.main()
