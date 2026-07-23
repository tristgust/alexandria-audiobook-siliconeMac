from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRAINING_PATH = ROOT / "app/static/specialists/voice_training.js"


class VoiceTrainingInterfaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = TRAINING_PATH.read_text(encoding="utf-8")

    def test_voice_lab_is_a_direct_contextual_module(self) -> None:
        for phrase in (
            "export async function mount",
            "dataRouteOwner",
            "voice-training-workspace",
            "route.context.character",
            "data-support-return",
            "return cleanup",
        ):
            self.assertIn(phrase, self.source)

    def test_real_training_and_reference_bank_state_are_loaded(self) -> None:
        for endpoint in (
            "/api/voice_training/status",
            "/api/voice_training/",
            "/api/expressive_reference_banks/status",
            "/api/expressive_reference_banks/",
            "/api/voice_backend/capabilities",
        ):
            self.assertIn(endpoint, self.source)
        for state in ("Loading", "No Voice Lab project", "Could not load", "Experimental"):
            self.assertIn(state, self.source)

    def test_training_never_duplicates_production_assignment(self) -> None:
        for phrase in (
            "Production Voice assignment happens only in Cast",
            "Training, validation, and installation do not change the production Voice",
            "Owned reference recording",
        ):
            self.assertIn(phrase, self.source)
        for forbidden in (
            "/assign",
            "/api/save_voice_config",
            "Assign production voice",
            "Remove production assignment",
            "innerHTML",
            "legacy",
        ):
            self.assertNotIn(forbidden, self.source)

    def test_javascript_is_valid(self) -> None:
        subprocess.run(
            ["node", "--check", str(TRAINING_PATH)],
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
