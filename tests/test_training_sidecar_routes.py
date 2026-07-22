from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from training_sidecar_api import TrainingSidecarApiError


class TrainingSidecarRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        source = self.root / "app" / "training_sidecar"
        source.mkdir(parents=True)
        (source / "requirements.txt").write_text(
            "qwen-tts==0.1.1\ntransformers==4.57.3\n",
            encoding="utf-8",
        )
        (source / "runner.py").write_text("print('{}')\n", encoding="utf-8")
        (source / "mlx_export.py").write_text("print('{}')\n", encoding="utf-8")
        self.root_patch = patch.object(
            app_module,
            "ROOT_DIR",
            str(self.root),
        )
        self.root_patch.start()
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.client.close()
        self.root_patch.stop()
        self.temp.cleanup()

    def test_status_is_model_free_and_file_pure(self) -> None:
        before = sorted(
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
        )
        response = self.client.get("/api/training_sidecar/status")
        after = sorted(
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["experimental"])
        self.assertFalse(payload["production_assignment_supported"])
        self.assertEqual(before, after)

    def test_create_read_and_execute_export_job(self) -> None:
        created = self.client.post(
            "/api/training_sidecar/jobs",
            json={
                "action": "export_mlx",
                "payload": {
                    "merged_dir": "training_sidecar_runtime/merged",
                    "output_dir": "lora_models/doctor/mlx_model",
                    "q_bits": 8,
                },
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        job = created.json()
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["action"], "export_mlx")

        read = self.client.get(
            f"/api/training_sidecar/jobs/{job['job_id']}"
        )
        self.assertEqual(read.status_code, 200, read.text)
        self.assertEqual(read.json(), job)

        completed = {
            **job,
            "status": "completed",
            "result": {
                "status": "validated_experimental",
                "technical_validation_passed": True,
                "production_assignment_supported": False,
            },
        }
        with patch.object(
            app_module,
            "execute_training_sidecar_job_payload",
            return_value=completed,
        ) as execute:
            response = self.client.post(
                f"/api/training_sidecar/jobs/{job['job_id']}/execute",
                json={"timeout": 300},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), completed)
        execute.assert_called_once_with(
            root_dir=str(self.root),
            job_id=job["job_id"],
            timeout=300.0,
        )

    def test_invalid_action_and_missing_job_are_machine_readable(self) -> None:
        invalid = self.client.post(
            "/api/training_sidecar/jobs",
            json={"action": "assign_production", "payload": {}},
        )
        self.assertEqual(invalid.status_code, 422, invalid.text)
        missing = self.client.get(
            "/api/training_sidecar/jobs/sidecar_000000000000000000000000"
        )
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(
            missing.json()["detail"]["code"],
            "training_sidecar_job_not_found",
        )

    def test_execute_error_translation_is_preserved(self) -> None:
        created = self.client.post(
            "/api/training_sidecar/jobs",
            json={"action": "environment", "payload": {}},
        ).json()
        with patch.object(
            app_module,
            "execute_training_sidecar_job_payload",
            side_effect=TrainingSidecarApiError(
                status_code=409,
                code="training_sidecar_conflict",
                detail="Already running.",
            ),
        ):
            response = self.client.post(
                f"/api/training_sidecar/jobs/{created['job_id']}/execute",
                json={},
            )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"],
            {
                "code": "training_sidecar_conflict",
                "message": "Already running.",
            },
        )

    def test_import_route_delegates_without_assignment(self) -> None:
        expected = {
            "status": "imported_experimental_unassigned",
            "production_assignment_supported": False,
        }
        with patch.object(
            app_module,
            "import_training_sidecar_artifact_payload",
            return_value=expected,
        ) as importer:
            response = self.client.post(
                "/api/training_sidecar/import",
                json={"source_path": "external/artifact"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), expected)
        importer.assert_called_once_with(
            root_dir=str(self.root),
            source_path="external/artifact",
        )

    def test_routes_are_registered_once(self) -> None:
        registrations = [
            (route.path, frozenset(getattr(route, "methods", set())))
            for route in app_module.app.routes
        ]
        expected = (
            ("/api/training_sidecar/status", "GET"),
            ("/api/training_sidecar/jobs/{job_id}", "GET"),
            ("/api/training_sidecar/jobs", "POST"),
            ("/api/training_sidecar/jobs/{job_id}/execute", "POST"),
            ("/api/training_sidecar/import", "POST"),
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
