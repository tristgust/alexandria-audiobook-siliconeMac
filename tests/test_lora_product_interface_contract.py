from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VOICE_LAB = (
    ROOT / "app" / "static" / "specialists" / "voice_training.js"
).read_text(encoding="utf-8")
APP = (ROOT / "app" / "app.py").read_text(encoding="utf-8")


class LoraProductInterfaceContractTests(unittest.TestCase):
    def test_voice_lab_keeps_experimental_training_truthful(self) -> None:
        for phrase in (
            "title: 'Voice Lab'",
            "capabilityResult.data?.training_action_enabled",
            "Training is available for feasibility review.",
            "Training, validation, and installation do not change the production Voice.",
            "Production Voice assignment happens only in Cast.",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, VOICE_LAB)
        self.assertNotIn("production speaker", VOICE_LAB)
        self.assertNotIn("'/api/lora/train'", VOICE_LAB)

    def test_lora_train_route_no_longer_invokes_legacy_shared_trainer(self) -> None:
        start = APP.index('@app.post("/api/lora/train")')
        end = APP.index('@app.get("/api/lora/models")', start)
        route = APP[start:end]
        self.assertNotIn("train_lora.py", route)
        self.assertNotIn("run_process(command", route)
        self.assertIn("_require_isolated_lora_training", route)
        self.assertIn("_run_lora_product_pipeline", route)
        self.assertIn('instruction_mode: Literal["identity_only", "per_record"]', APP)

    def test_pipeline_contains_all_fail_closed_stages(self) -> None:
        start = APP.index("def _run_lora_product_pipeline(")
        end = APP.index('@app.post("/api/lora/train")', start)
        pipeline = APP[start:end]
        for value in (
            'action="train_lora"',
            'action="merge_lora"',
            'action="export_mlx"',
            "install_training_sidecar_mlx_artifact_payload",
            '"cleanup_merged": True',
            '"production_assignment_supported": False',
            '"instruction_mode": request_payload["instruction_mode"]',
            'state["stage"] = "failed"',
        ):
            with self.subTest(value=value):
                self.assertIn(value, pipeline)


if __name__ == "__main__":
    unittest.main()
