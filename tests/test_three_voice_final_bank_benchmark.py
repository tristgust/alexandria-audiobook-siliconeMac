from __future__ import annotations

import importlib.util
import json
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "prepare_three_voice_final_bank_benchmark.py"
EVIDENCE_ROOT = ROOT / ".omo" / "evidence" / "b17-t74-three-voice-final-bank-benchmark"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ThreeVoiceFinalBankBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module(SCRIPT, "prepare_three_voice_final_bank_benchmark")

    def test_route_matrix_is_balanced_and_excludes_open_gaps(self) -> None:
        routes = self.module.ROUTES
        self.assertEqual(len(routes), 6)
        self.assertEqual(
            Counter(row["target"] for row in routes),
            Counter({"narrator": 2, "benny": 2, "doctor": 2}),
        )
        for row in routes:
            with self.subTest(route_id=row["route_id"]):
                self.assertNotIn(row["function"], self.module.OPEN_GAPS[row["target"]])
                self.assertTrue(row["bank_clip_id"])
                self.assertTrue(row["target_text"])
                self.assertGreater(row["alpha"], 0.0)
                self.assertLessEqual(row["alpha"], 1.0)

    def test_exact_validated_reference_routes(self) -> None:
        mapping = {row["route_id"]: row["bank_clip_id"] for row in self.module.ROUTES}
        self.assertEqual(
            mapping,
            {
                "narrator_anger_control": "narrator_ud_explosive_indignation",
                "narrator_joy_tuned": "narrator_ud_ecstatic_bucket_affection",
                "benny_fatalistic_dread": "benny_hesitation_fatalistic_dread",
                "benny_sardonic_concern": "benny_criminal_sardonic_concern",
                "doctor_playful_identity": "doctor_acf_playful_introduction",
                "doctor_dismissive_contempt": "doctor_acf_dismissive_contempt",
            },
        )
        self.assertIn("urgency", self.module.OPEN_GAPS["doctor"])
        self.assertEqual(self.module.EXPECTED_REFERENCE_COUNT, 31)

    def test_generated_matrix_uses_only_approved_final_bank_references(self) -> None:
        matrix_path = EVIDENCE_ROOT / "matrix.json"
        if not matrix_path.is_file():
            self.skipTest("Generated final-bank benchmark matrix is not present.")
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        self.assertEqual(matrix["round_id"], self.module.ROUND_ID)
        self.assertEqual(matrix["combined_bank"]["reference_count"], 31)
        self.assertEqual(matrix["route_count"], 6)
        self.assertEqual(matrix["sample_count"], 12)
        self.assertTrue(matrix["comparison_contract"]["all_bank_prompts_human_validated"])
        self.assertTrue(matrix["comparison_contract"]["only_performance_prompt_changes"])
        for route in matrix["routes"]:
            with self.subTest(route_id=route["route_id"]):
                self.assertTrue(route["bank_reference_status"].startswith("approved"))
                self.assertTrue(route["bank_reference_text"])

    def test_generated_package_contract(self) -> None:
        analysis_path = EVIDENCE_ROOT / "analysis.json"
        manifest_path = EVIDENCE_ROOT / "review" / "manifest.json"
        answer_path = EVIDENCE_ROOT / "answer-key.json"
        if not all(path.is_file() for path in (analysis_path, manifest_path, answer_path)):
            self.skipTest("Generated final-bank benchmark package is not present.")
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        answers = json.loads(answer_path.read_text(encoding="utf-8"))
        self.assertEqual(analysis["sample_count"], 12)
        self.assertEqual(analysis["technical_pass_count"], 12)
        self.assertEqual(manifest["candidate_count"], 6)
        self.assertEqual(manifest["final_validated_bank_reference_count"], 31)
        self.assertEqual(manifest["export_filename"], self.module.EXPORT_FILENAME)
        self.assertFalse(manifest["candidate_mapping_exposed"])
        self.assertFalse(manifest["model_names_exposed"])
        self.assertFalse(manifest["production_promotion_allowed"])
        self.assertEqual(len(answers), 6)
        for row in answers:
            self.assertEqual(set(row["candidate_mapping"].values()), {"combined_bank", "legacy_reference"})
            self.assertTrue(row["performance_reference"]["transcript"])
            self.assertFalse(row["production_promotion_allowed"])

    def test_generated_audio_is_canonical(self) -> None:
        analysis_path = EVIDENCE_ROOT / "analysis.json"
        if not analysis_path.is_file():
            self.skipTest("Generated final-bank benchmark analysis is not present.")
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        for row in analysis["samples"]:
            with self.subTest(sample_id=row["sample_id"]):
                path = Path(row["audio_path"])
                self.assertTrue(path.is_file())
                self.assertEqual(self.module.base.sha256_file(path), row["audio_sha256"])
                info = self.module.base.sf.info(path)
                self.assertEqual(info.samplerate, 24000)
                self.assertEqual(info.channels, 1)
                self.assertEqual(info.subtype, "PCM_16")
                self.assertTrue(row["technical_pass"])
                self.assertFalse(row["production_promotion_allowed"])


if __name__ == "__main__":
    unittest.main()
