from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from capability_truth import CapabilityTruthError, audit_capability_truth
from model_registry import registered_models


ROOT = Path(__file__).resolve().parents[1]
AUDIT_SOURCE_FILES = (
    "app/alexandria_preparer.py",
    "app/app.py",
    "app/mlx_backend.py",
    "app/static/pages/maintenance.js",
    "app/static/specialists/model_cache.js",
    "app/tts.py",
    "benchmarks/transcription_evaluator.py",
)


def copy_audit_sources(root: Path) -> None:
    for relative in AUDIT_SOURCE_FILES:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)


class CapabilityTruthTests(unittest.TestCase):
    def statuses(self) -> list[dict]:
        return [
            {
                "model": spec.as_dict(),
                "cached": spec.key in {
                    "mlx_clone",
                    "mlx_custom_voice",
                    "mlx_voice_design",
                    "mlx_controlled_clone",
                    "pytorch_qwen_base",
                },
                "state": "cached" if spec.key in {
                    "mlx_clone",
                    "mlx_custom_voice",
                    "mlx_voice_design",
                    "mlx_controlled_clone",
                    "pytorch_qwen_base",
                } else "missing",
            }
            for spec in registered_models()
        ]

    def capabilities(self) -> dict:
        return {
            "lora_training_supported": False,
            "training_action_enabled": False,
            "lora_inference_supported": False,
            "expressive_clone": {
                "supported": False,
                "model_cached": True,
                "per_line_instruction_supported": False,
                "acceptance": {"manual_audio_review_status": "pending"},
            },
            "experimental_lora_sidecar": {
                "merged_mlx_inference_technically_validated": False,
                "installed_artifact_count": 0,
            },
            "environment": {
                "pytorch_base_model_cached": True,
                "mlx_models_cached": {
                    "clone": True,
                    "custom_voice": True,
                    "voice_design": True,
                    "controlled_clone_voxcpm2": True,
                },
            },
        }

    def test_repository_capability_truth_passes(self) -> None:
        result = audit_capability_truth(
            repository_root=ROOT,
            capabilities=self.capabilities(),
            model_statuses=self.statuses(),
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["registry_count"], len(registered_models()))

    def test_commission_and_phantom_claims_fail(self) -> None:
        capabilities = self.capabilities()
        capabilities["environment"]["mlx_models_cached"]["clone"] = False
        statuses = self.statuses() + [
            {"model": {"key": "phantom_model"}, "cached": True, "state": "cached"}
        ]
        with self.assertRaises(CapabilityTruthError) as caught:
            audit_capability_truth(
                repository_root=ROOT,
                capabilities=capabilities,
                model_statuses=statuses,
            )
        kinds = {item["kind"] for item in caught.exception.issues}
        self.assertIn("commission", kinds)
        self.assertIn("phantom", kinds)

    def test_omission_and_unsupported_ready_fail(self) -> None:
        capabilities = self.capabilities()
        del capabilities["environment"]["mlx_models_cached"]["voice_design"]
        capabilities["training_action_enabled"] = True
        capabilities["expressive_clone"].update(
            {
                "supported": True,
                "model_cached": False,
                "per_line_instruction_supported": False,
            }
        )
        with self.assertRaises(CapabilityTruthError) as caught:
            audit_capability_truth(
                repository_root=ROOT,
                capabilities=capabilities,
                model_statuses=self.statuses(),
            )
        kinds = {item["kind"] for item in caught.exception.issues}
        self.assertIn("omission", kinds)
        self.assertIn("unsupported_ready", kinds)

    def test_orphan_runtime_binding_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_audit_sources(root)
            (root / "app/mlx_backend.py").write_text("", encoding="utf-8")
            with self.assertRaises(CapabilityTruthError) as caught:
                audit_capability_truth(
                    repository_root=root,
                    capabilities=self.capabilities(),
                    model_statuses=self.statuses(),
                )
        self.assertIn(
            "orphan",
            {item["kind"] for item in caught.exception.issues},
        )

    def test_direct_maintenance_hidden_internals_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_audit_sources(root)
            maintenance = root / "app/static/pages/maintenance.js"
            maintenance.write_text(
                maintenance.read_text(encoding="utf-8") + "\n// snapshot_path\n",
                encoding="utf-8",
            )
            with self.assertRaises(CapabilityTruthError) as caught:
                audit_capability_truth(
                    repository_root=root,
                    capabilities=self.capabilities(),
                    model_statuses=self.statuses(),
                )
        commissions = [
            item
            for item in caught.exception.issues
            if item["kind"] == "commission"
            and item["context"].get("surface") == "maintenance"
        ]
        self.assertEqual(len(commissions), 1)

    def test_direct_model_cache_api_binding_omission_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_audit_sources(root)
            model_cache = root / "app/static/specialists/model_cache.js"
            model_cache.write_text(
                model_cache.read_text(encoding="utf-8").replace(
                    "/api/model_registry/action",
                    "/api/model_registry/deleted-action",
                ),
                encoding="utf-8",
            )
            with self.assertRaises(CapabilityTruthError) as caught:
                audit_capability_truth(
                    repository_root=root,
                    capabilities=self.capabilities(),
                    model_statuses=self.statuses(),
                )
        omissions = [
            item
            for item in caught.exception.issues
            if item["kind"] == "omission"
            and item["context"].get("surface") == "model-cache"
            and item["context"].get("marker") == "/api/model_registry/action"
        ]
        self.assertEqual(len(omissions), 1)


if __name__ == "__main__":
    unittest.main()
