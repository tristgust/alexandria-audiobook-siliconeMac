from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from instruction_propagation import build_instruction_propagation_contract
from training_sidecar_service import (
    TrainingSidecarConflictError,
    TrainingSidecarValidationError,
    build_sidecar_status,
    create_sidecar_job,
    execute_sidecar_job,
    import_external_sidecar_artifact,
    install_mlx_lora_artifact,
    read_sidecar_job,
    sha256_file,
    sidecar_environment_dir,
    sidecar_python_path,
    sidecar_sox_binary_path,
    sidecar_sox_environment_dir,
)


class FakeRun:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), dict(kwargs)))
        if not self.results:
            raise AssertionError("FakeRun ran out of results")
        return self.results.pop(0)


class TrainingSidecarServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "app" / "training_sidecar"
        self.source.mkdir(parents=True)
        (self.source / "requirements.txt").write_text(
            "qwen-tts==0.1.1\ntransformers==4.57.3\n",
            encoding="utf-8",
        )
        (self.source / "runner.py").write_text(
            "print('{}')\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create_fake_environment(self) -> None:
        python = sidecar_python_path(self.root)
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("#!/bin/sh\n", encoding="utf-8")

    @staticmethod
    def completed(
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=[],
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def test_status_is_file_pure_and_reports_separate_environment(self) -> None:
        before = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
        status = build_sidecar_status(self.root)
        after = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
        self.assertEqual(before, after)
        self.assertTrue(status["experimental"])
        self.assertFalse(status["production_assignment_supported"])
        self.assertFalse(status["environment_exists"])
        self.assertEqual(
            status["environment_path"],
            "app/training_sidecar/env",
        )
        self.assertNotEqual(
            sidecar_environment_dir(self.root),
            self.root / "app" / "env",
        )
        self.assertFalse(status["sox_binary_available"])
        self.assertEqual(
            status["sox_environment_path"],
            "app/training_sidecar/sox_env",
        )
        self.assertEqual(
            sidecar_sox_environment_dir(self.root),
            (
                self.root
                / "app"
                / "training_sidecar"
                / "sox_env"
            ).resolve(),
        )

    def test_setup_job_uses_sidecar_environment_only(self) -> None:
        job = create_sidecar_job(
            root_dir=self.root,
            action="setup",
            created_at_utc="2026-07-17T04:00:00Z",
        )
        fake = FakeRun(
            [
                self.completed(),
                self.completed(),
                self.completed(
                    stdout='{"status":"ready"}\n'
                ),
                self.completed(),
            ]
        )
        result = execute_sidecar_job(
            root_dir=self.root,
            job_id=job["job_id"],
            run=fake,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(fake.calls), 4)
        flattened = " ".join(
            token
            for command, _ in fake.calls
            for token in command
        )
        self.assertIn("app/training_sidecar/env", flattened)
        self.assertIn("app/training_sidecar/requirements.txt", flattened)
        self.assertIn("app/training_sidecar/sox_env", flattened)
        self.assertIn("conda-forge", flattened)
        self.assertIn("sox", flattened)
        self.assertNotIn(str(self.root / "app" / "env"), flattened)

    def test_runner_receives_managed_sox_path_when_available(self) -> None:
        self.create_fake_environment()
        sox = sidecar_sox_binary_path(self.root)
        sox.parent.mkdir(parents=True, exist_ok=True)
        sox.write_text("#!/bin/sh\n", encoding="utf-8")
        job = create_sidecar_job(
            root_dir=self.root,
            action="environment",
            payload={},
        )
        fake = FakeRun(
            [self.completed(stdout='{"status":"ready"}\n')]
        )
        execute_sidecar_job(
            root_dir=self.root,
            job_id=job["job_id"],
            run=fake,
        )
        environment = fake.calls[0][1]["env"]
        self.assertEqual(
            environment["ALEXANDRIA_SIDECAR_SOX"],
            str(sox),
        )
        self.assertTrue(environment["PATH"].startswith(str(sox.parent)))
        if sys.platform == "darwin":
            self.assertTrue(
                environment["DYLD_LIBRARY_PATH"].startswith(
                    str(sox.parent.parent / "lib")
                )
            )

    def test_environment_job_parses_json_and_records_bounded_logs(self) -> None:
        self.create_fake_environment()
        job = create_sidecar_job(
            root_dir=self.root,
            action="environment",
            payload={},
        )
        fake = FakeRun(
            [
                self.completed(
                    stdout=(
                        "diagnostic\n"
                        '{"status":"ready","default_device":"mps"}\n'
                    ),
                    stderr="warning",
                )
            ]
        )
        result = execute_sidecar_job(
            root_dir=self.root,
            job_id=job["job_id"],
            run=fake,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["default_device"], "mps")
        self.assertIn("diagnostic", result["stdout_tail"])
        self.assertEqual(
            read_sidecar_job(
                root_dir=self.root,
                job_id=job["job_id"],
            ),
            result,
        )

    def test_training_command_requires_project_relative_paths(self) -> None:
        self.create_fake_environment()
        job = create_sidecar_job(
            root_dir=self.root,
            action="train_sft",
            payload={
                "data_dir": "../escape",
                "output_dir": "training_sidecar_runtime/output",
            },
        )
        result = execute_sidecar_job(
            root_dir=self.root,
            job_id=job["job_id"],
            run=FakeRun([]),
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("project-relative", result["error"])

    def test_lora_training_command_carries_resume_split_and_target_contract(self) -> None:
        self.create_fake_environment()
        data = self.root / "lora_datasets" / "narrator"
        output = self.root / "training_sidecar_runtime" / "pilot_resume"
        checkpoint = (
            self.root
            / "training_sidecar_runtime"
            / "pilot_seed"
            / "checkpoints"
            / "epoch_0001"
        )
        data.mkdir(parents=True)
        checkpoint.mkdir(parents=True)
        job = create_sidecar_job(
            root_dir=self.root,
            action="train_lora",
            payload={
                "data_dir": data.relative_to(self.root).as_posix(),
                "output_dir": output.relative_to(self.root).as_posix(),
                "resume_from": checkpoint.relative_to(self.root).as_posix(),
                "device": "mps",
                "epochs": 3,
                "max_samples": 20,
                "learning_rate": 2e-5,
                "gradient_accumulation_steps": 2,
                "lora_rank": 8,
                "lora_alpha": 16,
                "lora_target_profile": "attention",
                "validation_fraction": 0.2,
                "seed": 20260719,
                "instruction_mode": "per_record",
                "checkpoint_every_epoch": False,
                "local_files_only": True,
            },
        )
        fake = FakeRun(
            [self.completed(stdout='{"status":"completed_experimental"}\n')]
        )
        result = execute_sidecar_job(
            root_dir=self.root,
            job_id=job["job_id"],
            run=fake,
        )
        self.assertEqual(result["status"], "completed")
        command = fake.calls[0][0]
        for flag, value in (
            ("--resume-from", str(checkpoint.resolve())),
            ("--lora-target-profile", "attention"),
            ("--validation-fraction", "0.2"),
            ("--seed", "20260719"),
            ("--instruction-mode", "per_record"),
            ("--lora-rank", "8"),
            ("--lora-alpha", "16"),
        ):
            with self.subTest(flag=flag):
                position = command.index(flag)
                self.assertEqual(command[position + 1], value)
        self.assertIn("--no-checkpoint-every-epoch", command)
        self.assertIn("--local-files-only", command)

    def test_lora_resume_path_must_remain_project_confined(self) -> None:
        self.create_fake_environment()
        job = create_sidecar_job(
            root_dir=self.root,
            action="train_lora",
            payload={
                "data_dir": "lora_datasets/narrator",
                "output_dir": "training_sidecar_runtime/pilot",
                "resume_from": "../escape/checkpoints/epoch_0001",
            },
        )
        result = execute_sidecar_job(
            root_dir=self.root,
            job_id=job["job_id"],
            run=FakeRun([]),
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("project-relative", result["error"])

    def test_merge_and_mlx_export_commands_remain_isolated(self) -> None:
        self.create_fake_environment()
        adapter = self.root / "training_sidecar_runtime" / "adapter"
        merged = self.root / "training_sidecar_runtime" / "merged"
        exported = self.root / "lora_models" / "doctor" / "mlx_model"
        adapter.mkdir(parents=True)

        merge_job = create_sidecar_job(
            root_dir=self.root,
            action="merge_lora",
            payload={
                "adapter_dir": adapter.relative_to(self.root).as_posix(),
                "output_dir": merged.relative_to(self.root).as_posix(),
                "device": "mps",
                "local_files_only": True,
            },
        )
        merge_run = FakeRun(
            [
                self.completed(
                    stdout='{"status":"merged_experimental"}\n'
                )
            ]
        )
        merge_result = execute_sidecar_job(
            root_dir=self.root,
            job_id=merge_job["job_id"],
            run=merge_run,
        )
        self.assertEqual(merge_result["status"], "completed")
        merge_command = merge_run.calls[0][0]
        self.assertEqual(merge_command[0], str(sidecar_python_path(self.root)))
        self.assertIn("merge-adapter", merge_command)
        self.assertIn(str(adapter.resolve()), merge_command)
        self.assertIn(str(merged.resolve()), merge_command)
        self.assertIn("--local-files-only", merge_command)

        merged.mkdir(parents=True)
        export_job = create_sidecar_job(
            root_dir=self.root,
            action="export_mlx",
            payload={
                "merged_dir": merged.relative_to(self.root).as_posix(),
                "output_dir": exported.relative_to(self.root).as_posix(),
                "q_bits": 8,
                "cleanup_merged": True,
            },
        )
        export_run = FakeRun(
            [
                self.completed(
                    stdout='{"status":"validated_experimental"}\n'
                )
            ]
        )
        export_result = execute_sidecar_job(
            root_dir=self.root,
            job_id=export_job["job_id"],
            run=export_run,
        )
        self.assertEqual(export_result["status"], "completed")
        export_command = export_run.calls[0][0]
        self.assertEqual(export_command[0], sys.executable)
        self.assertIn("mlx_export.py", " ".join(export_command))
        self.assertIn(str(merged.resolve()), export_command)
        self.assertIn(str(exported.resolve()), export_command)
        self.assertIn("--cleanup-merged", export_command)

    def test_merge_and_export_reject_path_traversal(self) -> None:
        self.create_fake_environment()
        for action, payload in (
            (
                "merge_lora",
                {
                    "adapter_dir": "../escape",
                    "output_dir": "training_sidecar_runtime/merged",
                },
            ),
            (
                "export_mlx",
                {
                    "merged_dir": "training_sidecar_runtime/merged",
                    "output_dir": "../../escape",
                },
            ),
        ):
            with self.subTest(action=action):
                job = create_sidecar_job(
                    root_dir=self.root,
                    action=action,
                    payload=payload,
                )
                result = execute_sidecar_job(
                    root_dir=self.root,
                    job_id=job["job_id"],
                    run=FakeRun([]),
                )
                self.assertEqual(result["status"], "failed")
                self.assertIn("project-relative", result["error"])

    def test_failed_subprocess_is_recorded_without_retry(self) -> None:
        self.create_fake_environment()
        job = create_sidecar_job(
            root_dir=self.root,
            action="model_probe",
            payload={"device": "mps", "local_files_only": True},
        )
        fake = FakeRun(
            [
                self.completed(
                    returncode=2,
                    stdout='{"status":"failed","error":"checkpoint absent"}\n',
                    stderr="traceback",
                )
            ]
        )
        result = execute_sidecar_job(
            root_dir=self.root,
            job_id=job["job_id"],
            run=fake,
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "checkpoint absent")
        with self.assertRaises(TrainingSidecarConflictError):
            execute_sidecar_job(
                root_dir=self.root,
                job_id=job["job_id"],
                run=fake,
            )

    def mlx_export_fixture(self) -> tuple[Path, Path]:
        export = self.root / "training_sidecar_runtime" / "mlx_export"
        (export / "speech_tokenizer").mkdir(parents=True)
        files = {
            "model.safetensors": b"mlx-model",
            "config.json": b"{}",
            "ref_sample.wav": b"reference",
            "ref_sample.txt": b"Exact transcript.",
            "validation_neutral.wav": b"neutral",
            "validation_expressive.wav": b"expressive",
            "speech_tokenizer/model.safetensors": b"tokenizer",
        }
        for relative, content in files.items():
            path = export / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

        training = self.root / "training_sidecar_runtime" / "training"
        training.mkdir(parents=True)
        training_manifest = training / "sidecar_artifact.json"
        training_manifest.write_text(
            json.dumps({"artifact": "adapter"}),
            encoding="utf-8",
        )
        propagation = build_instruction_propagation_contract(
            mode="per_record",
            samples=[
                {
                    "source_index": 0,
                    "instruction": "Calm, measured narration.",
                }
            ],
        )
        metrics = {
            "mode": "lora",
            "epochs_completed": 3,
            "dataset": {"prepared_count": 32},
            "training_contract": {
                "lora_rank": 8,
                "learning_rate": 2e-5,
                "target_profile": "attention",
                "instruction_propagation": propagation,
            },
            "validation_metrics": [
                {
                    "epoch": 3,
                    "train_loss": 4.2,
                    "validation": {"loss": 4.1},
                }
            ],
        }
        metrics_path = training / "training_metrics.json"
        metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
        inventory = [
            {
                "path": relative,
                "sha256": sha256_file(export / relative),
                "size_bytes": (export / relative).stat().st_size,
            }
            for relative in sorted(files)
        ]
        manifest = {
            "schema_version": 1,
            "artifact_format": "merged_mlx_qwen_checkpoint",
            "status": "validated_experimental",
            "base_model": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
            "base_model_revision": "a" * 40,
            "source_adapter_manifest_sha256": sha256_file(training_manifest),
            "technical_validation_passed": True,
            "production_assignment_supported": False,
            "instruction_propagation": propagation,
            "export_fingerprint": "b" * 64,
            "validation": {
                "manual_audio_review_status": "pending",
                "measurements": {
                    "neutral": {
                        "real_time_factor": 0.6,
                        "speaker_cosine_to_reference": 0.99,
                    },
                    "expressive": {
                        "real_time_factor": 0.4,
                        "speaker_cosine_to_reference": 0.98,
                    },
                },
            },
            "files": inventory,
        }
        (export / "mlx_export_manifest.json").write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )
        return export, metrics_path

    def test_validated_mlx_export_installs_as_unassigned_lora_artifact(self) -> None:
        export, metrics = self.mlx_export_fixture()
        result = install_mlx_lora_artifact(
            root_dir=self.root,
            source_path=export.relative_to(self.root).as_posix(),
            adapter_id="narrator_attention_r8",
            name="Narrator Attention R8",
            dataset_id="narrator_attempt1",
            training_metrics_path=metrics.relative_to(self.root).as_posix(),
            installed_at_utc="2026-07-19T20:00:00Z",
        )
        self.assertEqual(
            result["status"],
            "installed_experimental_unassigned",
        )
        self.assertFalse(result["production_assignment_supported"])
        target = self.root / result["adapter_path"]
        self.assertTrue((target / "mlx_model" / "model.safetensors").is_file())
        self.assertEqual(
            (target / "ref_sample.txt").read_text(encoding="utf-8"),
            "Exact transcript.",
        )
        self.assertEqual(
            (target / "preview_sample.wav").read_bytes(),
            b"neutral",
        )
        training_meta = json.loads(
            (target / "training_meta.json").read_text(encoding="utf-8")
        )
        self.assertFalse(training_meta["production_assignment_supported"])
        self.assertEqual(
            training_meta["instruction_propagation"]["mode"],
            "per_record",
        )
        registered = json.loads(
            (self.root / "lora_models" / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(registered[0]["id"], "narrator_attention_r8")
        self.assertEqual(registered[0]["validation_loss"], 4.1)
        self.assertEqual(registered[0]["speaker_cosine_floor"], 0.98)
        self.assertEqual(registered[0]["instruction_mode"], "per_record")
        self.assertTrue(registered[0]["instruction_required_at_inference"])
        with self.assertRaises(TrainingSidecarConflictError):
            install_mlx_lora_artifact(
                root_dir=self.root,
                source_path=export.relative_to(self.root).as_posix(),
                adapter_id="narrator_attention_r8",
                name="Duplicate",
            )

    def test_mlx_install_rejects_instruction_propagation_mismatch(self) -> None:
        export, metrics = self.mlx_export_fixture()
        manifest_path = export / "mlx_export_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["instruction_propagation"] = (
            build_instruction_propagation_contract(
                mode="identity_only",
                samples=[],
            )
        )
        manifest_path.write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            TrainingSidecarValidationError,
            "instruction propagation do not match",
        ):
            install_mlx_lora_artifact(
                root_dir=self.root,
                source_path=export.relative_to(self.root).as_posix(),
                adapter_id="mismatched_propagation",
                name="Mismatched propagation",
                training_metrics_path=metrics.relative_to(self.root).as_posix(),
            )

    def test_mlx_install_rejects_tampering_and_unsafe_id(self) -> None:
        export, _ = self.mlx_export_fixture()
        (export / "model.safetensors").write_bytes(b"tampered")
        with self.assertRaisesRegex(
            TrainingSidecarValidationError,
            "hash does not match",
        ):
            install_mlx_lora_artifact(
                root_dir=self.root,
                source_path=export.relative_to(self.root).as_posix(),
                adapter_id="narrator_attention_r8",
                name="Narrator",
            )
        with self.assertRaisesRegex(
            TrainingSidecarValidationError,
            "lowercase",
        ):
            install_mlx_lora_artifact(
                root_dir=self.root,
                source_path=export.relative_to(self.root).as_posix(),
                adapter_id="../escape",
                name="Narrator",
            )

    def artifact_fixture(self) -> Path:
        directory = self.root / "external" / "artifact"
        directory.mkdir(parents=True)
        adapter = directory / "adapter_model.safetensors"
        adapter.write_bytes(b"experimental adapter")
        manifest = {
            "schema_version": 1,
            "artifact_format": "peft_lora_adapter",
            "status": "experimental_unassigned",
            "base_model": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
            "training_device": "cuda",
            "dataset_path": "/external/dataset",
            "created_at_utc": "2026-07-17T04:00:00Z",
            "metrics": {"steps_completed": 10},
            "files": [
                {
                    "path": adapter.name,
                    "sha256": sha256_file(adapter),
                    "size_bytes": adapter.stat().st_size,
                }
            ],
            "production_assignment_supported": False,
        }
        (directory / "sidecar_artifact.json").write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )
        return directory

    def test_external_artifact_import_is_validated_and_unassigned(self) -> None:
        directory = self.artifact_fixture()
        result = import_external_sidecar_artifact(
            root_dir=self.root,
            source_path=directory.relative_to(self.root).as_posix(),
            imported_at_utc="2026-07-17T04:05:00Z",
        )
        self.assertEqual(
            result["status"],
            "imported_experimental_unassigned",
        )
        self.assertFalse(result["production_assignment_supported"])
        target = self.root / result["target_path"]
        self.assertTrue((target / "adapter_model.safetensors").is_file())
        with self.assertRaises(TrainingSidecarConflictError):
            import_external_sidecar_artifact(
                root_dir=self.root,
                source_path=directory.relative_to(self.root).as_posix(),
                imported_at_utc="2026-07-17T04:05:00Z",
            )

    def test_external_import_rejects_tampered_hash_and_assignment_claim(self) -> None:
        directory = self.artifact_fixture()
        manifest_path = directory / "sidecar_artifact.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["production_assignment_supported"] = True
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(
            TrainingSidecarValidationError,
            "cannot claim production assignment",
        ):
            import_external_sidecar_artifact(
                root_dir=self.root,
                source_path=directory.relative_to(self.root).as_posix(),
            )

        manifest["production_assignment_supported"] = False
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        (directory / "adapter_model.safetensors").write_bytes(b"tampered")
        with self.assertRaisesRegex(
            TrainingSidecarValidationError,
            "hash does not match",
        ):
            import_external_sidecar_artifact(
                root_dir=self.root,
                source_path=directory.relative_to(self.root).as_posix(),
            )

    def test_invalid_job_id_and_action_are_rejected(self) -> None:
        with self.assertRaises(TrainingSidecarValidationError):
            read_sidecar_job(
                root_dir=self.root,
                job_id="../job",
            )
        with self.assertRaises(TrainingSidecarValidationError):
            create_sidecar_job(
                root_dir=self.root,
                action="assign_production_voice",
            )


if __name__ == "__main__":
    unittest.main()
