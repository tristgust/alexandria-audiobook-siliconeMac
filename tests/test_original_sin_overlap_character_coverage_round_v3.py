from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "benchmarks" / "build_original_sin_overlap_character_coverage_round_v3.py"


class CharacterCoverageRoundV3ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = BUILDER.read_text(encoding="utf-8")
        tree = ast.parse(cls.text)
        namespace: dict[str, object] = {}
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                try:
                    module = ast.Module(body=[node], type_ignores=[])
                    exec(compile(module, str(BUILDER), "exec"), namespace)
                except Exception:
                    continue
        cls.modes = namespace["MODE_SPECS"]
        cls.coverage = namespace["CHARACTER_COVERAGE_TARGETS"]

    def test_round_is_deficit_driven_and_has_sixteen_modes(self) -> None:
        self.assertEqual(len(self.modes), 16)
        ids = {mode["mode_id"] for mode in self.modes}
        self.assertEqual(len(ids), 16)
        self.assertIn("doctor_weary_moral_gravity", ids)
        self.assertIn("evan_broadcast_authority", ids)
        self.assertIn("securitybot_identity_repair", ids)
        self.assertIn("computer_formal_timestamp", ids)
        self.assertIn("tobias_robot_cold_control", ids)

    def test_recurring_character_thresholds_are_explicit(self) -> None:
        self.assertEqual(self.coverage["DOCTOR"]["required_modes"], 4)
        self.assertEqual(self.coverage["BERNICE"]["required_modes"], 4)
        self.assertEqual(self.coverage["ROZ FORRESTER"]["required_modes"], 4)
        self.assertEqual(self.coverage["CHRIS CWEJ"]["required_modes"], 3)

    def test_book_bot_label_is_split_between_two_identities(self) -> None:
        bot_modes = [mode for mode in self.modes if mode["book_speaker"] == "BOT"]
        self.assertEqual(
            {mode["character"] for mode in bot_modes},
            {"Securitybot", "Tobias Vaughn / Robot"},
        )
        tobias = next(
            mode for mode in bot_modes if mode["character"] == "Tobias Vaughn / Robot"
        )
        self.assertEqual(tobias["voice_key"], "TOBIAS VAUGHN")
        self.assertEqual(
            tobias["speaker_split"]["book_chunk_ids"],
            [1341, 3669, 3674, 3676, 3680, 3682, 3684],
        )

    def test_multimodel_and_non_installing_contracts_are_present(self) -> None:
        self.assertIn("qwen3_instruction_controlled", self.text)
        self.assertIn("voxcpm2_controllable_clone", self.text)
        self.assertIn("fish_s2_pro_free_zero_shot", self.text)
        self.assertIn("indextts2_matched_control", self.text)
        self.assertIn('"production_routing_changed": False', self.text)
        self.assertIn('"project_audio_changed": False', self.text)
        self.assertIn('"voice_config_changed": False', self.text)
        self.assertIn("entire_line_required", self.text)
        self.assertIn("written_notes_override_pass", self.text)

    def test_bounded_mode_smoke_filter_is_available(self) -> None:
        self.assertIn('"--mode"', self.text)
        self.assertIn("requested_modes", self.text)
        self.assertIn("Unknown mode IDs", self.text)

    def test_synthetic_repairs_use_new_bounded_effects(self) -> None:
        security = next(
            mode for mode in self.modes if mode["mode_id"] == "securitybot_identity_repair"
        )
        computer = next(
            mode for mode in self.modes if mode["mode_id"] == "computer_formal_timestamp"
        )
        self.assertEqual(security["effect_chain"], "securitybot_synthetic_v2")
        self.assertEqual(security["target_chunk_ids"], [495])
        self.assertEqual(computer["effect_chain"], "computer_modulation_v2")
        self.assertIn("quantization_levels", self.text)
        self.assertIn('"securitybot_identity_repair": False', self.text)


if __name__ == "__main__":
    unittest.main()
