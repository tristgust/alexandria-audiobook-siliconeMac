from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "benchmarks/chris_roz_multimodel_round1.json"


class ChrisRozMultimodelRoundContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = json.loads(CONFIG.read_text(encoding="utf-8"))

    def test_round_is_exactly_two_characters_two_tiers_three_models_four_styles_two_repeats(self) -> None:
        payload = self.payload
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual([row["key"] for row in payload["identities"]], ["chris", "roz"])
        self.assertEqual(
            [row["key"] for row in payload["reference_tiers"]],
            ["clean_actor", "canonical_cleaned"],
        )
        self.assertEqual(
            [row["key"] for row in payload["models"]],
            [
                "fish_s2_pro_cloud",
                "voxcpm2_controllable_clone",
                "indextts2_matched_control",
            ],
        )
        self.assertEqual(payload["generation"]["repeats"], 2)
        self.assertEqual({len(row["styles"]) for row in payload["identities"]}, {4})
        expected = 2 * 2 * 3 * 4 * 2
        self.assertEqual(expected, 96)

    def test_tnia_is_absent_from_every_active_round_value(self) -> None:
        payload = self.payload
        self.assertFalse(payload["tnia_miller_included"])
        active = json.dumps(
            {
                "models": payload["models"],
                "reference_tiers": payload["reference_tiers"],
                "identities": payload["identities"],
                "generation": payload["generation"],
            }
        ).casefold()
        self.assertNotIn("tnia", active)

    def test_fish_uses_the_proven_free_header_and_character_specific_routing(self) -> None:
        fish = next(row for row in self.payload["models"] if row["key"] == "fish_s2_pro_cloud")
        self.assertEqual(fish["api_model_header"], "s2.1-pro-free")
        modes = {
            style["fish_prompt_mode"]
            for identity in self.payload["identities"]
            for style in identity["styles"]
        }
        self.assertEqual(modes, {"simple_tag", "rich_tag", "full_alexandria_tag"})

    def test_index_emotion_references_are_same_character_and_no_cross_identity_blend_exists(self) -> None:
        for identity in self.payload["identities"]:
            prefix = f"{identity['key']}_"
            for style in identity["styles"]:
                reference = style["emotion_reference_id"]
                self.assertTrue(reference.startswith(prefix), (identity["key"], reference))
                self.assertGreaterEqual(float(style["index_alpha"]), 0.0)
                self.assertLessEqual(float(style["index_alpha"]), 1.0)

    def test_every_style_has_exact_text_and_model_specific_control_material(self) -> None:
        for identity in self.payload["identities"]:
            keys = []
            for style in identity["styles"]:
                keys.append(style["key"])
                self.assertTrue(style["target_text"].strip())
                self.assertTrue(style["instruction"].strip())
                self.assertTrue(style["fish_tag"].strip())
                self.assertTrue(style["emotion_reference_id"].strip())
            self.assertEqual(keys, ["neutral", "dry_humour", "urgent_authority", "vulnerability"])


if __name__ == "__main__":
    unittest.main()
