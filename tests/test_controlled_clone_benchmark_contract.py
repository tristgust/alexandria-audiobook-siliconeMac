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
    / "20260717T031401Z_voxcpm2_controlled_clone.json"
)
RUNNER = ROOT / "benchmarks" / "run_controlled_clone_benchmark.py"


class ControlledCloneBenchmarkContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.result = json.loads(RESULT.read_text(encoding="utf-8"))

    def test_result_records_supplied_clip_controlled_clone(self) -> None:
        self.assertEqual(self.result["schema_version"], 1)
        self.assertEqual(self.result["backend"], "voxcpm2_controlled")
        self.assertEqual(
            self.result["model"],
            "mlx-community/VoxCPM2-4bit",
        )
        self.assertRegex(
            self.result["reference_audio_sha256"],
            r"^[0-9a-f]{64}$",
        )
        self.assertNotIn("reference_text", self.result)
        self.assertNotIn("test_text", self.result)

    def test_measured_paths_are_faster_than_or_near_real_time(self) -> None:
        measurements = self.result["measurements"]
        self.assertEqual(set(measurements), {"neutral", "expressive"})
        for item in measurements.values():
            self.assertLessEqual(item["real_time_factor"], 1.05)
        self.assertTrue(
            self.result["acceptance"][
                "faster_than_or_equal_to_real_time"
            ]
        )

    def test_supplied_voice_identity_passes_measured_floor(self) -> None:
        floor = self.result["acceptance"]["speaker_similarity_floor"]
        self.assertEqual(floor, 0.95)
        for item in self.result["measurements"].values():
            self.assertGreaterEqual(
                item["speaker_cosine_to_reference"],
                floor,
            )
        self.assertTrue(
            self.result["acceptance"]["speaker_identity_passed"]
        )
        self.assertTrue(
            self.result["acceptance"]["manual_audio_review_required"]
        )
        self.assertFalse(self.result["acceptance"]["production_default"])

    def test_peak_memory_note_discloses_evaluation_model(self) -> None:
        self.assertGreater(self.result["peak_process_rss_gib"], 0)
        self.assertIn(
            "separate Qwen speaker-evaluation model",
            self.result["peak_memory_note"],
        )

    def test_runner_is_importable_and_does_not_embed_user_text(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "controlled_clone_benchmark",
            RUNNER,
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        self.assertEqual(
            module.VOXCPM_MODEL,
            "mlx-community/VoxCPM2-4bit",
        )
        source = RUNNER.read_text(encoding="utf-8")
        self.assertIn("--reference-text", source)
        self.assertIn("--reference-audio", source)
        self.assertNotIn("Hector Thomas", source)


if __name__ == "__main__":
    unittest.main()
