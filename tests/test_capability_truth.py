from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from capability_truth import CapabilityTruthError, audit_capability_truth
from model_registry import registered_models


ROOT = Path(__file__).resolve().parents[1]


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
            (root / "app/static").mkdir(parents=True)
            (root / "app/static/canonical_interface.js").write_text(
                "required_by_default missing_required_paths data-maintenance-model-action",
                encoding="utf-8",
            )
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


if __name__ == "__main__":
    unittest.main()
