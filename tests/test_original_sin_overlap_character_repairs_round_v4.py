from __future__ import annotations

from pathlib import Path
import runpy
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks/build_original_sin_overlap_character_repairs_round_v4.py"


class CharacterRepairsRoundV4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = SCRIPT.read_text(encoding="utf-8")
        cls.namespace = runpy.run_path(str(SCRIPT), run_name="character_repairs_v4_test")

    def test_round_contains_exact_six_remaining_modes(self) -> None:
        self.assertEqual(len(self.namespace["MODE_SPECS"]), 6)
        self.assertEqual(
            {row["mode_id"] for row in self.namespace["MODE_SPECS"]},
            {
                "doctor_urgent_discovery_repair",
                "doctor_weary_moral_gravity_repair",
                "roz_dry_banter_repair",
                "computer_processing_repair",
                "dantalion_dry_sardonic",
                "dantalion_sharp_irritation",
            },
        )

    def test_computer_repair_uses_raw_passes_and_three_stronger_effects(self) -> None:
        computer = next(row for row in self.namespace["MODE_SPECS"] if row["mode_id"] == "computer_processing_repair")
        self.assertEqual(computer["postprocess_sources"], ["da6c367d964ea6c9", "56da202533b9f6d6"])
        self.assertEqual(len(computer["effect_variants"]), 3)

    def test_round_is_non_installing(self) -> None:
        for marker in ('"production_routing_changed": False', '"project_audio_changed": False', '"voice_config_changed": False'):
            self.assertIn(marker, self.text)


if __name__ == "__main__":
    unittest.main()
