from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from instruction_propagation import (
    INSTRUCTION_PLACEMENT,
    build_instruction_propagation_contract,
    instruction_identity,
)
from training_sidecar.mlx_export import MLXExportError, export_mlx_checkpoint
from training_sidecar.qwen_training import sha256_file


class InstructionPropagationArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.merged = self.root / "merged"
        self.merged.mkdir()
        (self.merged / "ref_sample.wav").write_bytes(b"reference")
        (self.merged / "ref_sample.txt").write_text(
            "Exact supplied transcript.",
            encoding="utf-8",
        )
        self.propagation = build_instruction_propagation_contract(
            mode="per_record",
            samples=[
                {
                    "source_index": 0,
                    "instruction": "Calm, measured narration.",
                },
                {
                    "source_index": 1,
                    "instruction": "Urgent but controlled warning.",
                },
            ],
        )
        (self.merged / "merge_metrics.json").write_text(
            json.dumps(
                {
                    "base_model": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
                    "base_model_revision": "a" * 40,
                    "adapter_manifest_sha256": "b" * 64,
                    "instruction_propagation": self.propagation,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def fake_convert(*, merged, output, q_group_size, q_bits):
        (output / "speech_tokenizer").mkdir(parents=True)
        (output / "model.safetensors").write_bytes(b"mlx-model")
        (output / "config.json").write_text("{}", encoding="utf-8")
        (output / "speech_tokenizer" / "model.safetensors").write_bytes(
            b"tokenizer"
        )
        return {
            "conversion_seconds": 0.01,
            "q_group_size": q_group_size,
            "q_bits": q_bits,
            "removed_copied_pytorch_files": [],
        }

    @staticmethod
    def fake_validation(
        *,
        output,
        reference_audio,
        reference_text,
        validation_text,
        neutral_instruction,
        expressive_instruction,
        max_tokens,
    ):
        rows = {}
        for style, instruction in (
            ("neutral", neutral_instruction),
            ("expressive", expressive_instruction),
        ):
            target = output / f"validation_{style}.wav"
            target.write_bytes(style.encode("utf-8"))
            rows[style] = {
                **instruction_identity(instruction),
                "instruction_placement": INSTRUCTION_PLACEMENT,
                "audio_sha256": sha256_file(target),
                "elapsed_seconds": 0.1,
                "audio_duration_seconds": 1.0,
                "real_time_factor": 0.1,
                "speaker_cosine_to_reference": 0.99,
            }
        return {
            "validation_text_sha256": "c" * 64,
            "measurements": rows,
            "outputs_differ": True,
            "speaker_similarity_floor": 0.95,
            "identity_passed": True,
            "steady_state_faster_than_real_time": True,
            "instruction_channel_changed_output": True,
            "manual_audio_review_required": True,
            "manual_audio_review_status": "pending",
        }

    def test_merge_to_mlx_manifest_preserves_exact_propagation_contract(self) -> None:
        output = self.root / "mlx"
        with (
            patch(
                "training_sidecar.mlx_export._convert_checkpoint",
                side_effect=self.fake_convert,
            ),
            patch(
                "training_sidecar.mlx_export._generate_validation",
                side_effect=self.fake_validation,
            ),
        ):
            result = export_mlx_checkpoint(
                merged_dir=self.merged,
                output_dir=output,
                validation_text="The corridor was silent.",
                neutral_instruction="Calm, measured narration.",
                expressive_instruction="Urgent but controlled warning.",
                max_tokens=600,
            )
        self.assertEqual(result["status"], "validated_experimental")
        manifest = json.loads(
            (output / "mlx_export_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            manifest["instruction_propagation"],
            self.propagation,
        )
        self.assertEqual(
            manifest["instruction_propagation"]["propagation_fingerprint"],
            self.propagation["propagation_fingerprint"],
        )
        for row in manifest["validation"]["measurements"].values():
            self.assertEqual(
                row["instruction_placement"],
                INSTRUCTION_PLACEMENT,
            )
            self.assertEqual(len(row["formatted_instruction_sha256"]), 64)
        self.assertFalse(manifest["production_assignment_supported"])

    def test_tampered_merge_propagation_fails_before_output_publication(self) -> None:
        metrics_path = self.merged / "merge_metrics.json"
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        metrics["instruction_propagation"]["records"][0][
            "instruction_sha256"
        ] = "0" * 64
        metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
        output = self.root / "mlx"
        with self.assertRaisesRegex(
            MLXExportError,
            "instruction propagation is invalid",
        ):
            export_mlx_checkpoint(
                merged_dir=self.merged,
                output_dir=output,
                validation_text="The corridor was silent.",
                neutral_instruction="Calm, measured narration.",
                expressive_instruction="Urgent but controlled warning.",
                max_tokens=600,
            )
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
