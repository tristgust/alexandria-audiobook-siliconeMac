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
    / "20260717T014952Z_phase22_apple_silicon.json"
)
RUNNER = ROOT / "benchmarks" / "run_phase22_benchmarks.py"


class Phase22BenchmarkContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.result = json.loads(RESULT.read_text(encoding="utf-8"))

    def test_result_has_exact_honest_stable_outcome(self) -> None:
        self.assertEqual(self.result["schema_version"], 1)
        self.assertEqual(
            self.result["stable_lora_outcome"],
            "unsupported",
        )
        self.assertIn("must not expose", self.result["stable_lora_reason"])
        self.assertGreaterEqual(len(self.result["lora_blockers"]), 5)

    def test_hardware_and_mps_probe_match_target_machine(self) -> None:
        hardware = self.result["hardware"]
        self.assertIn("M2 Max", hardware.get("chip", ""))
        self.assertIn("96", hardware.get("memory", ""))
        probe = self.result["mps_probe"]
        self.assertTrue(probe["mps_built"])
        self.assertTrue(probe["mps_available"])
        self.assertTrue(probe["basic_autograd"])
        self.assertTrue(probe["gradient_finite"])

    def test_qwen_tts_import_failure_is_recorded(self) -> None:
        probe = self.result["qwen_tts_import_probe"]
        self.assertFalse(probe["imported"])
        self.assertEqual(probe["error_type"], "TypeError")
        self.assertIn("check_model_inputs", probe["error"])
        packages = self.result["environment"]["packages"]
        self.assertEqual(packages["qwen-tts"], "0.1.1")
        self.assertEqual(packages["mlx-audio"], "0.4.5")
        self.assertEqual(packages["transformers"], "5.12.1")
        self.assertFalse(self.result["environment"]["sox_available"])

    def test_existing_llm_readiness_measurement_is_included(self) -> None:
        llm = self.result["llm_measurement"]
        self.assertIsNotNone(llm)
        self.assertEqual(llm["schema_success_rate"], 1.0)
        self.assertEqual(llm["script_audit_pass_rate"], 1.0)
        self.assertEqual(llm["review_audit_pass_rate"], 1.0)
        self.assertGreater(llm["average_tokens_per_second"], 60.0)
        self.assertLess(llm["average_case_seconds"], 3.0)

    def test_all_required_tts_paths_are_measured(self) -> None:
        measurements = self.result["tts_measurements"]
        expected = {
            "voice_design",
            "voice_design_generated_clone",
            "custom_voice",
            "accent_pipeline",
            "mixed_length_custom_batch",
        }
        self.assertEqual(set(measurements), expected)
        self.assertLess(measurements["voice_design"]["warm_rtf"], 1.0)
        self.assertLess(
            measurements["voice_design_generated_clone"]["warm_rtf"],
            1.0,
        )
        self.assertLess(measurements["custom_voice"]["warm_rtf"], 1.0)
        self.assertGreater(measurements["accent_pipeline"]["rtf"], 1.0)
        mixed = measurements["mixed_length_custom_batch"]
        self.assertEqual(mixed["implementation"], "sequential_loop")
        self.assertLess(mixed["aggregate_rtf"], 1.0)
        self.assertEqual(len(mixed["items"]), 3)

    def test_quality_scope_does_not_claim_unmeasured_lora_or_preference(self) -> None:
        scope = self.result["quality_comparison_scope"]
        self.assertIn("not runnable", scope["lora"])
        self.assertEqual(scope["user_preference"], "not collected")

    def test_runner_discovers_existing_llm_benchmark_by_content(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "phase22_runner",
            RUNNER,
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        measurement = module.existing_llm_benchmark()
        self.assertIsNotNone(measurement)
        self.assertEqual(measurement["schema_success_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
