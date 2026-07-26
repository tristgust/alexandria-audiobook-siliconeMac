from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "apply_three_voice_paired_seed_reliability_review.py"
EVIDENCE = ROOT / ".omo" / "evidence" / "b17-t77-three-voice-paired-seed-review-applied"
APPLIED = EVIDENCE / "applied-paired-seed-review-ledger.json"
POLICY = EVIDENCE / "route-specific-prompt-policy.json"
NORMALIZED = EVIDENCE / "normalized-review-export.json"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "apply_three_voice_paired_seed_reliability_review", SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ThreeVoicePairedSeedReliabilityReviewApplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def test_normalized_review_preserves_uploaded_hash(self) -> None:
        if not NORMALIZED.is_file():
            self.skipTest("Normalized paired-seed review is not present.")
        payload = json.loads(NORMALIZED.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["source_upload_sha256"],
            "f35e83d74ff92ab85cb8bd7d1ed96f1ac181aae42aaa6030c4bef4abe0ecabd1",
        )
        self.assertEqual(payload["review"]["summary"]["complete_count"], 9)

    def test_applied_ledger_records_exact_route_conclusions(self) -> None:
        if not APPLIED.is_file():
            self.skipTest("Applied paired-seed ledger is not present.")
        payload = json.loads(APPLIED.read_text(encoding="utf-8"))
        result = self.module.validate_applied(payload)
        self.assertTrue(result["fixed_seed_runtime_reproducible"])
        self.assertEqual(result["hidden_repeat_role_agreement_count"], 2)
        self.assertEqual(
            result["recommendation_counts"],
            {
                "blocked_repeat_disagreement": 1,
                "legacy_reference_preferred_for_route_research": 1,
                "validated_bank_preferred_for_route_research": 1,
            },
        )
        groups = {row["route_group_id"]: row for row in payload["route_groups"]}
        self.assertEqual(
            groups["narrator_anger_control"]["recommendation"],
            "blocked_repeat_disagreement",
        )
        self.assertIsNone(groups["narrator_anger_control"]["preferred_role"])
        self.assertEqual(
            groups["benny_fatalistic_dread"]["preferred_role"],
            "legacy_reference",
        )
        self.assertEqual(
            groups["doctor_playful_identity"]["preferred_role"],
            "combined_bank",
        )

    def test_hidden_repeat_interpretation_uses_underlying_role_not_label(self) -> None:
        if not APPLIED.is_file():
            self.skipTest("Applied paired-seed ledger is not present.")
        payload = json.loads(APPLIED.read_text(encoding="utf-8"))
        groups = {row["route_group_id"]: row for row in payload["route_groups"]}
        self.assertEqual(
            groups["narrator_anger_control"]["hidden_repeat_selected_roles"],
            ["combined_bank", "legacy_reference"],
        )
        self.assertFalse(
            groups["narrator_anger_control"]["hidden_repeat_role_agreement"]
        )
        self.assertEqual(
            groups["benny_fatalistic_dread"]["hidden_repeat_selected_roles"],
            ["legacy_reference", "legacy_reference"],
        )
        self.assertEqual(
            groups["doctor_playful_identity"]["hidden_repeat_selected_roles"],
            ["combined_bank", "combined_bank"],
        )

    def test_route_policy_is_research_only_and_non_promoting(self) -> None:
        if not POLICY.is_file():
            self.skipTest("Route-specific prompt policy is not present.")
        payload = json.loads(POLICY.read_text(encoding="utf-8"))
        result = self.module.validate_policy(payload)
        self.assertEqual(result["research_preferred_count"], 2)
        self.assertEqual(result["blocked_count"], 1)
        self.assertEqual(payload["policy_scope"], "research_only")
        self.assertEqual(payload["general_reference_bank_routing"], "disabled")
        self.assertFalse(payload["automatic_production_assignment"])
        self.assertFalse(payload["production_promotion_allowed"])
        routes = {(row["target"], row["function"]): row for row in payload["routes"]}
        self.assertEqual(
            routes[("benny", "credible_fear")]["reference_key"],
            "benny-urgent_fear.wav",
        )
        self.assertEqual(
            routes[("doctor", "ordinary_identity")]["reference_key"],
            "doctor_acf_playful_introduction",
        )
        self.assertEqual(routes[("narrator", "explosive_anger")]["status"], "blocked")

    def test_general_routing_and_production_are_explicitly_blocked_in_source(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('"general_validated_bank_routing_recommended": False', source)
        self.assertIn('"general_legacy_routing_recommended": False', source)
        self.assertIn('"automatic_production_assignment": False', source)
        self.assertIn('"production_promotion_allowed": False', source)


if __name__ == "__main__":
    unittest.main()
