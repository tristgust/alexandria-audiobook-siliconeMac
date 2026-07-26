from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "prepare_three_voice_paired_seed_reliability.py"
EVIDENCE = ROOT / ".omo" / "evidence" / "b17-t76-three-voice-paired-seed-reliability"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ThreeVoicePairedSeedReliabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module(SCRIPT, "prepare_three_voice_paired_seed_reliability")

    def test_design_is_three_routes_two_unique_seeds_and_hidden_repeat(self):
        self.assertEqual(len(self.module.ROUTE_GROUP_IDS), 3)
        self.assertEqual(len(self.module.RUNS), 3)
        self.assertEqual(len(self.module.ROUTES), 9)
        self.assertEqual(
            {row["generation_seed"] for row in self.module.RUNS},
            {104729, 130363},
        )
        self.assertEqual(self.module.RUNS[2]["repeat_of"], "run_1")
        self.assertEqual(self.module.RUNS[0]["generation_seed"], self.module.RUNS[2]["generation_seed"])
        self.assertEqual(
            {row["route_group_id"] for row in self.module.ROUTES},
            set(self.module.ROUTE_GROUP_IDS),
        )

    def test_generated_matrix_pairs_seed_within_every_ab_pair(self):
        matrix_path = EVIDENCE / "matrix.json"
        if not matrix_path.is_file():
            self.skipTest("Generated paired-seed matrix is not present.")
        matrix = json.loads(matrix_path.read_text())
        self.assertEqual(matrix["route_count"], 9)
        self.assertEqual(matrix["sample_count"], 18)
        self.assertTrue(matrix["paired_generation_seed"])
        self.assertTrue(matrix["same_seed_within_prompt_pair"])
        self.assertTrue(matrix["comparison_contract"]["prompt_role_excluded_from_seed"])
        by_route = {}
        for sample in matrix["samples"]:
            by_route.setdefault(sample["route_id"], []).append(sample)
        self.assertEqual(set(by_route), {row["route_id"] for row in matrix["routes"]})
        for samples in by_route.values():
            self.assertEqual(len(samples), 2)
            self.assertEqual({row["prompt_role"] for row in samples}, {"combined_bank", "legacy_reference"})
            self.assertEqual(len({row["generation_seed"] for row in samples}), 1)
            self.assertEqual(len({row["sample_id"][:8] for row in samples}), 1)

    def test_hidden_repeat_has_identical_inference_inputs(self):
        matrix_path = EVIDENCE / "matrix.json"
        if not matrix_path.is_file():
            self.skipTest("Generated paired-seed matrix is not present.")
        matrix = json.loads(matrix_path.read_text())
        routes = {row["route_id"]: row for row in matrix["routes"]}
        stable = (
            "generation_seed",
            "target_text",
            "alpha",
            "identity_audio_sha256",
            "bank_reference_audio_sha256",
            "legacy_reference_audio_sha256",
        )
        for group_id in self.module.ROUTE_GROUP_IDS:
            first = routes[f"{group_id}__run_1"]
            repeat = routes[f"{group_id}__run_3"]
            self.assertEqual({key: first[key] for key in stable}, {key: repeat[key] for key in stable})

    def test_fixed_seed_repeats_are_pcm_identical_when_present(self):
        path = EVIDENCE / "repeatability-analysis.json"
        if not path.is_file():
            self.skipTest("Generated repeatability analysis is not present.")
        payload = json.loads(path.read_text())
        self.assertEqual(payload["comparison_count"], 6)
        self.assertEqual(payload["exact_pcm_match_count"], 6)
        self.assertTrue(payload["fixed_seed_runtime_reproducible"])
        self.assertTrue(all(row["exact_pcm_match"] for row in payload["comparisons"]))
        self.assertFalse(payload["production_promotion_allowed"])

    def test_generated_package_remains_blinded_and_non_promoting(self):
        manifest_path = EVIDENCE / "review" / "manifest.json"
        data_path = EVIDENCE / "review" / "data.js"
        if not manifest_path.is_file() or not data_path.is_file():
            self.skipTest("Generated paired-seed review package is not present.")
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(manifest["candidate_count"], 9)
        self.assertEqual(manifest["route_group_count"], 3)
        self.assertEqual(manifest["runs_per_route_group"], 3)
        self.assertTrue(manifest["paired_generation_seed"])
        self.assertFalse(manifest["repeat_relationship_exposed"])
        self.assertFalse(manifest["candidate_mapping_exposed"])
        self.assertFalse(manifest["production_promotion_allowed"])
        text = data_path.read_text()
        self.assertNotIn("generation_seed", text)
        self.assertNotIn("repeat_of_run_id", text)
        self.assertNotIn("legacy_reference", text)

    def test_all_generated_samples_pass_automatic_gate(self):
        path = EVIDENCE / "analysis.json"
        if not path.is_file():
            self.skipTest("Generated paired-seed analysis is not present.")
        payload = json.loads(path.read_text())
        self.assertEqual(payload["sample_count"], 18)
        self.assertEqual(payload["technical_pass_count"], 18)
        self.assertTrue(all(row["technical_pass"] for row in payload["samples"]))
        self.assertTrue(all(row["production_promotion_allowed"] is False for row in payload["samples"]))


if __name__ == "__main__":
    unittest.main()
