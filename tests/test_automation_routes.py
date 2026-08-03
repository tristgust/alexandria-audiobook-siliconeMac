from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app as app_module
from automation_api import (
    automation_state_root,
    provision_automation_credential,
)
from chatgpt_handoff import (
    build_result_envelope,
    load_task_bundle,
    save_result_envelope,
)
from external_workflows import get_task_bundle_path


FULL_SCOPES = {
    "automation:discover",
    "state:read",
    "work:read",
    "work:cancel",
    "tasks:read",
    "tasks:export",
    "tasks:import",
    "operations:produce",
    "operations:export",
}


def route_client(*, client_host: str = "127.0.0.1") -> TestClient:
    return TestClient(
        app_module.app,
        base_url="http://127.0.0.1",
        client=(client_host, 50421),
    )


class AutomationRouteSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.credential_path = self.root.parent / f"{self.root.name}-credential.json"
        self.state_root = self.root.parent / f"{self.root.name}-automation-state"
        self.token = "route-token-" + "x" * 64
        provision_automation_credential(
            path=self.credential_path,
            token=self.token,
            credential_id="credential_routes",
            scopes=FULL_SCOPES,
        )
        self.environment = patch.dict(
            os.environ,
            {
                "ALEXANDRIA_AUTOMATION_CREDENTIAL": str(self.credential_path),
                "ALEXANDRIA_AUTOMATION_STATE_HOME": str(self.state_root),
            },
            clear=False,
        )
        self.environment.start()
        self.flow = {
            "schema_version": 1,
            "generated_at_utc": "2026-08-02T22:00:00Z",
            "summary_state": "current",
            "completion_state": "requires_work",
            "blocker_count": 1,
            "project": {
                "id": "secret-project-title",
                "name": "Secret Project Title",
                "archive_state": "active",
                "latest_meaningful_activity": "2026-08-02T21:00:00Z",
                "technical_details": {
                    "project_path": "/Users/tristan/private/project",
                    "source_path": "/Users/tristan/private/source.epub",
                },
            },
            "source": {
                "filename": "private-source.epub",
                "title": "Private Source",
            },
            "recommended_stage": "produce",
            "safe_next_action": {
                "id": "open_produce",
                "label": "Open Produce",
                "native_destination": "produce",
            },
            "stages": [
                {
                    "key": "produce",
                    "state": "blocked",
                    "summary": "One production blocker remains.",
                    "safe_next_action": {
                        "id": "show_blocker",
                        "label": "Show blocker",
                        "native_destination": "produce",
                        "target_id": "chunk:7",
                    },
                    "fingerprints": {"chunks": "a" * 64},
                    "blockers": [
                        {
                            "code": "produce_audio_missing",
                            "title": "Audio missing",
                            "explanation": "Generate required audio.",
                            "blocking": True,
                            "native_destination": "produce",
                            "target_id": "chunk:7",
                            "technical_secret": "do-not-expose",
                        }
                    ],
                }
            ],
        }
        self.patchers = [
            patch.object(app_module, "ROOT_DIR", str(self.root)),
            patch.object(
                app_module,
                "_current_project_flow_status",
                return_value=copy.deepcopy(self.flow),
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.client = route_client()

    def tearDown(self) -> None:
        self.client.close()
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.environment.stop()
        self.credential_path.unlink(missing_ok=True)
        if self.state_root.exists():
            import shutil

            shutil.rmtree(self.state_root)
        self.temporary.cleanup()

    def headers(self, **updates: str) -> dict[str, str]:
        values = {
            "host": "127.0.0.1",
            "authorization": f"Bearer {self.token}",
        }
        values.update(updates)
        return values

    def test_capabilities_require_direct_loopback_host_origin_auth_and_scope(self) -> None:
        response = self.client.get(
            "/api/automation/capabilities",
            headers=self.headers(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertFalse(payload["mcp"]["enabled"])
        self.assertEqual(
            payload["mcp"]["decision"],
            "deferred_no_rest_capability_gap",
        )
        self.assertTrue(payload["task_bundles_primary"])
        self.assertNotIn(self.token, response.text)
        self.assertNotIn("credential_path", response.text)

        query_only = self.client.get(
            "/api/automation/capabilities",
            params={"token": self.token},
            headers={"host": "127.0.0.1"},
        )
        self.assertEqual(query_only.status_code, 401, query_only.text)
        self.assertEqual(
            query_only.json()["detail"]["code"],
            "automation_authentication_required",
        )

        wrong_host = self.client.get(
            "/api/automation/capabilities",
            headers=self.headers(host="attacker.invalid"),
        )
        self.assertEqual(wrong_host.status_code, 403, wrong_host.text)
        self.assertEqual(
            wrong_host.json()["detail"]["code"],
            "automation_host_rejected",
        )

        origin = self.client.get(
            "/api/automation/capabilities",
            headers=self.headers(origin="http://127.0.0.1:3000"),
        )
        self.assertEqual(origin.status_code, 403, origin.text)
        self.assertEqual(
            origin.json()["detail"]["code"],
            "automation_browser_origin_rejected",
        )

        forwarded = self.client.get(
            "/api/automation/capabilities",
            headers=self.headers(**{"x-forwarded-for": "127.0.0.1"}),
        )
        self.assertEqual(forwarded.status_code, 403, forwarded.text)
        self.assertEqual(
            forwarded.json()["detail"]["code"],
            "automation_forwarded_request_rejected",
        )

        remote = route_client(client_host="192.0.2.22")
        try:
            remote_response = remote.get(
                "/api/automation/capabilities",
                headers=self.headers(),
            )
        finally:
            remote.close()
        self.assertEqual(remote_response.status_code, 403, remote_response.text)
        self.assertEqual(
            remote_response.json()["detail"]["code"],
            "automation_loopback_required",
        )

        limited_path = self.root.parent / f"{self.root.name}-limited.json"
        limited_token = "limited-token-" + "y" * 64
        provision_automation_credential(
            path=limited_path,
            token=limited_token,
            credential_id="credential_limited",
            scopes={"automation:discover"},
        )
        try:
            with patch.dict(
                os.environ,
                {"ALEXANDRIA_AUTOMATION_CREDENTIAL": str(limited_path)},
                clear=False,
            ):
                missing_scope = self.client.get(
                    "/api/automation/state",
                    headers={
                        "host": "127.0.0.1",
                        "authorization": f"Bearer {limited_token}",
                    },
                )
        finally:
            limited_path.unlink(missing_ok=True)
        self.assertEqual(missing_scope.status_code, 403, missing_scope.text)
        self.assertEqual(
            missing_scope.json()["detail"]["code"],
            "automation_scope_required",
        )

        inside_path = self.root / "unsafe-credential.json"
        inside_token = "inside-token-" + "i" * 64
        provision_automation_credential(
            path=inside_path,
            token=inside_token,
            credential_id="credential_inside_project",
            scopes={"automation:discover"},
        )
        try:
            with patch.dict(
                os.environ,
                {
                    "ALEXANDRIA_AUTOMATION_CREDENTIAL": str(inside_path),
                    "ALEXANDRIA_AUTOMATION_STATE_HOME": str(self.root / "unsafe-state"),
                },
                clear=False,
            ):
                inside = self.client.get(
                    "/api/automation/capabilities",
                    headers={
                        "host": "127.0.0.1",
                        "authorization": f"Bearer {inside_token}",
                    },
                )
        finally:
            inside_path.unlink(missing_ok=True)
        self.assertEqual(inside.status_code, 503, inside.text)
        self.assertEqual(
            inside.json()["detail"]["code"],
            "automation_storage_inside_project",
        )

    def test_state_blockers_and_work_are_explicitly_redacted(self) -> None:
        state = self.client.get(
            "/api/automation/state",
            headers=self.headers(),
        )
        self.assertEqual(state.status_code, 200, state.text)
        state_text = state.text
        for secret in (
            "Secret Project Title",
            "secret-project-title",
            "/Users/tristan/private/project",
            "private-source.epub",
            "do-not-expose",
            "chunk:7",
        ):
            self.assertNotIn(secret, state_text)
        self.assertRegex(state.json()["project"]["project_ref"], r"^[0-9a-f]{20}$")
        self.assertEqual(state.json()["recommended_stage"], "produce")

        blockers = self.client.get(
            "/api/automation/blockers",
            headers=self.headers(),
        )
        self.assertEqual(blockers.status_code, 200, blockers.text)
        self.assertEqual(blockers.json()["count"], 1)
        self.assertNotIn("technical_secret", blockers.text)

        submitted = app_module.submit_background_job(
            str(self.root),
            domain="automation_fixture",
            operation="redaction",
            resources=("project_audio",),
            request={"private_payload": "DO-NOT-RETURN"},
            dependency_fingerprint="f" * 64,
            resumable=False,
            external_ref={"source_path": "/secret/source.wav"},
            metadata={"provider_token": "SECRET-TOKEN"},
        )["job"]
        work = self.client.get(
            "/api/automation/work",
            headers=self.headers(),
        )
        self.assertEqual(work.status_code, 200, work.text)
        self.assertIn(submitted["job_id"], work.text)
        for secret in (
            "DO-NOT-RETURN",
            "/secret/source.wav",
            "SECRET-TOKEN",
            "private_payload",
            "external_ref",
            "metadata",
            '"message"',
        ):
            self.assertNotIn(secret, work.text)

    def test_produce_review_is_body_bound_one_time_and_calls_native_executor(self) -> None:
        plan = {
            "schema_version": 1,
            "mode": "selected",
            "indices": [7],
            "chunk_ids": ["chunk:7"],
            "chunks_fingerprint": "c" * 64,
            "plan_fingerprint": "p" * 64,
            "safe_to_execute": True,
            "blockers": [],
        }
        execute = AsyncMock(
            return_value={
                "status": "accepted",
                "plan": plan,
                "generation": {"request_id": "audio_request_fixture"},
            }
        )
        with patch.object(
            app_module,
            "_current_produce_plan",
            return_value=copy.deepcopy(plan),
        ), patch.object(app_module, "_execute_produce_plan", new=execute):
            reviewed = self.client.post(
                "/api/automation/operations/produce/review",
                json={
                    "mode": "selected",
                    "selected_chunk_ids": ["chunk:7"],
                    "replace_active": False,
                    "confirm_regenerate_all": False,
                },
                headers=self.headers(),
            )
            self.assertEqual(reviewed.status_code, 200, reviewed.text)
            payload = reviewed.json()
            token = payload["review"]["review_token"]
            execute_payload = payload["execute_payload"]

            changed = {
                **execute_payload,
                "selected_chunk_ids": ["chunk:8"],
            }
            changed_response = self.client.post(
                "/api/automation/operations/produce/start",
                json=changed,
                headers=self.headers(
                    **{
                        "x-alexandria-review-token": token,
                        "idempotency-key": "produce-route-review-0001",
                    }
                ),
            )
            self.assertEqual(changed_response.status_code, 409, changed_response.text)
            self.assertEqual(
                changed_response.json()["detail"]["code"],
                "automation_review_request_changed",
            )

            started = self.client.post(
                "/api/automation/operations/produce/start",
                json=execute_payload,
                headers=self.headers(
                    **{
                        "x-alexandria-review-token": token,
                        "idempotency-key": "produce-route-review-0001",
                    }
                ),
            )
            self.assertEqual(started.status_code, 200, started.text)
            self.assertTrue(started.json()["automation"]["review_consumed"])
            self.assertEqual(execute.await_count, 1)
            native_request = execute.await_args.args[0]
            self.assertEqual(native_request.mode, "selected")
            self.assertEqual(native_request.selected_chunk_ids, ["chunk:7"])
            self.assertEqual(native_request.plan_fingerprint, "p" * 64)

            replay = self.client.post(
                "/api/automation/operations/produce/start",
                json=execute_payload,
                headers=self.headers(
                    **{
                        "x-alexandria-review-token": token,
                        "idempotency-key": "produce-route-review-0002",
                    }
                ),
            )
            self.assertEqual(replay.status_code, 409, replay.text)
            self.assertEqual(
                replay.json()["detail"]["code"],
                "automation_review_replay_rejected",
            )

    def test_export_review_and_start_call_native_plan_and_executor(self) -> None:
        plan = {
            "schema_version": 1,
            "formats": ["m4b"],
            "chapter_mode": "smart",
            "plan_fingerprint": "e" * 64,
            "dependency_fingerprint": "d" * 64,
            "safe_to_execute": True,
            "blockers": [],
        }
        execute = AsyncMock(
            return_value={"status": "accepted", "job": {"job_id": "export_job"}}
        )
        with patch.object(
            app_module,
            "_current_export_plan",
            return_value=copy.deepcopy(plan),
        ), patch.object(app_module, "execute_export_plan", new=execute):
            reviewed = self.client.post(
                "/api/automation/operations/export/review",
                json={
                    "metadata": {
                        "title": "Fixture",
                        "author": "Author",
                        "narrator": "Narrator",
                        "year": "2026",
                        "description": "Fixture",
                    },
                    "formats": ["m4b"],
                    "chapter_mode": "smart",
                },
                headers=self.headers(),
            )
            self.assertEqual(reviewed.status_code, 200, reviewed.text)
            reviewed_payload = reviewed.json()
            started = self.client.post(
                "/api/automation/operations/export/start",
                json=reviewed_payload["execute_payload"],
                headers=self.headers(
                    **{
                        "x-alexandria-review-token": reviewed_payload["review"]["review_token"],
                        "idempotency-key": "export-route-review-0001",
                    }
                ),
            )
            self.assertEqual(started.status_code, 200, started.text)
            self.assertEqual(execute.await_count, 1)
            native_request = execute.await_args.args[0]
            self.assertEqual(native_request.formats, ["m4b"])
            self.assertEqual(native_request.plan_fingerprint, "e" * 64)
            self.assertEqual(native_request.dependency_fingerprint, "d" * 64)

    def test_work_cancel_is_reviewed_and_fails_if_job_changes(self) -> None:
        job = app_module.submit_background_job(
            str(self.root),
            domain="automation_fixture",
            operation="cancel",
            resources=("project_audio",),
            request={"fixture": True},
            dependency_fingerprint="a" * 64,
            resumable=False,
        )["job"]
        reviewed = self.client.post(
            "/api/automation/work/cancel/review",
            data={"job_id": job["job_id"]},
            headers=self.headers(),
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        payload = reviewed.json()
        app_module.cancel_background_job(str(self.root), job["job_id"])
        changed = self.client.post(
            "/api/automation/work/cancel",
            json=payload["job"],
            headers=self.headers(
                **{
                    "x-alexandria-review-token": payload["review"]["review_token"],
                    "idempotency-key": "work-cancel-review-0001",
                }
            ),
        )
        self.assertEqual(changed.status_code, 409, changed.text)
        self.assertEqual(
            changed.json()["detail"]["code"],
            "automation_work_changed",
        )

        second = app_module.submit_background_job(
            str(self.root),
            domain="automation_fixture",
            operation="cancel-two",
            resources=("project_export",),
            request={"fixture": 2},
            dependency_fingerprint="b" * 64,
            resumable=False,
        )["job"]
        second_review = self.client.post(
            "/api/automation/work/cancel/review",
            data={"job_id": second["job_id"]},
            headers=self.headers(),
        ).json()
        cancelled = self.client.post(
            "/api/automation/work/cancel",
            json=second_review["job"],
            headers=self.headers(
                **{
                    "x-alexandria-review-token": second_review["review"]["review_token"],
                    "idempotency-key": "work-cancel-review-0002",
                }
            ),
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["job"]["state"], "cancelled")


class AutomationTaskBundleRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "app").mkdir()
        self.config_path = self.root / "app/config.json"
        self.config_path.write_text("{}", encoding="utf-8")
        self.source_path = self.root / "source.txt"
        self.source_path.write_text("The source text remains exact.", encoding="utf-8")
        (self.root / "project_state.json").write_text(
            json.dumps(
                {
                    "source_path": str(self.source_path),
                    "script_checkpoint_status": "not_started",
                }
            ),
            encoding="utf-8",
        )
        (self.root / "annotated_script.json").write_text("[]", encoding="utf-8")
        (self.root / "chunks.json").write_text("[]", encoding="utf-8")
        (self.root / "voice_config.json").write_text("{}", encoding="utf-8")
        (self.root / "character_roster.json").write_text(
            json.dumps({"schema_version": 2, "entries": []}),
            encoding="utf-8",
        )
        (self.root / "character_roster_draft.json").write_text(
            json.dumps({"schema_version": 2, "entries": []}),
            encoding="utf-8",
        )
        (self.root / "roster_discovery.json").write_text("{}", encoding="utf-8")
        (self.root / "visual_discovery.json").write_text("{}", encoding="utf-8")
        self.credential_path = self.root.parent / f"{self.root.name}-tasks-credential.json"
        self.state_root = self.root.parent / f"{self.root.name}-tasks-state"
        self.token = "task-route-token-" + "z" * 64
        provision_automation_credential(
            path=self.credential_path,
            token=self.token,
            credential_id="credential_task_routes",
            scopes={"tasks:read", "tasks:export", "tasks:import"},
        )
        self.environment = patch.dict(
            os.environ,
            {
                "ALEXANDRIA_AUTOMATION_CREDENTIAL": str(self.credential_path),
                "ALEXANDRIA_AUTOMATION_STATE_HOME": str(self.state_root),
            },
            clear=False,
        )
        self.environment.start()
        self.patchers = [
            patch.object(app_module, "ROOT_DIR", str(self.root)),
            patch.object(app_module, "CONFIG_PATH", str(self.config_path)),
            patch.object(app_module, "STATE_PATH", str(self.root / "project_state.json")),
            patch.object(app_module, "SCRIPT_PATH", str(self.root / "annotated_script.json")),
            patch.object(app_module, "CHUNKS_PATH", str(self.root / "chunks.json")),
            patch.object(app_module, "VOICE_CONFIG_PATH", str(self.root / "voice_config.json")),
            patch.object(app_module, "ROSTER_PATH", str(self.root / "character_roster.json")),
            patch.object(
                app_module,
                "ROSTER_DRAFT_PATH",
                str(self.root / "character_roster_draft.json"),
            ),
            patch.object(
                app_module,
                "ROSTER_DISCOVERY_PATH",
                str(self.root / "roster_discovery.json"),
            ),
            patch.object(
                app_module,
                "VISUAL_DISCOVERY_PATH",
                str(self.root / "visual_discovery.json"),
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.client = route_client()

    def tearDown(self) -> None:
        self.client.close()
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.environment.stop()
        self.credential_path.unlink(missing_ok=True)
        if self.state_root.exists():
            import shutil

            shutil.rmtree(self.state_root)
        self.temporary.cleanup()

    def headers(self, **updates: str) -> dict[str, str]:
        values = {
            "host": "127.0.0.1",
            "authorization": f"Bearer {self.token}",
        }
        values.update(updates)
        return values

    def root_manifest(self) -> dict[str, str]:
        import hashlib

        result = {}
        for path in sorted(self.root.rglob("*")):
            if path.is_file():
                result[path.relative_to(self.root).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
        return result

    @staticmethod
    def semantic_manifest(path: Path) -> dict:
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        for key in ("task_id", "created_at_utc", "manifest_fingerprint"):
            manifest.pop(key, None)
        return manifest

    def export_automation_bundle(self) -> tuple[dict, Path]:
        reviewed = self.client.post(
            "/api/automation/tasks/export/review",
            json={
                "task_type": "script_generation",
                "target": None,
                "options": {},
            },
            headers=self.headers(),
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        review = reviewed.json()
        executed = self.client.post(
            "/api/automation/tasks/export",
            json=review["execute_payload"],
            headers=self.headers(
                **{
                    "x-alexandria-review-token": review["review"]["review_token"],
                    "idempotency-key": "task-export-route-0001",
                }
            ),
        )
        self.assertEqual(executed.status_code, 200, executed.text)
        result = executed.json()
        path, _record = get_task_bundle_path(
            root_dir=self.root,
            task_id=result["task_id"],
        )
        return result, path

    def test_task_registry_library_export_download_and_native_parity(self) -> None:
        registry = self.client.get(
            "/api/automation/tasks/registry",
            headers=self.headers(),
        )
        self.assertEqual(registry.status_code, 200, registry.text)
        self.assertTrue(registry.json()["task_bundles_primary"])
        self.assertIn(
            "script_generation",
            {item["task_type"] for item in registry.json()["tasks"]},
        )

        result, automation_path = self.export_automation_bundle()
        download = self.client.get(
            result["download_url"],
            headers=self.headers(),
        )
        self.assertEqual(download.status_code, 200, download.text)
        self.assertEqual(download.content, automation_path.read_bytes())

        native = self.client.post(
            "/api/tasks/export",
            json={
                "task_type": "script_generation",
                "target": None,
                "options": {},
            },
        )
        self.assertEqual(native.status_code, 200, native.text)
        native_path, _record = get_task_bundle_path(
            root_dir=self.root,
            task_id=native.json()["task_id"],
        )
        self.assertEqual(
            self.semantic_manifest(automation_path),
            self.semantic_manifest(native_path),
        )

        library = self.client.get(
            "/api/automation/tasks/library",
            headers=self.headers(),
        )
        self.assertEqual(library.status_code, 200, library.text)
        self.assertGreaterEqual(library.json()["count"], 2)
        self.assertNotIn("target", library.text)
        self.assertNotIn("manifest_fingerprint", library.text)

    def test_task_import_review_is_project_file_pure_then_uses_native_import(self) -> None:
        export_result, task_path = self.export_automation_bundle()
        manifest = load_task_bundle(task_path)
        envelope = build_result_envelope(
            task_bundle=manifest,
            result_payload={
                "script": [
                    {
                        "speaker": "NARRATOR",
                        "text": "The imported line remains exact.",
                        "instruct": "Calm and clear.",
                    }
                ]
            },
            assistant_model="fixture-model",
            assistant_summary="Prepared one structured Script line.",
        )
        completed = self.root.parent / f"{self.root.name}-completed.json"
        save_result_envelope(envelope, completed)
        before = self.root_manifest()
        with completed.open("rb") as completed_handle, task_path.open("rb") as task_handle:
            reviewed = self.client.post(
                "/api/automation/tasks/import/review",
                files={
                    "file": (completed.name, completed_handle, "application/json"),
                    "original_task": (task_path.name, task_handle, "application/zip"),
                },
                headers=self.headers(),
            )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        self.assertFalse(reviewed.json()["project_mutated"])
        self.assertEqual(self.root_manifest(), before)

        review = reviewed.json()
        executed = self.client.post(
            "/api/automation/tasks/import",
            json=review["execute_payload"],
            headers=self.headers(
                **{
                    "x-alexandria-review-token": review["review"]["review_token"],
                    "idempotency-key": "task-import-route-0001",
                }
            ),
        )
        self.assertEqual(executed.status_code, 200, executed.text)
        result = executed.json()
        self.assertEqual(result["task_type"], "script_generation")
        self.assertEqual(result["native_destination"], "script")
        self.assertTrue((self.root / "external_workflows" / "results").is_dir())
        staging = automation_state_root(self.credential_path) / "staging"
        if staging.exists():
            self.assertEqual([item for item in staging.iterdir() if item.is_file()], [])

        replay = self.client.post(
            "/api/automation/tasks/import",
            json=review["execute_payload"],
            headers=self.headers(
                **{
                    "x-alexandria-review-token": review["review"]["review_token"],
                    "idempotency-key": "task-import-route-0002",
                }
            ),
        )
        self.assertEqual(replay.status_code, 409, replay.text)
        self.assertEqual(
            replay.json()["detail"]["code"],
            "automation_review_replay_rejected",
        )
        completed.unlink(missing_ok=True)

    def test_changed_staged_import_fails_closed_and_cleans_private_copy(self) -> None:
        _export_result, task_path = self.export_automation_bundle()
        manifest = load_task_bundle(task_path)
        envelope = build_result_envelope(
            task_bundle=manifest,
            result_payload={
                "script": [
                    {
                        "speaker": "NARRATOR",
                        "text": "A second imported line.",
                        "instruct": "Measured.",
                    }
                ]
            },
            assistant_model="fixture-model",
            assistant_summary="Prepared another Script line.",
        )
        completed = self.root.parent / f"{self.root.name}-changed.json"
        save_result_envelope(envelope, completed)
        with completed.open("rb") as completed_handle, task_path.open("rb") as task_handle:
            reviewed = self.client.post(
                "/api/automation/tasks/import/review",
                files={
                    "file": (completed.name, completed_handle, "application/json"),
                    "original_task": (task_path.name, task_handle, "application/zip"),
                },
                headers=self.headers(),
            )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        review_token = reviewed.json()["review"]["review_token"]
        ticket_id = review_token.split(".", 1)[0]
        ticket_path = automation_state_root(self.credential_path) / "reviews" / f"{ticket_id}.json"
        ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
        staged_path = Path(ticket["staged_files"][0]["path"])
        staged_path.write_bytes(b"changed-after-review")
        before = self.root_manifest()
        executed = self.client.post(
            "/api/automation/tasks/import",
            json=reviewed.json()["execute_payload"],
            headers=self.headers(
                **{
                    "x-alexandria-review-token": review_token,
                    "idempotency-key": "task-import-route-0003",
                }
            ),
        )
        self.assertEqual(executed.status_code, 409, executed.text)
        self.assertEqual(
            executed.json()["detail"]["code"],
            "automation_staged_file_changed",
        )
        self.assertEqual(self.root_manifest(), before)
        self.assertFalse(staged_path.exists())
        completed.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
