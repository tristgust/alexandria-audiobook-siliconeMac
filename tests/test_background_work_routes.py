from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import app as app_module
from background_work import (
    claim_job,
    configure_scheduler,
    get_job,
    submit_job,
)


class BackgroundWorkRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        configure_scheduler(self.root, max_pending=8)
        self.root_patch = patch.object(app_module, "ROOT_DIR", str(self.root))
        self.root_patch.start()
        self.client = TestClient(app_module.app)
        self.saved_state = copy.deepcopy(app_module.process_state)

    def tearDown(self) -> None:
        self.client.close()
        app_module.process_state.clear()
        app_module.process_state.update(self.saved_state)
        self.root_patch.stop()
        self.temporary.cleanup()

    def submit(
        self,
        domain: str,
        *,
        resources: tuple[str, ...],
        external_ref: dict | None = None,
        resumable: bool = True,
    ) -> dict:
        return submit_job(
            self.root,
            domain=domain,
            operation="fixture",
            resources=resources,
            request={
                "secret_input": "must-not-be-public",
                "domain": domain,
                "external_ref": copy.deepcopy(external_ref),
            },
            dependency_fingerprint="a" * 64,
            resumable=resumable,
            external_ref=external_ref,
        )["job"]

    def test_status_redacts_request_and_lease_tokens(self) -> None:
        job = self.submit(
            "audio_generation",
            resources=("project_audio", "model_runtime"),
            external_ref={"authority": "audio_generation_request", "request_id": "audio_1"},
        )
        claimed = claim_job(self.root, job["job_id"])
        response = self.client.get("/api/background-work")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["active_count"], 1)
        public = payload["active"][0]
        self.assertEqual(public["job_id"], job["job_id"])
        self.assertEqual(public["external_ref"]["request_id"], "audio_1")
        self.assertNotIn("request", public)
        self.assertNotIn("result", public)
        self.assertNotIn("error", public)
        self.assertNotIn("owner_token", public)
        self.assertNotIn("publication_token", public)
        self.assertNotIn("secret_input", response.text)
        self.assertNotIn(claimed["owner_token"], response.text)

    def test_generic_cancel_updates_scheduler_and_domain_flag(self) -> None:
        job = self.submit(
            "export",
            resources=("project_audio", "project_export"),
            resumable=False,
        )
        claim_job(self.root, job["job_id"])
        app_module.process_state["export"]["running"] = True
        response = self.client.post(
            f"/api/background-work/{job['job_id']}/cancel",
            json={},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "cancelling")
        self.assertTrue(app_module.process_state["export"]["cancel_requested"])
        self.assertEqual(get_job(self.root, job["job_id"])["state"], "cancelling")

    def test_voice_preparation_cancel_targets_only_owning_process(self) -> None:
        cases = (
            ("dataset_builder", "dataset_builder"),
            ("audio_preparer", "preparer"),
            ("audio_preparer_batch", "batch_preparer"),
        )
        process_keys = tuple(process_key for _authority, process_key in cases)
        for authority, expected_process_key in cases:
            with self.subTest(authority=authority):
                for process_key in process_keys:
                    app_module.process_state[process_key]["cancel"] = False
                job = self.submit(
                    "voice_preparation",
                    resources=("model_runtime", "voice_preparation"),
                    external_ref={"authority": authority},
                    resumable=False,
                )
                response = self.client.post(
                    f"/api/background-work/{job['job_id']}/cancel",
                    json={},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["status"], "cancelled")
                for process_key in process_keys:
                    self.assertEqual(
                        app_module.process_state[process_key]["cancel"],
                        process_key == expected_process_key,
                    )

    def test_queued_scheduler_job_blocks_project_switch(self) -> None:
        job = self.submit(
            "voice_preparation",
            resources=("model_runtime", "voice_preparation"),
        )
        blockers = app_module._project_switch_blockers()
        self.assertIn("background_work:voice_preparation", blockers)
        self.assertEqual(get_job(self.root, job["job_id"])["state"], "queued")

    def test_startup_reconciliation_redispatches_only_resumable_known_domain(self) -> None:
        audio = self.submit(
            "audio_generation",
            resources=("project_audio", "model_runtime"),
            external_ref={
                "authority": "audio_generation_request",
                "request_id": "audio_request_restart",
            },
        )
        export = self.submit(
            "export",
            resources=("project_export",),
            resumable=False,
        )
        claim_job(self.root, audio["job_id"])
        claim_job(self.root, export["job_id"])
        thread = MagicMock()
        with (
            patch.object(
                app_module,
                "load_audio_generation_request",
                return_value={"state": "recovering"},
            ),
            patch.object(app_module.threading, "Thread", return_value=thread) as factory,
        ):
            import asyncio

            asyncio.run(app_module.reconcile_background_work_after_startup())
        self.assertEqual(get_job(self.root, audio["job_id"])["state"], "queued")
        self.assertEqual(get_job(self.root, export["job_id"])["state"], "failed")
        factory.assert_called_once()
        thread.start.assert_called_once()
        self.assertEqual(factory.call_args.kwargs["args"], ("audio_request_restart", audio["job_id"]))

    def test_empty_status_read_does_not_create_scheduler_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            empty = Path(temporary)
            with patch.object(app_module, "ROOT_DIR", str(empty)):
                response = self.client.get("/api/background-work")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["active_count"], 0)
            self.assertFalse((empty / "background_work").exists())


if __name__ == "__main__":
    unittest.main()
