from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from instruction_propagation import build_instruction_propagation_contract
from voice_backend_capabilities import (
    VoiceBackendCapabilityError,
    build_voice_backend_capabilities,
    latest_controlled_clone_evidence_path,
    latest_lora_sidecar_evidence_path,
    latest_phase22_evidence_path,
    load_controlled_clone_evidence,
    installed_mlx_lora_artifacts,
    load_lora_sidecar_evidence,
    load_phase22_evidence,
    require_lora_training_supported,
)


class VoiceBackendCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.results = self.root / "benchmarks" / "results"
        self.results.mkdir(parents=True)
        self.evidence_path = (
            self.results / "20260717T014952Z_phase22_apple_silicon.json"
        )
        self.evidence = {
            "schema_version": 1,
            "stable_lora_outcome": "unsupported",
            "tts_measurements": {
                "voice_design": {"warm_rtf": 0.3},
                "custom_voice": {"warm_rtf": 0.31},
            },
        }
        self.evidence_path.write_text(
            json.dumps(self.evidence),
            encoding="utf-8",
        )
        self.controlled_path = (
            self.results / "20260721T120000Z_qwen3_icl_instruction_clone.json"
        )
        self.controlled_evidence = {
            "schema_version": 2,
            "backend": "qwen3_instruction_controlled",
            "model": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
            "measurements": {
                "neutral": {
                    "real_time_factor": 0.84,
                    "speaker_cosine_to_reference": 0.976,
                },
                "expressive": {
                    "real_time_factor": 0.78,
                    "speaker_cosine_to_reference": 0.96,
                },
            },
            "acceptance": {
                "delivery_directionality_passed": True,
                "speaker_identity_passed": True,
                "manual_audio_review_required": True,
                "manual_audio_review_status": "approved",
            },
        }
        self.controlled_path.write_text(
            json.dumps(self.controlled_evidence),
            encoding="utf-8",
        )
        self.sidecar_path = (
            self.results / "20260717T040339Z_mps_lora_merged_mlx.json"
        )
        self.sidecar_evidence = {
            "schema_version": 1,
            "architecture": (
                "mps_lora_training_merged_mlx_inference_experimental"
            ),
            "model_probe": {"device": "mps"},
            "training": {
                "trainable_parameters": 9617408,
                "trainable_percent": 0.499,
                "step_metrics": [
                    {
                        "step_seconds": 1.5,
                        "mps_current_allocated_gib": 7.31,
                    }
                ],
            },
            "pytorch_adapter_inference": {
                "real_time_factor": 7.18,
            },
            "mlx_export": {
                "validation": {
                    "measurements": {
                        "neutral": {
                            "real_time_factor": 0.56,
                            "speaker_cosine_to_reference": 0.976,
                        },
                        "expressive": {
                            "real_time_factor": 0.47,
                            "speaker_cosine_to_reference": 0.973,
                        },
                    },
                    "instruction_channel_changed_output": True,
                }
            },
            "quality_review": {
                "manual_audio_review_required": True,
                "manual_audio_review_status": "pending",
                "multi_sample_multi_epoch_validation_required": True,
            },
            "shared_runtime_lora_supported": False,
            "experimental_sidecar_training_supported": True,
            "direct_pytorch_inference_performant": False,
            "merged_mlx_inference_technically_validated": True,
            "production_assignment_supported": False,
        }
        self.sidecar_path.write_text(
            json.dumps(self.sidecar_evidence),
            encoding="utf-8",
        )

    def install_mlx_artifact_fixture(self) -> Path:
        model = self.root / "lora_models" / "narrator_pilot" / "mlx_model"
        (model / "speech_tokenizer").mkdir(parents=True)
        for relative in (
            "model.safetensors",
            "config.json",
            "ref_sample.wav",
            "ref_sample.txt",
            "validation_neutral.wav",
            "validation_expressive.wav",
            "speech_tokenizer/model.safetensors",
        ):
            path = model / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        export_fingerprint = "e" * 64
        propagation = build_instruction_propagation_contract(
            mode="per_record",
            samples=[
                {
                    "source_index": 0,
                    "instruction": "Calm, measured narration.",
                }
            ],
        )
        (model / "mlx_export_manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "artifact_format": "merged_mlx_qwen_checkpoint",
                    "status": "validated_experimental",
                    "technical_validation_passed": True,
                    "production_assignment_supported": False,
                    "instruction_propagation": propagation,
                    "export_fingerprint": export_fingerprint,
                }
            ),
            encoding="utf-8",
        )
        manifest = model.parents[1] / "manifest.json"
        manifest.write_text(
            json.dumps(
                [
                    {
                        "id": "narrator_pilot",
                        "name": "Narrator Pilot",
                        "experimental": True,
                        "technical_validation_passed": True,
                        "production_assignment_supported": False,
                        "manual_audio_review_status": "pending",
                        "instruction_propagation": propagation,
                        "export_fingerprint": export_fingerprint,
                        "base_model_revision": "a" * 40,
                        "mlx_model_path": (
                            "lora_models/narrator_pilot/mlx_model"
                        ),
                        "neutral_rtf": 0.6,
                        "expressive_rtf": 0.4,
                        "speaker_cosine_floor": 0.98,
                    }
                ]
            ),
            encoding="utf-8",
        )
        return model

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_latest_evidence_loads_and_validates_outcome(self) -> None:
        self.assertEqual(
            latest_phase22_evidence_path(self.root),
            self.evidence_path,
        )
        self.assertEqual(
            load_phase22_evidence(self.root),
            self.evidence,
        )

    def test_controlled_clone_evidence_loads_by_contract(self) -> None:
        self.assertEqual(
            latest_controlled_clone_evidence_path(self.root),
            self.controlled_path,
        )
        self.assertEqual(
            load_controlled_clone_evidence(self.root),
            self.controlled_evidence,
        )

    def test_lora_sidecar_evidence_loads_by_contract(self) -> None:
        self.assertEqual(
            latest_lora_sidecar_evidence_path(self.root),
            self.sidecar_path,
        )
        self.assertEqual(
            load_lora_sidecar_evidence(self.root),
            self.sidecar_evidence,
        )

    def test_invalid_lora_sidecar_evidence_is_rejected(self) -> None:
        self.sidecar_evidence["production_assignment_supported"] = True
        self.sidecar_path.write_text(
            json.dumps(self.sidecar_evidence),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            VoiceBackendCapabilityError,
            "production_assignment_supported",
        ):
            load_lora_sidecar_evidence(self.root)

    def test_invalid_controlled_clone_evidence_is_rejected(self) -> None:
        self.controlled_evidence["model"] = "unverified-model"
        self.controlled_path.write_text(
            json.dumps(self.controlled_evidence),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            VoiceBackendCapabilityError,
            "unsupported backend or model",
        ):
            load_controlled_clone_evidence(self.root)

    def test_invalid_evidence_outcome_is_rejected(self) -> None:
        self.evidence["stable_lora_outcome"] = "maybe"
        self.evidence_path.write_text(
            json.dumps(self.evidence),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            VoiceBackendCapabilityError,
            "unsupported LoRA outcome",
        ):
            load_phase22_evidence(self.root)

    def test_capability_status_is_model_free_and_exposes_blockers(self) -> None:
        versions = {
            "transformers": "5.12.1",
            "qwen-tts": "0.1.1",
            "mlx-audio": "0.4.5",
        }
        with (
            patch(
                "voice_backend_capabilities._package_version",
                side_effect=lambda name: versions.get(name),
            ),
            patch(
                "voice_backend_capabilities._requirement_for"
            ) as requirement_for,
            patch(
                "voice_backend_capabilities._requirement_satisfied",
                side_effect=[False, True],
            ),
            patch(
                "voice_backend_capabilities.shutil.which",
                return_value=None,
            ),
            patch(
                "voice_backend_capabilities.model_cache_status",
                side_effect=lambda name: {
                    "cached": name != "pytorch_qwen_base"
                },
            ),
        ):
            from packaging.requirements import Requirement

            requirement_for.side_effect = [
                Requirement("transformers==4.57.3"),
                Requirement("transformers>=5.5.0,<5.13.0"),
            ]
            status = build_voice_backend_capabilities(
                root_dir=self.root,
            )
        self.assertEqual(status["stable_lora_outcome"], "unsupported")
        self.assertFalse(status["lora_training_supported"])
        self.assertFalse(status["lora_inference_supported"])
        self.assertFalse(status["training_action_enabled"])
        self.assertTrue(
            status["environment"]["transformers_requirement_conflict"]
        )
        self.assertFalse(status["environment"]["sox_available"])
        self.assertFalse(
            status["environment"]["pytorch_base_model_cached"]
        )
        self.assertTrue(
            status["environment"]["mlx_models_cached"]["voice_design"]
        )
        self.assertEqual(
            status["measured_inference"]["voice_design"]["warm_rtf"],
            0.3,
        )
        controlled = status["expressive_clone"]
        self.assertTrue(controlled["supported"])
        self.assertEqual(
            controlled["backend"],
            "qwen3_instruction_controlled",
        )
        self.assertTrue(controlled["uses_supplied_reference_identity"])
        self.assertTrue(controlled["per_line_instruction_supported"])
        self.assertFalse(controlled["production_default"])
        self.assertTrue(controlled["experimental_preview_available"])
        self.assertEqual(controlled["status"], "approved")
        self.assertEqual(
            controlled["measurements"]["expressive"]["real_time_factor"],
            0.78,
        )
        windows = status["synthesis_windows"]
        self.assertEqual(windows["schema_version"], 1)
        self.assertEqual(
            windows["catalog"]["qwen3_custom"]["seam_mode"],
            "silence_gap",
        )
        self.assertEqual(
            windows["catalog"]["voxcpm2_controlled"]["seam_mode"],
            "discard_overlap",
        )
        self.assertEqual(
            windows["catalog"]["external_generic"]["family"],
            "external",
        )
        sidecar = status["experimental_lora_sidecar"]
        self.assertTrue(sidecar["available"])
        self.assertTrue(sidecar["training_supported"])
        self.assertEqual(sidecar["training_device"], "mps")
        self.assertEqual(sidecar["measured_step_seconds"], 1.5)
        self.assertEqual(
            sidecar["measured_mps_current_allocated_gib"],
            7.31,
        )
        self.assertFalse(sidecar["direct_pytorch_inference_performant"])
        self.assertEqual(sidecar["direct_pytorch_inference_rtf"], 7.18)
        self.assertTrue(
            sidecar["merged_mlx_inference_technically_validated"]
        )
        self.assertTrue(sidecar["per_line_instruction_supported"])
        self.assertFalse(sidecar["production_assignment_supported"])
        self.assertEqual(sidecar["manual_audio_review_status"], "pending")
        joined = " ".join(status["blockers"])
        self.assertIn("incompatible Transformers", joined)
        self.assertIn("SoX", joined)
        self.assertIn("dynamic adapter-loading", joined)

    def test_installed_mlx_artifact_rejects_instruction_propagation_mismatch(self) -> None:
        model = self.install_mlx_artifact_fixture()
        inner_path = model / "mlx_export_manifest.json"
        inner = json.loads(inner_path.read_text(encoding="utf-8"))
        inner["instruction_propagation"] = (
            build_instruction_propagation_contract(
                mode="identity_only",
                samples=[],
            )
        )
        inner_path.write_text(json.dumps(inner), encoding="utf-8")
        self.assertEqual(installed_mlx_lora_artifacts(self.root), [])

    def test_validated_installed_mlx_artifact_enables_experimental_inference(self) -> None:
        model = self.install_mlx_artifact_fixture()
        installed = installed_mlx_lora_artifacts(self.root)
        self.assertEqual(len(installed), 1)
        self.assertEqual(installed[0]["id"], "narrator_pilot")
        self.assertEqual(installed[0]["mlx_model_path"], model.relative_to(self.root).as_posix())
        self.assertEqual(installed[0]["instruction_mode"], "per_record")
        self.assertTrue(installed[0]["instruction_required_at_inference"])

        versions = {
            "transformers": "5.12.1",
            "qwen-tts": "0.1.1",
            "mlx-audio": "0.4.5",
        }
        with (
            patch(
                "voice_backend_capabilities._package_version",
                side_effect=lambda name: versions.get(name),
            ),
            patch(
                "voice_backend_capabilities._requirement_for",
                return_value=None,
            ),
            patch(
                "voice_backend_capabilities.shutil.which",
                return_value=None,
            ),
            patch(
                "voice_backend_capabilities.model_cache_status",
                return_value={"cached": True},
            ),
        ):
            status = build_voice_backend_capabilities(root_dir=self.root)
        self.assertEqual(status["stable_lora_outcome"], "unsupported")
        self.assertFalse(status["lora_training_supported"])
        self.assertTrue(status["lora_inference_supported"])
        self.assertFalse(status["training_action_enabled"])
        self.assertIn("standalone MLX LoRA inference", status["reason"])
        sidecar = status["experimental_lora_sidecar"]
        self.assertTrue(sidecar["inference_supported"])
        self.assertEqual(sidecar["installed_artifact_count"], 1)
        self.assertEqual(sidecar["installed_artifacts"][0]["id"], "narrator_pilot")
        self.assertFalse(sidecar["production_assignment_supported"])

    def test_require_training_supported_fails_closed(self) -> None:
        with patch(
            "voice_backend_capabilities.build_voice_backend_capabilities",
            return_value={
                "lora_training_supported": False,
                "reason": "Unsupported.",
                "blockers": ["Conflict."],
            },
        ):
            with self.assertRaisesRegex(
                VoiceBackendCapabilityError,
                "Unsupported.*Conflict",
            ):
                require_lora_training_supported(root_dir=self.root)

    def test_missing_evidence_still_fails_closed(self) -> None:
        self.evidence_path.unlink()
        self.controlled_path.unlink()
        self.sidecar_path.unlink()
        with (
            patch(
                "voice_backend_capabilities._package_version",
                return_value=None,
            ),
            patch(
                "voice_backend_capabilities._requirement_for",
                return_value=None,
            ),
            patch(
                "voice_backend_capabilities.shutil.which",
                return_value=None,
            ),
            patch(
                "voice_backend_capabilities.model_cache_status",
                return_value={"cached": False},
            ),
        ):
            status = build_voice_backend_capabilities(
                root_dir=self.root,
            )
        self.assertEqual(status["stable_lora_outcome"], "unsupported")
        self.assertEqual(status["measured_inference"], {})
        self.assertIsNone(status["evidence_path"])
        self.assertFalse(status["expressive_clone"]["supported"])
        self.assertIsNone(status["expressive_clone"]["evidence_path"])
        self.assertFalse(status["experimental_lora_sidecar"]["available"])
        self.assertFalse(
            status["experimental_lora_sidecar"]["training_supported"]
        )
        self.assertIsNone(
            status["experimental_lora_sidecar"]["evidence_path"]
        )


if __name__ == "__main__":
    unittest.main()
