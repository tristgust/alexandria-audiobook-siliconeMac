from __future__ import annotations

from pathlib import Path
import runpy
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks/build_original_sin_overlap_final_character_round_v5.py"


class FinalCharacterRoundV5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.namespace = runpy.run_path(str(SCRIPT), run_name="final_character_v5_test")
        cls.text = SCRIPT.read_text(encoding="utf-8")

    def test_round_contains_exact_three_remaining_characters(self) -> None:
        self.assertEqual({row["character"] for row in self.namespace["MODE_SPECS"]}, {"The Doctor", "Shythe Shahid", "Doc Dantalion"})

    def test_targets_are_new_unseen_lines(self) -> None:
        ids = {row["mode_id"]: row["target_chunk_ids"] for row in self.namespace["MODE_SPECS"]}
        self.assertEqual(ids["doctor_sudden_realization_final"], [362])
        self.assertEqual(ids["shythe_crisis_broadcast"], [4629])
        self.assertEqual(ids["dantalion_weary_memory"], [2658])

    def test_round_is_non_installing(self) -> None:
        for marker in ('"production_routing_changed": False', '"project_audio_changed": False', '"voice_config_changed": False'):
            self.assertIn(marker, self.text)


if __name__ == "__main__":
    unittest.main()
