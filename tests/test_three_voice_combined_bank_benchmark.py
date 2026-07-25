from __future__ import annotations

import importlib.util
import json
import unittest
from types import SimpleNamespace
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "prepare_three_voice_combined_bank_benchmark.py"
ASSET_ROOT = ROOT / "benchmarks" / "three_voice_combined_bank_benchmark_assets"
EVIDENCE_ROOT = ROOT / ".omo" / "evidence" / "b17-t63-three-voice-combined-bank-benchmark"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ThreeVoiceCombinedBankBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module(SCRIPT, "prepare_three_voice_combined_bank_benchmark")

    def test_matrix_is_small_balanced_and_excludes_open_gaps(self) -> None:
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

    def test_routes_cover_the_intended_bank_functions(self) -> None:
        mapping = {row["route_id"]: row["bank_clip_id"] for row in self.module.ROUTES}
        self.assertEqual(
            mapping,
            {
                "narrator_joy": "narrator_ud_ecstatic_bucket_affection",
                "narrator_anger": "narrator_ud_explosive_indignation",
                "benny_fear": "benny_hesitation_fearful_vigilance",
                "benny_reassurance": "benny_hesitation_protective_reassurance",
                "doctor_playful_identity": "doctor_acf_playful_introduction",
                "doctor_urgency": "doctor_acf_emergency_command",
            },
        )

    def test_superseded_matrix_requires_explicit_audit_override(self) -> None:
        with self.assertRaisesRegex(
            self.module.CombinedBankBenchmarkError,
            "historical transcript-guided candidates without explicit human approval",
        ):
            self.module.prepare(SimpleNamespace(allow_superseded=False))

    def test_review_assets_are_blinded_and_bounded(self) -> None:
        html = (ASSET_ROOT / "index.html").read_text(encoding="utf-8")
        app = (ASSET_ROOT / "app.js").read_text(encoding="utf-8")
        self.assertEqual(html.lower().count("<audio"), 4)
        self.assertIn("Candidate A", html)
        self.assertIn("Candidate B", html)
        self.assertIn("Neither", html)
        self.assertIn("Identity drift", html)
        self.assertIn("Weak delivery", html)
        self.assertIn("Wrong pacing", html)
        self.assertIn("Artifacts", html)
        self.assertIn("alexandria_three_voice_combined_bank_benchmark_review.json", app)
        self.assertNotIn("legacy_reference", html)
        self.assertNotIn("prompt_role", html)

    def test_generated_evidence_contract_when_present(self) -> None:
        matrix_path = EVIDENCE_ROOT / "matrix.json"
        analysis_path = EVIDENCE_ROOT / "analysis.json"
        manifest_path = EVIDENCE_ROOT / "review" / "manifest.json"
        answer_path = EVIDENCE_ROOT / "answer-key.json"
        if not all(path.is_file() for path in (matrix_path, analysis_path, manifest_path, answer_path)):
            self.skipTest("Generated benchmark evidence is not present in this checkout.")
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        answers = json.loads(answer_path.read_text(encoding="utf-8"))
        self.assertEqual(matrix["route_count"], 6)
        self.assertEqual(matrix["sample_count"], 12)
        self.assertTrue(matrix["comparison_contract"]["only_performance_prompt_changes"])
        self.assertTrue(matrix["comparison_contract"]["open_gap_functions_excluded"])
        self.assertEqual(analysis["sample_count"], 12)
        self.assertEqual(analysis["technical_pass_count"], 12)
        self.assertEqual(manifest["candidate_count"], 6)
        self.assertEqual(manifest["maximum_simultaneous_audio_elements"], 4)
        self.assertFalse(manifest["candidate_mapping_exposed"])
        self.assertFalse(manifest["model_names_exposed"])
        self.assertFalse(manifest["production_promotion_allowed"])
        self.assertEqual(len(answers), 6)
        for row in answers:
            self.assertEqual(set(row["candidate_mapping"].values()), {"combined_bank", "legacy_reference"})
            self.assertFalse(row["production_promotion_allowed"])

    def test_generated_audio_is_canonical_when_present(self) -> None:
        analysis_path = EVIDENCE_ROOT / "analysis.json"
        if not analysis_path.is_file():
            self.skipTest("Generated benchmark analysis is not present in this checkout.")
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        for row in analysis["samples"]:
            with self.subTest(sample_id=row["sample_id"]):
                path = Path(row["audio_path"])
                self.assertTrue(path.is_file())
                self.assertEqual(self.module.sha256_file(path), row["audio_sha256"])
                info = self.module.sf.info(path)
                self.assertEqual(info.samplerate, 24000)
                self.assertEqual(info.channels, 1)
                self.assertEqual(info.subtype, "PCM_16")
                self.assertTrue(row["technical_pass"])
                self.assertFalse(row["production_promotion_allowed"])


if __name__ == "__main__":
    unittest.main()
