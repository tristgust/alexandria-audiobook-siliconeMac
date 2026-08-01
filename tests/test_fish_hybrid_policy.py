from __future__ import annotations

import unittest

from fish_hybrid_policy import (
    apply_fish_hybrid_policy,
    eligible_for_fish_hybrid,
    fish_hybrid_decision,
)


class FishHybridPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.voice = {
            "type": "clone",
            "clone_backend": "qwen3_instruction_controlled",
            "ref_audio": "voice.wav",
            "ref_text": "Exact reference words.",
            "fish_hybrid_enabled": True,
            "fish_hybrid_styles": ["fear", "grief", "sarcasm"],
            "fish_hybrid_use_approved_routes": True,
            "fish_hybrid_fallback_to_local": True,
        }

    def test_selected_style_routes_to_fish(self) -> None:
        decision = fish_hybrid_decision(
            voice_data=self.voice,
            text="There was no goodbye.",
            instruction="Deep grief, close to breaking.",
            approved_prompt_selected=False,
        )
        self.assertTrue(decision.use_fish)
        self.assertEqual(decision.route.style, "grief")
        self.assertEqual(decision.reason, "style:grief")

    def test_neutral_line_stays_local(self) -> None:
        decision = fish_hybrid_decision(
            voice_data=self.voice,
            text="The door opened.",
            instruction="Neutral, natural clear delivery.",
            approved_prompt_selected=False,
        )
        self.assertFalse(decision.use_fish)
        self.assertEqual(decision.route.style, "neutral")

    def test_approved_prompt_route_can_use_fish(self) -> None:
        decision = fish_hybrid_decision(
            voice_data=self.voice,
            text="A line with a validated delivery reference.",
            instruction="Measured and deliberate.",
            approved_prompt_selected=True,
        )
        self.assertTrue(decision.use_fish)
        self.assertEqual(decision.reason, "approved_prompt_route")

    def test_migration_policy_keeps_local_backend(self) -> None:
        migrated = apply_fish_hybrid_policy(self.voice, enabled=True)
        self.assertEqual(
            migrated["clone_backend"],
            "qwen3_instruction_controlled",
        )
        self.assertTrue(migrated["fish_hybrid_enabled"])
        self.assertIn("expressive", migrated["fish_hybrid_styles"])
        disabled = apply_fish_hybrid_policy(migrated, enabled=False)
        self.assertNotIn("fish_hybrid_enabled", disabled)
        self.assertEqual(
            disabled["clone_backend"],
            "qwen3_instruction_controlled",
        )

    def test_eligibility_requires_reference_and_transcript(self) -> None:
        self.assertTrue(eligible_for_fish_hybrid(self.voice))
        self.assertFalse(eligible_for_fish_hybrid({"type": "clone"}))


if __name__ == "__main__":
    unittest.main()
