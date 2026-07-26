from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "apply_three_voice_final_bank_benchmark_review.py"
EVIDENCE_ROOT = (
    ROOT
    / ".omo"
    / "evidence"
    / "b17-t75-three-voice-final-bank-benchmark-review-applied"
)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ThreeVoiceFinalBankBenchmarkReviewApplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module(SCRIPT, "apply_three_voice_final_bank_benchmark_review")

    def test_source_blocks_bank_routing_and_production_promotion(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('"reference_bank_prompt_routing_recommended": False', source)
        self.assertIn('"broad_bank_improvement_claim_supported": False', source)
        self.assertIn('"automatic_production_assignment": False', source)
        self.assertIn('"production_promotion_allowed": False', source)
        self.assertIn('"generation_reliability_diagnostic"', source)
        self.assertIn('"reference_bank_mining_should_continue": False', source)

    def test_exact_repeat_control_detects_single_seed_instability(self) -> None:
        current = {
            "round_id": self.module.CURRENT_MATRIX_ROUND_ID,
            "routes": [{
                "route_id": "narrator_anger_control",
                "target_text": "line",
                "alpha": 0.75,
                "identity_audio_sha256": "identity",
                "bank_reference_audio_sha256": "bank",
                "legacy_reference_audio_sha256": "legacy",
            }],
            "samples": [
                {"route_id": "narrator_anger_control", "prompt_role": "combined_bank", "sample_id": "new-bank"},
                {"route_id": "narrator_anger_control", "prompt_role": "legacy_reference", "sample_id": "new-legacy"},
            ],
        }
        prior = {
            "round_id": self.module.PRIOR_MATRIX_ROUND_ID,
            "routes": [{
                "route_id": "narrator_anger",
                "target_text": "line",
                "alpha": 0.75,
                "identity_audio_sha256": "identity",
                "bank_reference_audio_sha256": "bank",
                "legacy_reference_audio_sha256": "legacy",
            }],
            "samples": [
                {"route_id": "narrator_anger", "prompt_role": "combined_bank", "sample_id": "old-bank"},
                {"route_id": "narrator_anger", "prompt_role": "legacy_reference", "sample_id": "old-legacy"},
            ],
        }
        prior_applied = {
            "round_id": self.module.PRIOR_APPLIED_ROUND_ID,
            "outcomes": [{
                "route_id": "narrator_anger",
                "selected_role": "combined_bank",
                "quality_status": "clean_preference",
            }],
        }
        current_outcomes = {
            "narrator_anger_control": {
                "selected_role": "neither",
                "quality_status": "unusable_both",
            }
        }
        result = self.module.exact_repeat_control(current, prior, prior_applied, current_outcomes)
        self.assertTrue(result["exact_configuration_match"])
        self.assertTrue(result["seed_changed_because_round_id_changed"])
        self.assertTrue(result["outcome_changed"])
        self.assertFalse(result["single_seed_decision_reliable"])

    def test_generated_applied_ledger_when_present(self) -> None:
        path = EVIDENCE_ROOT / "applied-benchmark-review-ledger.json"
        if not path.is_file():
            self.skipTest("Generated applied benchmark evidence is not present in this checkout.")
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = self.module.validate_applied(payload)
        self.assertEqual(
            result["selected_role_counts"],
            {"combined_bank": 1, "legacy_reference": 4, "neither": 1},
        )
        self.assertEqual(result["clean_combined_bank_win_count"], 0)
        self.assertEqual(result["clean_legacy_reference_win_count"], 3)
        self.assertFalse(result["single_seed_benchmark_reliable"])
        self.assertFalse(result["reference_bank_prompt_routing_recommended"])
        self.assertEqual(payload["quality_blocked_combined_bank_preference_count"], 1)
        self.assertEqual(payload["quality_blocked_legacy_reference_preference_count"], 1)
        self.assertFalse(payload["broad_bank_improvement_claim_supported"])
        self.assertTrue(payload["validated_bank_research_library_retained"])

    def test_normalized_review_preserves_uploaded_hash(self) -> None:
        path = EVIDENCE_ROOT / "normalized-review-export.json"
        if not path.is_file():
            self.skipTest("Normalized uploaded review is not present in this checkout.")
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["source_upload_sha256"],
            "382e03142f9decd76592ac50e2025aeeff8fca6a4c4f2c98e03116114ab8af14",
        )
        self.assertEqual(payload["review"]["summary"]["complete_count"], 6)


if __name__ == "__main__":
    unittest.main()
