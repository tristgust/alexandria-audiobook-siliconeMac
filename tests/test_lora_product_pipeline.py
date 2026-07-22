from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module


class LoraProductPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.datasets = self.root / "lora_datasets"
        self.models = self.root / "lora_models"
        self.dataset = self.datasets / "reviewed_voice"
        self.dataset.mkdir(parents=True)
        self.models.mkdir(parents=True)
        (self.dataset / "metadata.jsonl").write_text(
            json.dumps(
                {
                    "audio_filepath": "sample.wav",
                    "text": "A reviewed line.",
                    "review_status": "approved",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.patches = [
            patch.object(app_module, "ROOT_DIR", str(self.root)),
            patch.object(app_module, "LORA_DATASETS_DIR", str(self.datasets)),
            patch.object(app_module, "LORA_MODELS_DIR", str(self.models)),
            patch.object(
                app_module,
                "LORA_MODELS_MANIFEST",
                str(self.models / "manifest.json"),
            ),
            patch.object(
                app_module,
                "_current_voice_backend_capabilities",
                return_value={
                    "experimental_lora_sidecar": {
                        "training_supported": True,
                        "training_device": "mps",
                    },
                    "lora_inference_supported": True,
                },
            ),
        ]
        for item in self.patches:
            item.start()
        self.state = app_module.process_state["lora_training"]
        self.state.update(
            {
                "running": False,
                "logs": [],
                "stage": "idle",
                "adapter_id": None,
                "job_id": None,
                "result": None,
                "error": None,
                "failed_stage": None,
            }
        )
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.client.close()
        self.state.update(
            {
                "running": False,
                "logs": [],
                "stage": "idle",
                "adapter_id": None,
                "job_id": None,
                "result": None,
                "error": None,
                "failed_stage": None,
            }
        )
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def request_payload(self) -> dict:
        return {
            "name": "Reviewed Voice",
            "dataset_id": "reviewed_voice",
            "epochs": 3,
            "lr": 2e-5,
            "batch_size": 1,
            "lora_r": 8,
            "lora_alpha": 16,
            "gradient_accumulation_steps": 2,
            "language": "english",
            "lora_target_profile": "attention",
            "validation_fraction": 0.2,
            "seed": 20260719,
            "instruction_mode": "per_record",
            "max_samples": 20,
            "max_audio_seconds": 15,
            "local_files_only": True,
        }

    def test_route_queues_isolated_pipeline_instead_of_legacy_trainer(self) -> None:
        with patch.object(app_module, "_run_lora_product_pipeline") as pipeline:
            response = self.client.post(
                "/api/lora/train",
                json=self.request_payload(),
            )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "started")
        self.assertTrue(payload["experimental"])
        self.assertFalse(payload["production_assignment_supported"])
        self.assertRegex(
            payload["adapter_id"],
            r"^reviewed_voice_[0-9]+_[0-9a-f]{6}$",
        )
        pipeline.assert_called_once()
        kwargs = pipeline.call_args.kwargs
        self.assertEqual(kwargs["dataset_relative"], "lora_datasets/reviewed_voice")
        self.assertEqual(kwargs["request_payload"]["lora_target_profile"], "attention")
        self.assertEqual(kwargs["request_payload"]["validation_fraction"], 0.2)
        self.assertEqual(kwargs["request_payload"]["instruction_mode"], "per_record")
        self.assertIn(payload["adapter_id"], kwargs["experiment_relative"])
        self.assertTrue(self.state["running"])
        self.assertEqual(self.state["stage"], "queued")

    def test_route_rejects_nonsequential_batch_and_concurrent_run(self) -> None:
        invalid = {**self.request_payload(), "batch_size": 2}
        response = self.client.post("/api/lora/train", json=invalid)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("batch size 1", response.json()["detail"])

        self.state["running"] = True
        response = self.client.post(
            "/api/lora/train",
            json=self.request_payload(),
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("already running", response.json()["detail"])

    def test_pipeline_runs_train_merge_export_and_atomic_install(self) -> None:
        request = self.request_payload()
        jobs = {
            "train_lora": "sidecar_" + "1" * 24,
            "merge_lora": "sidecar_" + "2" * 24,
            "export_mlx": "sidecar_" + "3" * 24,
        }

        def create_job(*, root_dir, action, payload):
            self.assertEqual(root_dir, str(self.root))
            if action == "train_lora":
                self.assertEqual(payload["instruction_mode"], "per_record")
            return {"job_id": jobs[action], "action": action, "payload": payload}

        results = {
            jobs["train_lora"]: {
                "status": "completed",
                "result": {
                    "status": "completed_experimental",
                    "metrics": {
                        "epochs_completed": 3,
                        "validation_metrics": [
                            {
                                "epoch": 1,
                                "train_loss": 4.5,
                                "validation": {"loss": 4.4},
                            },
                            {
                                "epoch": 2,
                                "train_loss": 4.3,
                                "validation": {"loss": 4.2},
                            },
                            {
                                "epoch": 3,
                                "train_loss": 4.1,
                                "validation": {"loss": 4.0},
                            },
                        ],
                        "quality_gate": {
                            "dataset_reviewed": True,
                            "production_assignment_supported": False,
                        },
                    },
                },
            },
            jobs["merge_lora"]: {
                "status": "completed",
                "result": {
                    "status": "merged_experimental",
                    "production_assignment_supported": False,
                },
            },
            jobs["export_mlx"]: {
                "status": "completed",
                "result": {
                    "status": "validated_experimental",
                    "technical_validation_passed": True,
                    "production_assignment_supported": False,
                },
            },
        }

        def execute_job(*, root_dir, job_id, timeout):
            self.assertEqual(root_dir, str(self.root))
            self.assertGreater(timeout, 0)
            return results[job_id]

        installed = {
            "status": "installed_experimental_unassigned",
            "adapter_path": "lora_models/reviewed_voice_fixture",
            "mlx_model_path": "lora_models/reviewed_voice_fixture/mlx_model",
            "production_assignment_supported": False,
        }
        self.state.update(
            {
                "running": True,
                "logs": [],
                "stage": "queued",
                "adapter_id": "reviewed_voice_fixture",
                "job_id": None,
                "result": None,
                "error": None,
                "failed_stage": None,
            }
        )
        with (
            patch.object(
                app_module,
                "create_training_sidecar_job_payload",
                side_effect=create_job,
            ) as create,
            patch.object(
                app_module,
                "execute_training_sidecar_job_payload",
                side_effect=execute_job,
            ) as execute,
            patch.object(
                app_module,
                "install_training_sidecar_mlx_artifact_payload",
                return_value=installed,
            ) as install,
        ):
            app_module._run_lora_product_pipeline(
                request_payload=request,
                adapter_id="reviewed_voice_fixture",
                dataset_relative="lora_datasets/reviewed_voice",
                experiment_relative=(
                    "training_sidecar_runtime/lora_experiments/"
                    "reviewed_voice_fixture"
                ),
            )

        self.assertEqual(
            [call.kwargs["action"] for call in create.call_args_list],
            ["train_lora", "merge_lora", "export_mlx"],
        )
        self.assertEqual(execute.call_count, 3)
        install.assert_called_once()
        install_kwargs = install.call_args.kwargs
        self.assertEqual(install_kwargs["adapter_id"], "reviewed_voice_fixture")
        self.assertEqual(install_kwargs["dataset_id"], "reviewed_voice")
        self.assertTrue(install_kwargs["source_path"].endswith("/mlx"))
        self.assertTrue(
            install_kwargs["training_metrics_path"].endswith(
                "/training/training_metrics.json"
            )
        )
        self.assertFalse(self.state["running"])
        self.assertEqual(self.state["stage"], "complete")
        self.assertIsNone(self.state["error"])
        self.assertEqual(
            self.state["result"]["adapter_id"],
            "reviewed_voice_fixture",
        )
        self.assertFalse(
            self.state["result"]["production_assignment_supported"]
        )
        self.assertEqual(
            sum("[EPOCH]" in line for line in self.state["logs"]),
            3,
        )
        self.assertTrue(any("[DONE]" in line for line in self.state["logs"]))

    def test_pipeline_failure_never_calls_installer_or_claims_completion(self) -> None:
        request = self.request_payload()
        self.state.update(
            {
                "running": True,
                "logs": [],
                "stage": "queued",
                "adapter_id": "broken_fixture",
                "job_id": None,
                "result": None,
                "error": None,
                "failed_stage": None,
            }
        )
        with (
            patch.object(
                app_module,
                "create_training_sidecar_job_payload",
                return_value={"job_id": "sidecar_" + "4" * 24},
            ),
            patch.object(
                app_module,
                "execute_training_sidecar_job_payload",
                return_value={
                    "status": "failed",
                    "error": "synthetic MPS failure",
                    "result": None,
                },
            ),
            patch.object(
                app_module,
                "install_training_sidecar_mlx_artifact_payload",
            ) as install,
        ):
            app_module._run_lora_product_pipeline(
                request_payload=request,
                adapter_id="broken_fixture",
                dataset_relative="lora_datasets/reviewed_voice",
                experiment_relative=(
                    "training_sidecar_runtime/lora_experiments/broken_fixture"
                ),
            )
        install.assert_not_called()
        self.assertFalse(self.state["running"])
        self.assertEqual(self.state["stage"], "failed")
        self.assertEqual(self.state["failed_stage"], "training")
        self.assertEqual(self.state["error"], "synthetic MPS failure")
        self.assertIsNone(self.state["result"])
        self.assertTrue(any("[ERROR]" in line for line in self.state["logs"]))
        self.assertFalse(any("[DONE]" in line for line in self.state["logs"]))


if __name__ == "__main__":
    unittest.main()
