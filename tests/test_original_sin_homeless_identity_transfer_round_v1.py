from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks/build_original_sin_homeless_identity_transfer_round_v1.py"


class HomelessIdentityTransferRoundV1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = SCRIPT.read_text(encoding="utf-8")

    def test_round_compares_adaptation_and_designed_identity_sources(self) -> None:
        self.assertIn('(("adaptation", adaptation), ("designed", designed))', self.text)
        self.assertIn('SOURCE_CANDIDATE_ID = "3932d1942197febd"', self.text)

    def test_round_uses_four_models_per_identity_source(self) -> None:
        self.assertIn("for backend_name in (VOX, FISH, INDEX)", self.text)
        self.assertIn("specs.append((QWEN", self.text)

    def test_round_is_non_installing(self) -> None:
        for marker in ('"production_routing_changed": False', '"project_audio_changed": False', '"voice_config_changed": False'):
            self.assertIn(marker, self.text)


if __name__ == "__main__":
    unittest.main()
