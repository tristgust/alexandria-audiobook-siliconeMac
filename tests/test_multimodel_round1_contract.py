from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))

from build_multimodel_round1_manifest import control_for, support_for
from merge_multimodel_round1_review_results import (
    is_complete,
    normalized_row,
    preliminary_disposition,
)
from multimodel_blind_round1_contract import ROUND_ID, STYLE_GROUPS, STYLES
from package_multimodel_round1_review import public_identity_key


class MultimodelRound1ContractTests(unittest.TestCase):
    def test_taxonomy_has_five_groups_and_thirty_eight_unique_styles(self) -> None:
        self.assertEqual(ROUND_ID, "alexandria_multimodel_expressive_clone_round1_v1")
        self.assertEqual(len(STYLE_GROUPS), 5)
        self.assertEqual(len(STYLES), 38)
        keys = [item["key"] for item in STYLES]
        self.assertEqual(len(set(keys)), 38)
        self.assertEqual(
            {key: len(value["styles"]) for key, value in STYLE_GROUPS.items()},
            {
                "baseline_positive": 8,
                "sorrow_vulnerability": 10,
                "threat_conflict": 11,
                "subtext_cognition": 5,
                "vocal_modes_events": 4,
            },
        )
        for style in STYLES:
            self.assertIn(style["key"], STYLE_GROUPS[style["group"]]["styles"])
            self.assertTrue(style["target_text"].strip())
            self.assertTrue(style["instruction"].strip())

    def test_required_model_screen_is_exact(self) -> None:
        payload = json.loads(
            (BENCHMARKS / "multimodel_round1_models.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            payload["required_model_keys"],
            [
                "indextts2",
                "voxcpm2",
                "qwen3_tts",
                "fish_s2_pro",
                "higgs_audio_v25",
                "moss_tts_local_v15",
                "chatterbox_multilingual_v3",
            ],
        )
        self.assertEqual(
            {item["key"] for item in payload["models"]},
            set(payload["required_model_keys"]),
        )

    def test_qwen_clone_emotions_fail_closed_but_native_and_acted_lanes_run(self) -> None:
        model = {"key": "qwen3_tts"}
        self.assertEqual(support_for(model, "narrator", "neutral"), (True, None))
        supported, reason = support_for(model, "narrator", "fear")
        self.assertFalse(supported)
        self.assertIn("does not accept style instructions", reason)
        self.assertEqual(support_for(model, "ryan_acted", "fear"), (True, None))
        self.assertEqual(support_for(model, "native_qwen_aiden", "fear"), (True, None))

    def test_higgs_v25_does_not_silently_use_v2(self) -> None:
        supported, reason = support_for(
            {"key": "higgs_audio_v25"},
            "narrator",
            "neutral",
        )
        self.assertFalse(supported)
        self.assertIn("No distinct public Higgs Audio V2.5 checkpoint", reason)
        self.assertIn("do not substitute Higgs TTS 2 invisibly", reason)

    def test_moss_short_line_profile_is_bounded_and_instruction_aware(self) -> None:
        style = next(item for item in STYLES if item["key"] == "hopeful")
        control = control_for(
            {"key": "moss_tts_local_v15"},
            "narrator",
            style,
            {},
        )
        self.assertEqual(control["max_tokens"], 768)
        self.assertEqual(control["n_vq_for_inference"], 12)
        self.assertEqual(control["instruction"], style["instruction"])
        self.assertTrue(control["semantic_instruction_directly_consumed"])

    def test_chatterbox_is_labeled_as_numeric_proxy_not_semantic_instruction(self) -> None:
        style = next(item for item in STYLES if item["key"] == "grief")
        control = control_for(
            {"key": "chatterbox_multilingual_v3"},
            "narrator",
            style,
            {},
        )
        self.assertEqual(control["mechanism"], "numeric_exaggeration_cfg_proxy")
        self.assertFalse(control["semantic_instruction_directly_consumed"])
        self.assertIn("exaggeration", control)
        self.assertIn("cfg_weight", control)

    def test_public_native_keys_contain_speaker_name_but_not_model_name(self) -> None:
        cases = {
            ("native_qwen_aiden", "Aiden"): "native_aiden",
            ("native_voxcpm2_rowan", "Rowan"): "native_rowan",
            ("native_fish_marlow", "Marlow"): "native_marlow",
            ("native_moss_alder", "Alder"): "native_alder",
            ("native_chatterbox_linden", "Linden"): "native_linden",
            ("native_higgs_belinda", "Belinda"): "native_belinda",
        }
        for (internal_key, review_name), expected in cases.items():
            with self.subTest(internal_key=internal_key):
                public_key = public_identity_key(internal_key, review_name)
                self.assertEqual(public_key, expected)
                self.assertNotIn(internal_key.split("_")[1], public_key)
        self.assertEqual(public_identity_key("narrator", "Narrator"), "narrator")
        self.assertEqual(public_identity_key("ryan_acted", "Ryan"), "ryan_acted")

    def test_review_contract_has_simple_follow_up_flag_not_disposition_menu(self) -> None:
        package_source = (BENCHMARKS / "package_multimodel_round1_review.py").read_text(
            encoding="utf-8"
        )
        app_source = (
            BENCHMARKS / "multimodel_review_assets" / "app.js"
        ).read_text(encoding="utf-8")
        self.assertIn('"flag_for_follow_up"', package_source)
        self.assertNotIn('"round2_disposition"', package_source)
        self.assertNotIn("Round 2 disposition", app_source)
        self.assertIn("Flag for follow-up", app_source)

    def test_pairwise_round_two_is_separate_and_three_choice(self) -> None:
        design = (ROOT / "docs" / "MULTIMODEL_BLIND_REVIEW_DESIGN.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("A is better", design)
        self.assertIn("No meaningful preference", design)
        self.assertIn("B is better", design)
        self.assertIn("Randomize which candidate is shown as A or B", design)
        self.assertIn("distinct review route or application mode", design)

    def test_round_two_preparation_is_derived_from_scores(self) -> None:
        strong = normalized_row(
            {
                "sample_id": "blind-a",
                "identity_1_to_5": 4,
                "delivery_1_to_5": 5,
                "naturalness_1_to_5": 4,
                "artifact_severity_1_to_5": 1,
                "spoken_text_matches_expected": True,
                "requested_mode_is_clear": True,
                "approve_for_comparison": True,
                "flag_for_follow_up": False,
                "notes": "",
            }
        )
        self.assertTrue(is_complete(strong))
        self.assertEqual(
            preliminary_disposition(strong),
            "strong_round2_candidate",
        )
        flagged = {**strong, "flag_for_follow_up": True}
        self.assertEqual(
            preliminary_disposition(flagged),
            "targeted_follow_up",
        )
        rejected = {**strong, "spoken_text_matches_expected": False}
        self.assertEqual(preliminary_disposition(rejected), "reject")
        pending = normalized_row({"sample_id": "blind-b", "notes": "partial"})
        self.assertFalse(is_complete(pending))
        self.assertEqual(preliminary_disposition(pending), "pending")

    def test_removed_manual_disposition_is_ignored_on_old_imports(self) -> None:
        row = normalized_row(
            {
                "sample_id": "blind-old",
                "round2_disposition": "promote",
                "notes": "legacy export",
            }
        )
        self.assertNotIn("round2_disposition", row)
        self.assertEqual(row["notes"], "legacy export")


if __name__ == "__main__":
    unittest.main()
