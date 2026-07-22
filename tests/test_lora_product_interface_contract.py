from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "app" / "app.py").read_text(encoding="utf-8")


class LoraProductInterfaceContractTests(unittest.TestCase):
    def test_visible_training_controls_use_isolated_sidecar_capability(self) -> None:
        self.assertIn(
            "capabilities?.experimental_lora_sidecar?.training_supported === true",
            HTML,
        )
        self.assertIn("Isolated MPS ready", HTML)
        self.assertIn("Train, validate, and install", HTML)
        self.assertIn("lora-target-profile", HTML)
        self.assertIn("lora-validation-fraction", HTML)
        self.assertIn("Attention only — recommended", HTML)
        self.assertIn("production speaker", HTML)
        self.assertNotIn(
            "Use expressive voice projects while the isolated trainer is evaluated.",
            HTML,
        )

    def test_browser_submits_real_pipeline_settings_and_tracks_stages(self) -> None:
        start = HTML.index("window.startLoraTraining = async () =>")
        end = HTML.index("function pollLoraTraining", start)
        function = HTML[start:end]
        for value in (
            "'/api/lora/train'",
            "lora_target_profile",
            "validation_fraction",
            "instruction_mode",
            "local_files_only: true",
            "batch_size: 1",
        ):
            with self.subTest(value=value):
                self.assertIn(value, function)
        poll_end = HTML.index("function renderAdapterArtifactActions", end)
        poll = HTML[end:poll_end]
        self.assertIn("status.stage", poll)
        self.assertIn("status.result?.adapter_id", poll)
        self.assertIn("review required", poll)
        self.assertIn("loadVoiceBackendCapabilities();", poll)

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
