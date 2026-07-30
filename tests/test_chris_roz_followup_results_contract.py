from __future__ import annotations

import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "benchmarks/chris_roz_evaluation_routing_profile.json"


class ChrisRozFollowupResultsContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = json.loads(PROFILE.read_text(encoding="utf-8"))

    def test_identity_policy_keeps_tnia_absent_and_clean_actor_primary(self) -> None:
        identity = self.profile["identity_conditioning"]
        self.assertEqual(identity["default_tier"], "clean_actor")
        self.assertFalse(identity["tnia_miller_included"])
        self.assertEqual(identity["chris"]["canonical_repair_candidate"], "mossformer2_demucs")
        self.assertEqual(
            identity["chris"]["canonical_repair_round"],
            "alexandria_chris_reference_repair_pairwise_v2",
        )

    def test_pairwise_winners_are_encoded(self) -> None:
        routes = self.profile["routes"]
        self.assertEqual(routes["chris"]["dry_humour"]["model_key"], "indextts2_matched_control")
        self.assertEqual(routes["roz"]["neutral"]["model_key"], "fish_s2_pro_cloud")

    def test_urgency_uses_protective_index_reference(self) -> None:
        route = self.profile["routes"]["chris"]["urgent_authority"]
        self.assertEqual(route["model_key"], "indextts2_matched_control")
        self.assertEqual(route["delivery_reference"], "chris_dread_protective")
        self.assertEqual(route["control"]["emotion_strength"], 1.0)
        self.assertEqual(route["alternate"]["emotion_strength"], 0.85)

    def test_every_route_uses_only_supported_models(self) -> None:
        allowed = {
            "fish_s2_pro_cloud",
            "voxcpm2_controllable_clone",
            "indextts2_matched_control",
        }
        for character in self.profile["routes"].values():
            for route in character.values():
                self.assertIn(route["model_key"], allowed)

    def test_profile_remains_evaluation_only(self) -> None:
        self.assertTrue(self.profile["manual_listening_required"])
        self.assertFalse(self.profile["production_promotion_allowed"])


if __name__ == "__main__":
    unittest.main()
