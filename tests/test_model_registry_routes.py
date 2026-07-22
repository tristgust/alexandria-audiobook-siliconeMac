from __future__ import annotations

import copy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from model_memory import ModelMemoryError
from model_registry import ModelCacheOperationError


class ModelRegistryRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app_module.app)
        self.saved_state = copy.deepcopy(app_module.process_state["model_cache"])
        app_module.process_state["model_cache"].update(
            {
                "running": False,
                "logs": [],
                "status": "idle",
                "action": None,
                "model_keys": [],
                "current_model_key": None,
                "completed_count": 0,
                "total_count": 0,
                "results": [],
                "error": None,
                "error_code": None,
                "cancel_requested": False,
                "started_at": None,
                "finished_at": None,
            }
        )

    def tearDown(self) -> None:
        self.client.close()
        app_module.process_state["model_cache"].clear()
        app_module.process_state["model_cache"].update(self.saved_state)

    @staticmethod
    def _model(
        key: str,
        *,
        state: str,
        required: bool,
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "model": {
                "key": key,
                "repo_id": f"owner/{key}",
                "revision": "a" * 40,
                "runtime": "fixture",
                "purpose": f"Purpose for {key}",
                "estimated_size_bytes": 1000,
                "required_paths": ["config.json"],
                "required_by_default": required,
                "cache_name": f"models--owner--{key}",
            },
            "state": state,
            "cached": state == "cached",
            "snapshot_path": f"/cache/{key}" if state != "missing" else None,
            "cache_root": "/cache",
            "revision": "a" * 40,
            "required_paths": ["config.json"],
            "missing_required_paths": [] if state == "cached" else ["config.json"],
            "broken_symlinks": [],
            "file_count": 3 if state == "cached" else 0,
            "size_bytes": 750 if state == "cached" else 0,
        }

    def _status(self, models: list[dict[str, object]]) -> dict[str, object]:
        return {
            "schema_version": 1,
            "models": models,
            "cached_count": sum(bool(item["cached"]) for item in models),
            "missing_count": sum(item["state"] == "missing" for item in models),
            "incomplete_count": sum(item["state"] == "incomplete" for item in models),
            "cached_size_bytes": sum(int(item["size_bytes"]) for item in models),
            "estimated_total_bytes": sum(
                int(item["model"]["estimated_size_bytes"]) for item in models
            ),
            "required_count": sum(
                bool(item["model"]["required_by_default"]) for item in models
            ),
            "required_missing_count": sum(
                bool(item["model"]["required_by_default"])
                and item["state"] == "missing"
                for item in models
            ),
            "required_incomplete_count": sum(
                bool(item["model"]["required_by_default"])
                and item["state"] == "incomplete"
                for item in models
            ),
        }

    def test_status_route_returns_inventory_cache_root_and_operation(self) -> None:
        status = self._status(
            [self._model("mlx_clone", state="cached", required=True)]
        )
        with (
            patch.object(app_module, "model_registry_status", return_value=status),
            patch.object(
                app_module,
                "shared_huggingface_cache_dir",
                return_value=Path("/shared-cache"),
            ),
        ):
            response = self.client.get("/api/model_registry/status")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["cache_dir"], "/shared-cache")
        self.assertEqual(payload["models"][0]["state"], "cached")
        self.assertEqual(payload["operation"]["status"], "idle")

    def test_status_failure_is_actionable_and_machine_readable(self) -> None:
        with patch.object(
            app_module,
            "model_registry_status",
            side_effect=RuntimeError("inventory failed"),
        ):
            response = self.client.get("/api/model_registry/status")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json()["detail"]["code"],
            "model_registry_status_failed",
        )

    def test_memory_status_is_model_free_and_reports_active_jobs(self) -> None:
        coordinator = SimpleNamespace(policy=lambda: {"schema_version": 1})
        backend = SimpleNamespace(
            _memory=SimpleNamespace(active_jobs=2),
        )
        with (
            patch.object(app_module, "ModelMemoryCoordinator", return_value=coordinator),
            patch.object(
                app_module,
                "memory_snapshot",
                return_value={"total_bytes": 100, "available_bytes": 60, "used_bytes": 40},
            ),
            patch.object(app_module, "_loaded_mlx_backend", return_value=backend),
            patch.object(app_module, "_loaded_model_registry_keys", return_value=["mlx_clone"]),
        ):
            response = self.client.get("/api/model_registry/memory")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["active_jobs"], 2)
        self.assertEqual(payload["loaded_model_keys"], ["mlx_clone"])
        self.assertEqual(payload["memory"]["available_bytes"], 60)

    def test_memory_policy_update_persists_validated_values(self) -> None:
        coordinator = SimpleNamespace(
            update_policy=lambda value: value,
        )
        with patch.object(
            app_module,
            "ModelMemoryCoordinator",
            return_value=coordinator,
        ):
            response = self.client.put(
                "/api/model_registry/memory/policy",
                json={
                    "minimum_headroom_bytes": 1024,
                    "idle_unload_seconds": 60,
                    "release_and_retry_on_oom": False,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["policy"]["minimum_headroom_bytes"], 1024)

    def test_manual_release_is_safe_when_idle_and_blocked_when_active(self) -> None:
        backend = SimpleNamespace(
            release_models_manually=lambda: {
                "released": True,
                "reason": "manual",
                "active_jobs": 0,
            }
        )
        with patch.object(app_module, "_loaded_mlx_backend", return_value=backend):
            response = self.client.post("/api/model_registry/memory/release")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["released"])

        def blocked():
            raise ModelMemoryError(
                "model_memory_active_jobs",
                "Models cannot be released while synthesis jobs are active.",
                details={"active_jobs": 1},
            )

        backend.release_models_manually = blocked
        with patch.object(app_module, "_loaded_mlx_backend", return_value=backend):
            response = self.client.post("/api/model_registry/memory/release")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"]["code"],
            "model_memory_active_jobs",
        )

    def test_single_download_requires_model_key(self) -> None:
        response = self.client.post(
            "/api/model_registry/action",
            json={"action": "download"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"]["code"],
            "invalid_model_registry_action",
        )

    def test_single_download_starts_only_requested_registered_model(self) -> None:
        operation = {"running": True, "status": "starting"}
        with (
            patch.object(
                app_module,
                "model_spec",
                return_value=SimpleNamespace(key="mlx_clone"),
            ) as spec,
            patch.object(
                app_module,
                "_start_model_cache_operation",
                return_value=operation,
            ) as start,
        ):
            response = self.client.post(
                "/api/model_registry/action",
                json={"action": "download", "model_key": "mlx_clone"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "started")
        spec.assert_called_once_with("mlx_clone")
        start.assert_called_once_with(["mlx_clone"], "download")

    def test_download_required_selects_missing_and_incomplete_required_only(self) -> None:
        status = self._status(
            [
                self._model("required_missing", state="missing", required=True),
                self._model("required_incomplete", state="incomplete", required=True),
                self._model("optional_missing", state="missing", required=False),
                self._model("required_cached", state="cached", required=True),
            ]
        )
        with (
            patch.object(app_module, "model_registry_status", return_value=status),
            patch.object(
                app_module,
                "_start_model_cache_operation",
                return_value={"running": True, "status": "starting"},
            ) as start,
        ):
            response = self.client.post(
                "/api/model_registry/action",
                json={"action": "download_required"},
            )

        self.assertEqual(response.status_code, 200)
        start.assert_called_once_with(
            ["required_missing", "required_incomplete"],
            "download_required",
        )

    def test_download_required_is_noop_when_required_models_are_complete(self) -> None:
        status = self._status(
            [
                self._model("required_cached", state="cached", required=True),
                self._model("optional_missing", state="missing", required=False),
            ]
        )
        with patch.object(app_module, "model_registry_status", return_value=status):
            response = self.client.post(
                "/api/model_registry/action",
                json={"action": "download_required"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "already_cached")
        self.assertFalse(response.json()["operation"]["running"])

    def test_cancellation_is_cooperative_and_idempotent(self) -> None:
        app_module.process_state["model_cache"].update(
            {"running": True, "status": "running", "cancel_requested": False}
        )
        response = self.client.post("/api/model_registry/action/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cancelling")
        self.assertTrue(app_module.process_state["model_cache"]["cancel_requested"])

        app_module.process_state["model_cache"]["running"] = False
        app_module.process_state["model_cache"]["status"] = "cancelled"
        response = self.client.post("/api/model_registry/action/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cancelled")
        self.assertFalse(response.json()["cancel_requested"])

    def test_parallel_operation_is_rejected(self) -> None:
        app_module.process_state["model_cache"]["running"] = True
        response = self.client.post(
            "/api/model_registry/action",
            json={"action": "download", "model_key": "mlx_clone"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"]["code"],
            "model_cache_operation_running",
        )

    def test_background_operation_records_successful_progress(self) -> None:
        spec = SimpleNamespace(
            repo_id="owner/mlx-clone",
            revision="b" * 40,
        )
        with (
            patch.object(app_module, "model_spec", return_value=spec),
            patch.object(
                app_module,
                "download_or_repair_model",
                return_value={
                    "operation": "downloaded",
                    "state": "cached",
                    "snapshot_path": "/cache/mlx-clone",
                    "size_bytes": 1234,
                },
            ),
        ):
            app_module._run_model_cache_operation(["mlx_clone"], "download")

        state = app_module.process_state["model_cache"]
        self.assertFalse(state["running"])
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["completed_count"], 1)
        self.assertEqual(state["total_count"], 0)
        self.assertEqual(state["results"][0]["state"], "cached")
        self.assertIn("validated", " ".join(state["logs"]).casefold())

    def test_background_operation_preserves_actionable_failure_code(self) -> None:
        spec = SimpleNamespace(
            repo_id="owner/mlx-clone",
            revision="b" * 40,
        )
        with (
            patch.object(app_module, "model_spec", return_value=spec),
            patch.object(
                app_module,
                "download_or_repair_model",
                side_effect=ModelCacheOperationError(
                    "insufficient_model_cache_space",
                    "Not enough disk space.",
                    model_key="mlx_clone",
                ),
            ),
        ):
            app_module._run_model_cache_operation(["mlx_clone"], "download")

        state = app_module.process_state["model_cache"]
        self.assertFalse(state["running"])
        self.assertEqual(state["status"], "failed")
        self.assertEqual(
            state["error_code"],
            "insufficient_model_cache_space",
        )
        self.assertEqual(state["error"], "Not enough disk space.")


if __name__ == "__main__":
    unittest.main()
