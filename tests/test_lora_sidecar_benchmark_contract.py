from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = (
    ROOT
    / "benchmarks"
    / "results"
    / "20260717T040339Z_mps_lora_merged_mlx.json"
)
RUNNER = ROOT / "benchmarks" / "run_lora_sidecar_benchmark.py"


class LoraSidecarBenchmarkContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.result = json.loads(RESULT.read_text(encoding="utf-8"))

    def test_result_records_exact_experimental_architecture(self) -> None:
        self.assertEqual(self.result["schema_version"], 1)
        self.assertEqual(
            self.result["architecture"],
            "mps_lora_training_merged_mlx_inference_experimental",
        )
        self.assertFalse(self.result["shared_runtime_lora_supported"])
        self.assertTrue(
            self.result["experimental_sidecar_training_supported"]
        )
        self.assertFalse(
            self.result["direct_pytorch_inference_performant"]
        )
        self.assertTrue(
            self.result["merged_mlx_inference_technically_validated"]
        )
        self.assertFalse(self.result["production_assignment_supported"])

    def test_model_and_real_tts_targets_are_measured(self) -> None:
        probe = self.result["model_probe"]
        self.assertEqual(probe["device"], "mps")
        self.assertEqual(probe["dtype"], "torch.float32")
        self.assertGreater(probe["total_parameters"], 1_900_000_000)
        self.assertGreater(probe["talker_parameters"], 1_900_000_000)
        targets = self.result["target_probe"]
        self.assertEqual(targets["module_count"], 231)
        self.assertEqual(
            set(targets["target_suffixes"]),
            {
                "q_proj",
                "k_proj",
                "v_proj",
                "o_proj",
                "gate_proj",
                "up_proj",
                "down_proj",
            },
        )
        self.assertRegex(
            targets["actual_module_names_sha256"],
            r"^[0-9a-f]{64}$",
        )

    def test_mps_lora_training_step_is_measured_and_fast(self) -> None:
        training = self.result["training"]
        self.assertEqual(training["lora_rank"], 8)
        self.assertEqual(training["lora_alpha"], 16)
        self.assertEqual(training["steps_completed"], 1)
        self.assertEqual(training["trainable_parameters"], 9617408)
        self.assertLess(training["trainable_percent"], 1.0)
        step = training["step_metrics"][0]
        self.assertLess(step["step_seconds"], 5.0)
        self.assertGreater(step["mps_current_allocated_gib"], 1.0)
        self.assertGreater(step["mps_driver_allocated_gib"], 1.0)
        reference = training["reference_audio"]
        self.assertEqual(reference["sample_rate"], 24000)
        self.assertEqual(reference["channels"], 1)
        self.assertRegex(reference["sha256"], r"^[0-9a-f]{64}$")

    def test_pytorch_inference_is_explicitly_not_the_production_path(self) -> None:
        inference = self.result["pytorch_adapter_inference"]
        self.assertEqual(inference["device"], "mps")
        self.assertGreater(inference["real_time_factor"], 1.0)
        self.assertFalse(inference["production_assignment_supported"])

    def test_merged_mlx_inference_is_fast_and_retains_identity(self) -> None:
        export = self.result["mlx_export"]
        self.assertTrue(export["technical_validation_passed"])
        self.assertLess(export["size_bytes"], 4 * 1024**3)
        validation = export["validation"]
        self.assertTrue(validation["outputs_differ"])
        self.assertTrue(validation["identity_passed"])
        self.assertTrue(validation["steady_state_faster_than_real_time"])
        self.assertTrue(validation["instruction_channel_changed_output"])
        for item in validation["measurements"].values():
            self.assertLess(item["real_time_factor"], 1.0)
            self.assertGreaterEqual(
                item["speaker_cosine_to_reference"],
                0.95,
            )

    def test_quality_claim_remains_blocked(self) -> None:
        quality = self.result["quality_review"]
        self.assertTrue(quality["manual_audio_review_required"])
        self.assertEqual(quality["manual_audio_review_status"], "pending")
        self.assertTrue(
            quality["multi_sample_multi_epoch_validation_required"]
        )
        self.assertTrue(quality["one_step_probe_is_not_a_quality_claim"])

    def test_result_contains_hashes_not_supplied_text_or_local_paths(self) -> None:
        rendered = RESULT.read_text(encoding="utf-8")
        self.assertNotIn("Hector Thomas", rendered)
        self.assertNotIn("clone_voices/", rendered)
        self.assertNotIn("/Users/", rendered)
        self.assertNotIn("/private/tmp/", rendered)
        for key in (
            "validation_text_sha256",
            "neutral_instruction_sha256",
            "expressive_instruction_sha256",
        ):
            self.assertRegex(self.result[key], r"^[0-9a-f]{64}$")

    def test_runner_is_importable_and_exposes_deliberate_cli(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "lora_sidecar_benchmark",
            RUNNER,
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        self.assertEqual(
            module.ARCHITECTURE,
            "mps_lora_training_merged_mlx_inference_experimental",
        )
        source = RUNNER.read_text(encoding="utf-8")
        for flag in (
            "--data-dir",
            "--work-dir",
            "--output",
            "--cleanup-large-intermediates",
        ):
            self.assertIn(flag, source)
        self.assertIn("infer-adapter", source)
        self.assertIn("merge-adapter", source)
        self.assertIn("mlx_export.py", source)


if __name__ == "__main__":
    unittest.main()
