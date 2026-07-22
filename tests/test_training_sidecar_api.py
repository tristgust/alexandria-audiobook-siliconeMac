from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from training_sidecar_api import (
    TrainingSidecarApiError,
    create_training_sidecar_job_payload,
    execute_training_sidecar_job_payload,
    get_training_sidecar_job_payload,
    get_training_sidecar_status_payload,
    import_training_sidecar_artifact_payload,
    install_training_sidecar_mlx_artifact_payload,
)
from training_sidecar_service import sha256_file, sidecar_python_path


class TrainingSidecarApiTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create_environment(self) -> None:
        python = sidecar_python_path(self.root)
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("#!/bin/sh\n", encoding="utf-8")

    def test_status_is_model_free_and_file_pure(self) -> None:
        before = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
        status = get_training_sidecar_status_payload(root_dir=self.root)
        after = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
        self.assertEqual(before, after)
        self.assertTrue(status["experimental"])
        self.assertFalse(status["production_assignment_supported"])

    def test_create_read_and_execute_job_payload(self) -> None:
        self.create_environment()
        job = create_training_sidecar_job_payload(
            root_dir=self.root,
            action="environment",
        )

        def fake_run(*args, **kwargs):
            del args, kwargs
            return subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout='{"status":"ready","default_device":"mps"}\n',
                stderr="",
            )

        completed = execute_training_sidecar_job_payload(
            root_dir=self.root,
            job_id=job["job_id"],
            run=fake_run,
        )
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["result"]["default_device"], "mps")
        self.assertEqual(
            get_training_sidecar_job_payload(
                root_dir=self.root,
                job_id=job["job_id"],
            ),
            completed,
        )

    def test_invalid_action_and_missing_job_are_machine_readable(self) -> None:
        with self.assertRaises(TrainingSidecarApiError) as action:
            create_training_sidecar_job_payload(
                root_dir=self.root,
                action="assign_voice",
            )
        self.assertEqual(action.exception.status_code, 422)
        self.assertEqual(action.exception.code, "training_sidecar_rejected")
        with self.assertRaises(TrainingSidecarApiError) as missing:
            get_training_sidecar_job_payload(
                root_dir=self.root,
                job_id="sidecar_000000000000000000000000",
            )
        self.assertEqual(missing.exception.status_code, 404)
        self.assertEqual(missing.exception.code, "training_sidecar_job_not_found")

    def test_install_mlx_payload_preserves_experimental_unassigned_state(self) -> None:
        expected = {
            "status": "installed_experimental_unassigned",
            "adapter_id": "voice_pilot",
            "production_assignment_supported": False,
        }
        with patch(
            "training_sidecar_api.install_mlx_lora_artifact",
            return_value=expected,
        ) as installer:
            result = install_training_sidecar_mlx_artifact_payload(
                root_dir=self.root,
                source_path="training_sidecar_runtime/export",
                adapter_id="voice_pilot",
                name="Voice Pilot",
                dataset_id="reviewed_voice",
                training_metrics_path=(
                    "training_sidecar_runtime/training/training_metrics.json"
                ),
                installed_at_utc="2026-07-19T22:00:00Z",
            )
        self.assertEqual(result, expected)
        installer.assert_called_once_with(
            root_dir=self.root,
            source_path="training_sidecar_runtime/export",
            adapter_id="voice_pilot",
            name="Voice Pilot",
            dataset_id="reviewed_voice",
            training_metrics_path=(
                "training_sidecar_runtime/training/training_metrics.json"
            ),
            installed_at_utc="2026-07-19T22:00:00Z",
        )

    def test_import_payload_preserves_experimental_unassigned_state(self) -> None:
        source = self.root / "external" / "artifact"
        source.mkdir(parents=True)
        adapter = source / "adapter.safetensors"
        adapter.write_bytes(b"adapter")
        manifest = {
            "schema_version": 1,
            "artifact_format": "peft_lora_adapter",
            "status": "experimental_unassigned",
            "base_model": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
            "training_device": "cuda",
            "dataset_path": "/external/dataset",
            "created_at_utc": "2026-07-17T05:00:00Z",
            "metrics": {"steps_completed": 1},
            "files": [
                {
                    "path": adapter.name,
                    "sha256": sha256_file(adapter),
                    "size_bytes": adapter.stat().st_size,
                }
            ],
            "production_assignment_supported": False,
        }
        (source / "sidecar_artifact.json").write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )
        imported = import_training_sidecar_artifact_payload(
            root_dir=self.root,
            source_path=source.relative_to(self.root).as_posix(),
        )
        self.assertEqual(
            imported["status"],
            "imported_experimental_unassigned",
        )
        self.assertFalse(imported["production_assignment_supported"])


if __name__ == "__main__":
    unittest.main()
